"""Executable DATA_CONTRACT v1.0, owned by TV4; errors stop a stage."""
from __future__ import annotations

import re
import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_numeric_dtype
from .time_utils import require_utc, utc_timestamp

CONTRACT_VERSION = "1.0"
SCHEMAS = {
    "D1": "ts_available exchange market_type symbol open high low close volume_base volume_quote data_kind".split(),
    "D2": "ts_recv_utc ts_event_utc exchange market_type symbol bid ask bid_size ask_size mid spread_abs spread_bps depth_bid_10bps_usdt depth_ask_10bps_usdt data_kind".split(),
    "D5": "ts_available flag_high_vol flag_weekend flag_event regime_bull_bear".split(),
    "D6": "window_start window_end available_at venue is_lower is_upper is_mid component_share ils leader status reason".split(),
    "D7": "event_id ts_start ts_end duration_s market_type buy_exchange sell_exchange gross_bps net_bps regime".split(),
    "D8": "signal_id ts_decision ts_fill market_type buy_exchange sell_exchange qty_btc buy_px sell_px fees_usdt pnl_usdt status reject_reason".split(),
    "D9": "ts_available exchange market_type symbol funding_rate funding_interval_h open_interest_base open_interest_usdt data_kind".split(),
}
TIME_COLUMNS = {"ts_available", "ts_recv_utc", "ts_event_utc", "window_start", "window_end", "available_at", "ts_start", "ts_end", "ts_decision", "ts_fill"}
NUMBER_COLUMNS = set("open high low close volume_base volume_quote bid ask bid_size ask_size mid spread_abs spread_bps depth_bid_10bps_usdt depth_ask_10bps_usdt is_lower is_upper is_mid component_share ils duration_s gross_bps net_bps qty_btc buy_px sell_px fees_usdt pnl_usdt funding_rate funding_interval_h open_interest_base open_interest_usdt".split())
TEXT_COLUMNS = set("exchange market_type symbol data_kind venue leader status reason event_id signal_id buy_exchange sell_exchange regime regime_bull_bear reject_reason".split())
VENUE_PATTERN = re.compile(r"^[a-z][a-z0-9]*_(spot|perp)$")


def data_kind(df: pd.DataFrame) -> str:
    """Return exactly one provenance label, from a long column or wide metadata."""
    if "data_kind" in df:
        kinds = df["data_kind"].dropna().unique().tolist()
        if df["data_kind"].isna().any() or len(kinds) != 1:
            raise ValueError("data_kind: mixed or missing provenance")
        kind = kinds[0]
    else:
        kind = df.attrs.get("data_kind")
    if kind not in {"synthetic", "historical", "live"}:
        raise ValueError(f"data_kind: unsupported or absent label {kind!r}")
    return str(kind)


def venues_from_panel(df: pd.DataFrame) -> list[str]:
    """Read venue suffixes of close columns, preserving column order."""
    return [str(c).split("__", 1)[1] for c in df if str(c).startswith("close__")]


def validate_split_manifest(manifest: dict) -> dict:
    """Validate D4 JSON types, disjoint UTC boundaries and fingerprint."""
    keys = {f"{part}_{edge}" for part in ("dev", "val", "test") for edge in ("start", "end")} | {"fingerprint"}
    if not isinstance(manifest, dict) or keys-set(manifest):
        raise ValueError("D4: missing split boundaries or fingerprint")
    if not isinstance(manifest["fingerprint"], str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["fingerprint"]):
        raise ValueError("D4: fingerprint must be a SHA-256 hex digest")
    times = [utc_timestamp(manifest[f"{part}_{edge}"]) for part in ("dev", "val", "test") for edge in ("start", "end")]
    if any(times[i] > times[i+1] for i in (0, 2, 4)) or not (times[1] < times[2] and times[3] < times[4]):
        raise ValueError("D4: split boundaries overlap or are reversed")
    if "test_consumed" in manifest and not isinstance(manifest["test_consumed"], bool):
        raise ValueError("D4: test_consumed must be boolean")
    return manifest


