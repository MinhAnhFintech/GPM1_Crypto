"""TV6 C6.03: group consecutive above-threshold gaps into events and measure how long they last (RQ4).

Event definition, applied to each directed pair (market_type, buy_exchange, sell_exchange)
independently - the two directions of one venue pair are separate series:
  * an observation is "above" when gross_gap_bps >= threshold_bps (NaN is never above);
  * consecutive above observations exactly grid_step_s apart form one event;
  * an event ends at a below-threshold observation, a NaN gap, or a time hole > grid_step_s
    (missing rows are never bridged).
duration_s = (ts_end - ts_start) + grid_step_s: each observation stands for one grid step, so a
single-observation event lasts grid_step_s. censored_end / censored_start flag events touching
the last / first observation of that pair, whose true duration is longer than measured.
Gross gap decides the event (RQ4 asks how long gaps persist); net gap only records profitability.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from src.utils.io_utils import write_parquet
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind as frame_data_kind, validate_frame
from src.utils.time_utils import require_utc

ROOT = Path(__file__).resolve().parents[2]
DATA_KINDS = ("synthetic", "historical", "live")
PAIR_KEYS = ["market_type", "buy_exchange", "sell_exchange"]
INPUT_COLUMNS = ["ts_available", *PAIR_KEYS, "gross_gap_bps", "net_gap_bps"]
EVENT_COLUMNS = ["event_id", "buy_exchange", "sell_exchange", "market_type", "ts_start", "ts_end", "n_obs",
                 "duration_s", "gross_bps", "net_bps", "max_gross_gap_bps", "mean_gross_gap_bps",
                 "max_net_gap_bps", "any_net_positive", "censored_start", "censored_end", "regime", "data_kind"]
FLOAT_COLUMNS = ["duration_s", "gross_bps", "net_bps", "max_gross_gap_bps", "mean_gross_gap_bps", "max_net_gap_bps"]
DEFAULT_GAP_EVENTS_PATH = "outputs/tables/gap_events.parquet"


def _resolve_data_kind(gaps_df: pd.DataFrame, data_kind) -> str:
    kinds = set()
    if "data_kind" in gaps_df.columns:
        if gaps_df["data_kind"].isna().any():
            raise ValueError("data_kind: missing provenance in gap rows")
        kinds |= set(gaps_df["data_kind"].unique())
    elif gaps_df.attrs.get("data_kind"):
        kinds.add(gaps_df.attrs["data_kind"])
    if data_kind is not None:
        kinds.add(data_kind)
    if len(kinds) != 1 or next(iter(kinds)) not in DATA_KINDS:
        raise ValueError(f"data_kind must be exactly one of {DATA_KINDS}; got {sorted(map(str, kinds)) or 'none'}")
    return next(iter(kinds))


def _check_same_market(gaps_df: pd.DataFrame) -> None:
    if not gaps_df["market_type"].isin(["spot", "perp"]).all():
        raise ValueError("market_type must be spot or perp")
    for column in ("buy_venue", "sell_venue"):
        if column in gaps_df.columns and not (gaps_df[column].str.rsplit("_", n=1).str[1] == gaps_df["market_type"]).all():
            raise ValueError(f"{column}: spot-perp pair or market_type mismatch; only same-market_type pairs are allowed")


def _empty_events(kind: str) -> pd.DataFrame:
    dtypes = {c: "float64" for c in FLOAT_COLUMNS} | {"ts_start": "datetime64[ns, UTC]", "ts_end": "datetime64[ns, UTC]",
              "n_obs": "int64", "any_net_positive": "bool", "censored_start": "bool", "censored_end": "bool"}
    events = pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, object)) for c in EVENT_COLUMNS})
    events.attrs["data_kind"] = kind
    return events


def detect_gap_events(gaps_df: pd.DataFrame, threshold_bps: float, grid_step_s: float, data_kind=None) -> pd.DataFrame:
    """Gap events per directed pair from a compute_gaps table (pure function, input unchanged).

    gaps_df: long table with ts_available (datetime64[ns, UTC]), market_type, buy_exchange,
      sell_exchange, gross_gap_bps, net_gap_bps (bps one-way), and data_kind as a column
      or attrs. One row per pair and ts_available; rows finer than grid_step_s are rejected.
    threshold_bps: gross gap threshold in bps (inclusive). grid_step_s: grid spacing in seconds.
    data_kind: optional label; must agree with the input's single label, mixing raises.
    Time: an event is known only up to its ts_end; gross_bps/net_bps are the gaps at ts_start,
      i.e. what was observable when the event was first detected.
    Returns EVENT_COLUMNS sorted by ts_start; regime is None until D5 regime flags are joined.
    """
    missing = set(INPUT_COLUMNS) - set(gaps_df.columns)
    if missing:
        raise KeyError(f"gaps_df lacks columns {sorted(missing)}")
    if not math.isfinite(threshold_bps):
        raise ValueError("threshold_bps must be finite")
    if not grid_step_s > 0:
        raise ValueError("grid_step_s must be positive (seconds)")
    kind = _resolve_data_kind(gaps_df, data_kind)
    require_utc(gaps_df["ts_available"], "ts_available")
    _check_same_market(gaps_df)
    step = pd.Timedelta(seconds=grid_step_s)

    df = gaps_df[INPUT_COLUMNS].sort_values([*PAIR_KEYS, "ts_available"], kind="stable").reset_index(drop=True)
    if df.duplicated([*PAIR_KEYS, "ts_available"]).any():
        raise ValueError("gaps_df: duplicate ts_available within a pair")
    same_pair = df[PAIR_KEYS].eq(df[PAIR_KEYS].shift()).all(axis=1)
    elapsed = df["ts_available"].diff()
    if (same_pair & elapsed.lt(step)).any():
        raise ValueError(f"gaps_df: observations closer than grid_step_s={grid_step_s}")
    above = df["gross_gap_bps"].ge(threshold_bps)
    continues = same_pair & elapsed.eq(step) & above.shift(fill_value=False)
    df["event_seq"] = (above & ~continues).cumsum()
    pair_bounds = df.groupby(PAIR_KEYS, sort=False)["ts_available"]
    df["pair_first"], df["pair_last"] = pair_bounds.transform("min"), pair_bounds.transform("max")
    df["net_positive"] = df["net_gap_bps"].gt(0)

    hits = df[above]
    if hits.empty:
        return _empty_events(kind)
    events = hits.groupby("event_seq", sort=False).agg(
        market_type=("market_type", "first"), buy_exchange=("buy_exchange", "first"), sell_exchange=("sell_exchange", "first"),
        ts_start=("ts_available", "first"), ts_end=("ts_available", "last"), n_obs=("ts_available", "size"),
        gross_bps=("gross_gap_bps", "first"), net_bps=("net_gap_bps", "first"),
        max_gross_gap_bps=("gross_gap_bps", "max"), mean_gross_gap_bps=("gross_gap_bps", "mean"),
        max_net_gap_bps=("net_gap_bps", "max"), any_net_positive=("net_positive", "any"),
        pair_first=("pair_first", "first"), pair_last=("pair_last", "first"),
    )
    events["duration_s"] = (events["ts_end"] - events["ts_start"]).dt.total_seconds() + float(grid_step_s)
    events["censored_start"] = events["ts_start"].eq(events["pair_first"])
    events["censored_end"] = events["ts_end"].eq(events["pair_last"])
    events["event_id"] = (events["market_type"] + "_" + events["buy_exchange"] + "_" + events["sell_exchange"] + "_"
                          + events["ts_start"].dt.strftime("%Y%m%dT%H%M%SZ"))
    events["regime"] = pd.Series(None, index=events.index, dtype=object)
    events["data_kind"] = kind
    events = events.sort_values(["ts_start", *PAIR_KEYS], kind="stable").reset_index(drop=True)
    events[FLOAT_COLUMNS] = events[FLOAT_COLUMNS].astype("float64")
    events["n_obs"] = events["n_obs"].astype("int64")
    for column in ("ts_start", "ts_end"):
        events[column] = events[column].astype("datetime64[ns, UTC]")
    events = events[EVENT_COLUMNS]
    events.attrs["data_kind"] = kind
    return events


def write_gap_events(df: pd.DataFrame, path=DEFAULT_GAP_EVENTS_PATH) -> None:
    """Write D7 gap events to Parquet (creates folders); exactly one data_kind is required.

    Non-empty tables are validated against D7. Synthetic events may not be written into the
    official outputs/ tables; keep them under outputs/synthetic/ (repo convention). Relative
    paths resolve against the repo root.
    """
    path = Path(path)
    path = path if path.is_absolute() else ROOT/path
    missing = set(EVENT_COLUMNS) - set(df.columns)
    if missing:
        raise KeyError(f"gap events lack columns {sorted(missing)}")
    kind = frame_data_kind(df) if not df.empty else df.attrs.get("data_kind")
    if kind not in DATA_KINDS:
        raise ValueError(f"gap events need exactly one data_kind label, got {kind!r}")
    # Shared destination rule (TV4), checked before anything is written: synthetic events never land in the research zone
    # (outputs/ except outputs/synthetic/, data/raw|interim|processed), research events never in synthetic/fixtures;
    # sub-folders, ".." and aliases are resolved first.
    validate_output_location(path, root=ROOT, mode="synthetic" if kind == "synthetic" else "historical")
    df = df.copy()
    df.attrs["data_kind"] = kind
    write_parquet(df, path, "D7" if not df.empty else None)
