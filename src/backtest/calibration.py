"""TV6 C6.04: calibrate the assumed half spread of the close-only proxy from the live D2 sample.

assumed_half_spread_bps(venue) = statistic(spread_bps) / 2 over that venue's live quotes, with
spread_bps = (ask - bid) / mid * 10_000 as in D2. Half a quoted spread is the cost of crossing from
mid to the touch on one leg, so the close proxy adds it exactly once per leg (DATA_CONTRACT 2.5).
"""
from __future__ import annotations

import pandas as pd

from src.utils.schema import data_kind, validate_frame

STATISTICS = ("median", "mean")
CALIBRATION_COLUMNS = ["venue", "n_quotes", "first_ts_recv_utc", "last_ts_recv_utc", "spread_bps_statistic",
                       "half_spread_bps", "statistic", "source_data_kind"]


def estimate_half_spread_bps(quotes: pd.DataFrame, venues=None, statistic: str = "median") -> pd.DataFrame:
    """Per-venue half spread in bps one-way from canonical D2 quotes (pure function).

    quotes: D2 long table (validated here); its spread_bps is used as recorded, never refilled.
    venues: venue IDs to return; every requested venue must have quotes, otherwise KeyError
      (a missing venue is never given 0 bps).
    Units: bps one-way per leg. Time: a parameter estimate from the live window; it is not a
      price observation and is applied unchanged to every historical minute (proxy assumption).
    """
    if statistic not in STATISTICS:
        raise ValueError(f"statistic must be one of {STATISTICS}, got {statistic!r}")
    validate_frame(quotes, "D2")
    kind = data_kind(quotes)
    venue = quotes["exchange"] + "_" + quotes["market_type"]
    frame = quotes.assign(venue=venue)
    requested = list(dict.fromkeys(venue.unique() if venues is None else venues))
    missing = sorted(set(requested) - set(frame["venue"]))
    if missing:
        raise KeyError(f"No live quotes to calibrate half spread for {missing}")
    rows = []
    for name in requested:
        part = frame[frame["venue"] == name]
        value = float(getattr(part["spread_bps"], statistic)())
        rows.append({"venue": name, "n_quotes": int(len(part)), "first_ts_recv_utc": part["ts_recv_utc"].min(),
                     "last_ts_recv_utc": part["ts_recv_utc"].max(), "spread_bps_statistic": value,
                     "half_spread_bps": value / 2.0, "statistic": statistic, "source_data_kind": kind})
    return pd.DataFrame(rows, columns=CALIBRATION_COLUMNS)


def half_spread_map(calibration: pd.DataFrame) -> dict:
    """{venue: half_spread_bps} from estimate_half_spread_bps output."""
    return dict(zip(calibration["venue"], calibration["half_spread_bps"].astype(float)))