def _numeric(df, columns):
    for column in columns:
        if not is_numeric_dtype(df[column]) or is_bool_dtype(df[column]):
            raise ValueError(f"{column}: expected numeric dtype, got {df[column].dtype}")
        if np.isinf(df[column].to_numpy(dtype=float, na_value=np.nan)).any():
            raise ValueError(f"{column}: infinite value")


def validate_frame(df: pd.DataFrame, schema: str) -> pd.DataFrame:
    """Validate names, types, UTC, source labels and core invariants; return df."""
    if df.empty:
        raise ValueError(f"{schema}: empty data")
    if not df.columns.is_unique:
        raise ValueError(f"{schema}: duplicate columns")
    if schema in {"D3", "D3b"}:
        _validate_wide(df, schema)
        return df
    if schema not in SCHEMAS:
        raise ValueError(f"Unknown schema {schema}")
    missing = set(SCHEMAS[schema]) - set(df.columns)
    if missing:
        raise ValueError(f"{schema}: missing columns {sorted(missing)}")
    for column in TIME_COLUMNS & set(df.columns):
        require_utc(df[column], column, nullable=column in {"ts_event_utc", "ts_fill"})
    _numeric(df, NUMBER_COLUMNS & set(df.columns))
    for column in TEXT_COLUMNS & set(df.columns):
        nonnull = df[column].dropna()
        if not nonnull.map(lambda value: isinstance(value, str)).all():
            raise ValueError(f"{column}: expected strings")
    for column in ("exchange", "market_type", "symbol"):
        if column in df and df[column].isna().any():
            raise ValueError(f"{column}: missing identifier")
    if "exchange" in df and not df.exchange.str.fullmatch(r"[a-z][a-z0-9]*").all():
        raise ValueError("exchange: expected lowercase CCXT-style ID")
    if "market_type" in df and not df.market_type.isin(["spot", "perp"]).all():
        raise ValueError("market_type must be spot or perp")
    if "symbol" in df and not df.symbol.eq("BTC/USDT").all():
        raise ValueError("symbol must be canonical BTC/USDT; CCXT symbols belong in config")
    if "data_kind" in df:
        data_kind(df)
    if schema == "D1":
        keys = ["ts_available", "exchange", "market_type", "symbol"]
        if df.duplicated(keys).any():
            raise ValueError("D1: duplicate availability/venue/symbol")
        prices = df[["open", "high", "low", "close"]]
        if prices.isna().any().any() or (prices <= 0).any().any():
            raise ValueError("D1: OHLC prices must be present and positive")
        if ((df.high < df[["open", "low", "close"]].max(axis=1)) | (df.low > df[["open", "high", "close"]].min(axis=1))).any():
            raise ValueError("D1: invalid OHLC bounds")
        if (df[["volume_base", "volume_quote"]] < 0).any().any():
            raise ValueError("D1: negative volume")
    if schema == "D2":
        if df[["bid", "ask"]].isna().any().any() or (df.bid <= 0).any() or (df.ask < df.bid).any():
            raise ValueError("D2: invalid/crossed book")
        expected_mid = (df.bid + df.ask) / 2
        for column, expected in {"mid": expected_mid, "spread_abs": df.ask-df.bid, "spread_bps": (df.ask-df.bid)/expected_mid*1e4}.items():
            if not np.allclose(df[column], expected, rtol=1e-9, atol=1e-8):
                raise ValueError(f"D2: inconsistent {column}")
        if (df[["bid_size", "ask_size", "depth_bid_10bps_usdt", "depth_ask_10bps_usdt"]] < 0).any().any():
            raise ValueError("D2: negative size/depth")
        if "depth_censored" in df and (not is_bool_dtype(df.depth_censored) or df.depth_censored.isna().any()):
            raise ValueError("D2: depth_censored must be boolean")
    if schema == "D5":
        for column in ("flag_high_vol", "flag_weekend", "flag_event"):
            if not is_bool_dtype(df[column]) or df[column].isna().any():
                raise ValueError(f"D5: {column} must be boolean")
    if schema == "D6":
        if (df.window_start > df.window_end).any() or (df.available_at < df.window_end).any():
            raise ValueError("D6: rolling result available before its window ends")
        ok = df[df.status.eq("ok") | df.status.eq("OK")]
        shares = ok[["is_lower", "is_upper", "is_mid", "ils"]]
        if shares.isna().any().any() or ((shares < -1e-8) | (shares > 1+1e-8)).any().any():
            raise ValueError("D6: invalid successful shares")
        # C5.04 explicitly keeps signed Gonzalo-Granger CS estimates; only
        # IS and ILS are bounded shares. Never clip or reject a finite CS.
        if ok.component_share.isna().any():
            raise ValueError("D6: missing successful component_share")
        if (ok.is_lower > ok.is_upper).any() or (ok.is_mid < ok.is_lower).any() or (ok.is_mid > ok.is_upper).any():
            raise ValueError("D6: inconsistent IS bounds")
        for _, group in ok.groupby(["window_start", "window_end"]):
            for row in group.itertuples():
                if pd.notna(row.leader) and row.leader != "":
                    leader = group[group.venue.eq(row.leader)]
                    others = group[~group.venue.eq(row.leader)]
                    if len(leader) != 1 or others.empty or not (leader.is_lower.iloc[0] > others.is_upper.max()):
                        raise ValueError("D6: leader has overlapping bounds")
    if schema == "D7":
        if (df.ts_end < df.ts_start).any() or (df.duration_s < 0).any():
            raise ValueError("D7: invalid duration")
    if schema == "D8" and (df.ts_fill.dropna() < df.loc[df.ts_fill.notna(), "ts_decision"]).any():
        raise ValueError("D8: fill before decision")
    if schema == "D9":
        if not df.market_type.eq("perp").all() or (df.funding_interval_h <= 0).any():
            raise ValueError("D9: perpetual funding requires positive interval in hours")
    return df


