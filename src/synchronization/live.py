"""TV4: causal D2 quote alignment for live cost-model handoffs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.utils.schema import CONTRACT_VERSION, data_kind, validate_frame

QUOTE_METRICS = (
    "bid", "ask", "bid_size", "ask_size", "mid", "spread_abs", "spread_bps",
    "depth_bid_10bps_usdt", "depth_ask_10bps_usdt",
)


def align_live_quotes(df_long, *, grid_freq, max_age) -> pd.DataFrame:
    """Align canonical D2 quotes using receive time, never event/grid time.

    grid_freq is a positive fixed duration; max_age is an explicit nonnegative
    duration. Prices are USDT, sizes BTC, depth USDT, age seconds. At grid g,
    choose the latest quote of that venue with ts_recv_utc <= g; equality at
    max_age remains valid. Mask ALL quote values when absent or stale, without
    interpolation. Reject duplicate receive-time/venue/symbol keys.

    Grid spans floor(min receive) through floor(max receive). It never invents
    future grid points after the newest received quote. source_ts_recv_utc,
    source_ts_event_utc and quote_age_s retain the last past source even when
    stale, for audit; missing__ tells consumers whether its prices are usable.
    This intermediate wide quote frame is not the OHLCV D3 schema.
    """
    validate_frame(df_long, "D2")
    kind = data_kind(df_long)
    if "data_kind" in df_long.attrs and df_long.attrs["data_kind"] != kind:
        raise ValueError("data_kind: column and metadata disagree")
    keys = ["ts_recv_utc", "exchange", "market_type", "symbol"]
    if df_long.duplicated(keys).any():
        raise ValueError("D2: duplicate receive-time/venue/symbol")
    try:
        offset = pd.tseries.frequencies.to_offset(grid_freq)
        step = pd.Timedelta(offset.nanos, unit="ns")
    except (TypeError, ValueError) as exc:
        raise ValueError("grid_freq must be positive and fixed") from exc
    if step <= pd.Timedelta(0):
        raise ValueError("grid_freq must be positive and fixed")
    try:
        age_limit = pd.Timedelta(max_age)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_age must be a nonnegative duration") from exc
    if pd.isna(age_limit) or age_limit < pd.Timedelta(0):
        raise ValueError("max_age must be a nonnegative duration")

    ordered = df_long.sort_values("ts_recv_utc", kind="stable")
    grid = pd.date_range(ordered.ts_recv_utc.min().floor(step), ordered.ts_recv_utc.max().floor(step),
                         freq=step, name="ts_available").as_unit("ns")
    panel = pd.DataFrame(index=grid)
    venues = {}
    for (exchange, market_type), group in ordered.groupby(["exchange", "market_type"], sort=True):
        venue = f"{exchange}_{market_type}"
        fields = ["ts_recv_utc", "ts_event_utc", *QUOTE_METRICS]
        flags = [f for f in ("depth_censored", "depth_truncated") if f in group]
        extras = [c for c in ("depth_cover_bps", "depth_bid_5bps_usdt", "depth_ask_5bps_usdt",
                              "depth_bid_2bps_usdt", "depth_ask_2bps_usdt") if c in group]
        fields += flags + extras
        source = group[fields].rename(columns={"ts_recv_utc": "source_recv", "ts_event_utc": "source_event"})
        aligned = pd.merge_asof(pd.DataFrame({"ts_available": grid}), source,
                                left_on="ts_available", right_on="source_recv", direction="backward",
                                allow_exact_matches=True)
        age = aligned.ts_available - aligned.source_recv
        usable = aligned.source_recv.notna() & age.le(age_limit)
        for metric in (*QUOTE_METRICS, *extras):
            panel[f"{metric}__{venue}"] = aligned[metric].where(usable).to_numpy(dtype=float, na_value=np.nan)
        panel[f"source_ts_recv_utc__{venue}"] = pd.array(aligned.source_recv, dtype="datetime64[ns, UTC]")
        panel[f"source_ts_event_utc__{venue}"] = pd.array(aligned.source_event, dtype="datetime64[ns, UTC]")
        panel[f"quote_age_s__{venue}"] = age.dt.total_seconds().to_numpy(dtype=float)
        panel[f"missing__{venue}"] = ~usable.to_numpy(dtype=bool)
        for flag in flags:
            panel[f"{flag}__{venue}"] = pd.array(aligned[flag].where(usable), dtype="boolean")
        venues[venue] = {"exchange": exchange, "market_type": market_type}
    panel.attrs = {"contract_version": CONTRACT_VERSION, "data_kind": kind, "symbol": "BTC/USDT",
                   "venues": venues, "grid_freq": str(step), "max_age": str(age_limit),
                   "alignment": "previous_tick_receive_time", "frame_type": "aligned_quotes"}
    return panel
