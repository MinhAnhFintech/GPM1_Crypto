"""TV6 C6.09: T6b - do gaps and net gaps differ by market regime?

Compares gap events (D7) across the D5 regime flags: weekend vs weekday, high vs normal volatility, inside vs outside
a major event, bull vs bear. Because regimes occupy very different amounts of time, counts are always normalised by the
time spent in the regime:  events per 1 000 pair-minutes = n_events / (minutes in regime x directed pairs of that
contract group) x 1 000. spot-spot and perp-perp are reported apart; historical and live are never pooled.

Time safety: an event is assigned the regime flag at its ts_start (the ts_available at which it was first seen), and
D5 flags are built from trailing information only (dev-only P90 threshold, trailing 30-day mean). Only the development
split is analysed: events and exposure are restricted to the D4 development window and the run refuses events outside it.
The live sample has no D5 (its dates lie after the historical sample), so only the calendar flag (weekend, UTC) is
available there; volatility, event and trend are not defined for it and are not reported.
Descriptive only: events cluster in time (a volatile day produces many), so rate ratios are not tested for significance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DIMENSIONS = {
    "weekend": ("flag_weekend", {True: "weekend", False: "weekday"}, "weekday"),
    "volatility": ("flag_high_vol", {True: "high_vol", False: "normal_vol"}, "normal_vol"),
    "event": ("flag_event", {True: "event", False: "no_event"}, "no_event"),
    "trend": ("regime_bull_bear", {"bull": "bull", "bear": "bear"}, "bull"),
}
GROUPS = ("spot", "perp")
T6B_COLUMNS = ["source", "data_kind", "dimension", "level", "market_type", "minutes_in_level", "n_events",
               "events_per_1000_pair_minutes", "rate_ratio_vs_reference", "reference_level", "median_duration_s",
               "p90_duration_s", "share_single_obs_pct", "mean_max_gross_bps", "mean_net_at_start_bps", "n_any_net_positive",
               "n_backtest_a_signals", "backtest_a_pnl_usdt"]


def _minutes_in_level(flags: pd.DataFrame, column: str, mapping: dict) -> dict:
    """Grid minutes of the window spent in each level of one flag (one D5 row = one 1-minute grid point)."""
    counts = flags[column].value_counts(dropna=True)
    return {name: int(counts.get(key, 0)) for key, name in mapping.items()}


def _level_of(series: pd.Series, mapping: dict) -> pd.Series:
    return series.map(mapping)                     # NaN (e.g. trend warm-up) stays NaN and is excluded


def gap_by_regime(events: pd.DataFrame, flags: pd.DataFrame, window: tuple, pairs_per_group: dict,
                  ledger: pd.DataFrame | None = None, source: str = "historical_dev") -> pd.DataFrame:
    """T6b rows for one source.

    events: D7 gap events; flags: D5 (ts_available + flag columns); window: (start, end) inclusive UTC of the analysed split;
    pairs_per_group: {"spot": directed pairs, "perp": ...}; ledger: optional backtest ledger (D8) whose signals are counted
    per regime at ts_decision. Units: seconds, bps, USDT. Events are assigned the regime at ts_start.
    """
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    if len(events) and (events["ts_start"].min() < start or events["ts_end"].max() > end):
        raise ValueError("Events lie outside the analysed window; T6b analyses the development split only")
    flags = flags[(flags["ts_available"] >= start) & (flags["ts_available"] <= end)].set_index("ts_available")
    kind = events["data_kind"].iloc[0] if len(events) else events.attrs.get("data_kind", "n/a")
    rows = []
    for dimension, (column, mapping, reference) in DIMENSIONS.items():
        if column not in flags.columns:
            continue
        minutes = _minutes_in_level(flags.reset_index(), column, mapping)
        level_at_start = _level_of(flags[column].reindex(events["ts_start"]).reset_index(drop=True), mapping) if len(events) else pd.Series(dtype=object)
        ev = events.assign(level=level_at_start.to_numpy()) if len(events) else events.assign(level=pd.Series(dtype=object))
        sig = None
        if ledger is not None and len(ledger):
            sig = ledger.assign(level=_level_of(flags[column].reindex(ledger["ts_decision"]).reset_index(drop=True), mapping).to_numpy())
        for market_type in GROUPS:
            rates = {}
            for level in mapping.values():
                part = ev[(ev["market_type"] == market_type) & (ev["level"] == level)]
                exposure = minutes.get(level, 0) * pairs_per_group[market_type]
                rate = len(part)/exposure*1000 if exposure else np.nan
                rates[level] = rate
                signals = sig[(sig["market_type"] == market_type) & (sig["level"] == level) & (sig["status"] == "filled")] if sig is not None else None
                rows.append({"source": source, "data_kind": kind, "dimension": dimension, "level": level, "market_type": market_type,
                             "minutes_in_level": minutes.get(level, 0), "n_events": len(part), "events_per_1000_pair_minutes": rate,
                             "reference_level": reference,
                             "median_duration_s": float(part["duration_s"].median()) if len(part) else np.nan,
                             "p90_duration_s": float(part["duration_s"].quantile(.9)) if len(part) else np.nan,
                             "share_single_obs_pct": float((part["n_obs"] == 1).mean()*100) if len(part) else np.nan,
                             "mean_max_gross_bps": float(part["max_gross_gap_bps"].mean()) if len(part) else np.nan,
                             "mean_net_at_start_bps": float(part["net_bps"].mean()) if len(part) else np.nan,
                             "n_any_net_positive": int(part["any_net_positive"].sum()) if len(part) else 0,
                             "n_backtest_a_signals": len(signals) if signals is not None else np.nan,
                             "backtest_a_pnl_usdt": float(signals["pnl_usdt"].sum()) if signals is not None else np.nan})
            ref_rate = rates.get(reference, np.nan)
            for row in rows[-len(mapping):]:
                row["rate_ratio_vs_reference"] = row["events_per_1000_pair_minutes"]/ref_rate if ref_rate else np.nan
    return pd.DataFrame(rows, columns=T6B_COLUMNS)


def live_weekend_rows(events: pd.DataFrame, sessions: pd.DataFrame, pairs_per_group: dict) -> pd.DataFrame:
    """Live sample: weekend vs weekday only (UTC calendar day, same definition as D5 flag_weekend).

    sessions: session_windows(D2). Exposure is the collected time (session length in minutes), because the live
    sample is three-hour sessions, not a continuous series.
    """
    weekend_of = lambda t: pd.Timestamp(t).tz_convert("UTC").dayofweek >= 5
    minutes = {"weekend": 0.0, "weekday": 0.0}
    for session in sessions.itertuples():
        minutes["weekend" if weekend_of(session.start) else "weekday"] += (session.end - session.start).total_seconds()/60
    ev = events.assign(level=events["ts_start"].map(lambda t: "weekend" if weekend_of(t) else "weekday"))
    kind = events["data_kind"].iloc[0] if len(events) else "live"
    rows = []
    for market_type in GROUPS:
        rates = {}
        for level in ("weekday", "weekend"):
            part = ev[(ev["market_type"] == market_type) & (ev["level"] == level)]
            exposure = minutes[level]*pairs_per_group[market_type]
            rates[level] = len(part)/exposure*1000 if exposure else np.nan
            rows.append({"source": "live_calendar", "data_kind": kind, "dimension": "weekend", "level": level, "market_type": market_type,
                         "minutes_in_level": round(minutes[level]), "n_events": len(part), "events_per_1000_pair_minutes": rates[level],
                         "reference_level": "weekday",
                         "median_duration_s": float(part["duration_s"].median()) if len(part) else np.nan,
                         "p90_duration_s": float(part["duration_s"].quantile(.9)) if len(part) else np.nan,
                         "share_single_obs_pct": float((part["n_obs"] == 1).mean()*100) if len(part) else np.nan,
                         "mean_max_gross_bps": float(part["max_gross_gap_bps"].mean()) if len(part) else np.nan,
                         "mean_net_at_start_bps": float(part["net_bps"].mean()) if len(part) else np.nan,
                         "n_any_net_positive": int(part["any_net_positive"].sum()) if len(part) else 0,
                         "n_backtest_a_signals": np.nan, "backtest_a_pnl_usdt": np.nan})
        for row in rows[-2:]:
            row["rate_ratio_vs_reference"] = row["events_per_1000_pair_minutes"]/rates["weekday"] if rates["weekday"] else np.nan
    return pd.DataFrame(rows, columns=T6B_COLUMNS)