def _validate_wide(df, schema):
    require_utc(df.index, "ts_available index")
    if df.index.name != "ts_available" or not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError(f"{schema}: index must be unique, sorted and named ts_available")
    data_kind(df)
    prefix = "close__" if schema == "D3" else "ret_1m__"
    venues = [str(c)[len(prefix):] for c in df if str(c).startswith(prefix)]
    if not venues or any(not VENUE_PATTERN.fullmatch(v) for v in venues):
        raise ValueError(f"{schema}: invalid or missing venue columns")
    metrics = ["close", "log_close", "volume_base", "missing"] if schema == "D3" else ["ret_1m", "vol_share", "rv_60m"]
    required = {f"{metric}__{v}" for v in venues for metric in metrics}
    if schema == "D3b":
        required |= {"dispersion_bps_spot", "dispersion_bps_perp"}
    if required-set(df.columns):
        raise ValueError(f"{schema}: missing columns {sorted(required-set(df.columns))}")
    _numeric(df, [c for c in required if not c.startswith("missing__")])
    if schema == "D3":
        for venue in venues:
            close, log_close, volume, missing = [df[f"{m}__{venue}"] for m in metrics]
            if not is_bool_dtype(missing) or missing.isna().any():
                raise ValueError(f"missing__{venue}: expected nonnullable boolean")
            if not missing.eq(close.isna()).all() or log_close[missing].notna().any() or volume[missing].notna().any():
                raise ValueError(f"{venue}: missing flag and values disagree")
            if (close.dropna() <= 0).any() or (volume.dropna() < 0).any():
                raise ValueError(f"{venue}: invalid close/volume")
            if not np.allclose(log_close[~missing], np.log(close[~missing]), rtol=1e-10, atol=1e-10):
                raise ValueError(f"{venue}: log_close disagrees with close")
