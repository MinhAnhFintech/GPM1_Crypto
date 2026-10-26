"""C6.09: T6b gap events by regime. Hand-made flags and events; normalisation, assignment at ts_start, guards."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest import make_t6b
from src.backtest.regime_gap import T6B_COLUMNS, gap_by_regime, live_weekend_rows
from src.cost_model.gap_events import detect_gap_events
from src.utils.io_utils import write_parquet

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-05T00:00Z")                 # a Monday
PAIRS = {"spot": 6, "perp": 6}


def flags(n=100, weekend_from=None, high_vol=(), event=(), trend=None):
    ts = pd.date_range(T0, periods=n, freq="1min", name="ts_available").as_unit("ns")
    f = pd.DataFrame({"ts_available": ts, "flag_high_vol": False, "flag_weekend": False, "flag_event": False, "regime_bull_bear": "bull"})
    for a, b in high_vol:
        f.loc[a:b, "flag_high_vol"] = True
    for a, b in event:
        f.loc[a:b, "flag_event"] = True
    if weekend_from is not None:
        f.loc[weekend_from:, "flag_weekend"] = True
    if trend is not None:
        f.loc[:trend, "regime_bull_bear"] = pd.NA
    f.attrs["data_kind"] = "synthetic"
    return f


def events(spec, kind="synthetic"):
    """spec = [(market_type, start_minute, n_minutes)] -> D7 events (gross 6 bps while above the 5 bps threshold)."""
    frames = []
    for i, (market, start, length) in enumerate(spec):
        ts = pd.date_range(T0 + pd.Timedelta(minutes=start - 1), periods=length + 2, freq="1min").as_unit("ns")
        gross = np.r_[0.0, np.full(length, 6.0), 0.0]
        frame = pd.DataFrame({"ts_available": ts, "buy_exchange": f"b{i}", "sell_exchange": f"s{i}", "market_type": market,
                              "gross_gap_bps": gross, "net_gap_bps": gross - 20.0})
        frame.attrs["data_kind"] = kind
        frames.append(detect_gap_events(frame, 5.0, 60))
    out = pd.concat(frames, ignore_index=True)
    out.attrs["data_kind"] = kind
    return out


WINDOW = (T0, T0 + pd.Timedelta(minutes=99))


def row(table, dimension, level, market):
    selected = table[(table.dimension == dimension) & (table.level == level) & (table.market_type == market)]
    assert len(selected) == 1
    return selected.iloc[0]


def test_rates_are_normalised_by_time_in_regime_and_pairs():
    f = flags(high_vol=[(0, 19)])                                            # 20 high-vol minutes, 80 normal
    ev = events([("perp", 5, 2), ("perp", 10, 1), ("perp", 50, 3), ("spot", 60, 1)])
    t = gap_by_regime(ev, f, WINDOW, PAIRS)
    high, normal = row(t, "volatility", "high_vol", "perp"), row(t, "volatility", "normal_vol", "perp")
    assert (high.n_events, high.minutes_in_level, normal.n_events, normal.minutes_in_level) == (2, 20, 1, 80)
    assert high.events_per_1000_pair_minutes == pytest.approx(2/(20*6)*1000)
    assert normal.events_per_1000_pair_minutes == pytest.approx(1/(80*6)*1000)
    assert high.rate_ratio_vs_reference == pytest.approx((2/20)/(1/80)) and normal.rate_ratio_vs_reference == 1.0
    assert row(t, "volatility", "high_vol", "spot").n_events == 0 and row(t, "volatility", "normal_vol", "spot").n_events == 1
    assert list(t.columns) == T6B_COLUMNS


def test_event_is_assigned_the_regime_at_its_start_not_its_end():
    f = flags(high_vol=[(10, 30)])
    ev = events([("perp", 5, 10)])                                           # starts minute 5 (normal), ends in high vol
    t = gap_by_regime(ev, f, WINDOW, PAIRS)
    assert row(t, "volatility", "normal_vol", "perp").n_events == 1 and row(t, "volatility", "high_vol", "perp").n_events == 0


def test_weekend_event_and_duration_statistics():
    f = flags(weekend_from=60)
    ev = events([("spot", 10, 1), ("spot", 20, 3), ("spot", 70, 2)])
    t = gap_by_regime(ev, f, WINDOW, PAIRS)
    weekday, weekend = row(t, "weekend", "weekday", "spot"), row(t, "weekend", "weekend", "spot")
    assert (weekday.n_events, weekend.n_events) == (2, 1)
    assert weekday.median_duration_s == pytest.approx(120.0) and weekend.median_duration_s == 120.0
    assert weekday.share_single_obs_pct == pytest.approx(50.0) and weekend.share_single_obs_pct == 0.0
    assert weekday.mean_max_gross_bps == pytest.approx(6.0) and weekday.n_any_net_positive == 0
    assert weekday.mean_net_at_start_bps == pytest.approx(-14.0)


def test_trend_warm_up_is_excluded_not_counted_as_a_regime():
    f = flags(trend=29)                                                      # first 30 minutes have no bull/bear label
    ev = events([("perp", 5, 1), ("perp", 40, 1)])                           # one in warm-up, one labelled bull
    t = gap_by_regime(ev, f, WINDOW, PAIRS)
    bull, bear = row(t, "trend", "bull", "perp"), row(t, "trend", "bear", "perp")
    assert (bull.minutes_in_level, bull.n_events, bear.minutes_in_level, bear.n_events) == (70, 1, 0, 0)
    assert np.isnan(bear.events_per_1000_pair_minutes) and np.isnan(bear.rate_ratio_vs_reference)      # no minutes, no rate
    assert bull.rate_ratio_vs_reference == 1.0


def test_backtest_signals_are_counted_per_regime_at_decision_time():
    f = flags(high_vol=[(0, 19)])
    ledger = pd.DataFrame({"ts_decision": [T0 + pd.Timedelta(minutes=3), T0 + pd.Timedelta(minutes=50), T0 + pd.Timedelta(minutes=51)],
                           "market_type": "perp", "status": ["filled", "filled", "rejected"], "pnl_usdt": [-2.0, -3.0, 0.0]})
    t = gap_by_regime(events([("perp", 5, 1)]), f, WINDOW, PAIRS, ledger)
    high, normal = row(t, "volatility", "high_vol", "perp"), row(t, "volatility", "normal_vol", "perp")
    assert (high.n_backtest_a_signals, high.backtest_a_pnl_usdt) == (1, -2.0)
    assert (normal.n_backtest_a_signals, normal.backtest_a_pnl_usdt) == (1, -3.0)      # the rejected signal is not counted


def test_events_outside_the_analysed_window_are_refused():
    ev = events([("perp", 95, 8)])                                           # runs past the window end (minute 99)
    with pytest.raises(ValueError, match="development split only"):
        gap_by_regime(ev, flags(), WINDOW, PAIRS)


def test_live_weekend_rows_use_utc_calendar_and_collected_minutes():
    saturday = pd.Timestamp("2026-10-17T13:00Z")
    sessions = pd.DataFrame({"session_id": ["S2", "M1"], "start": [saturday, saturday + pd.Timedelta(days=2, hours=-6)],
                             "end": [saturday + pd.Timedelta(hours=3), saturday + pd.Timedelta(days=2, hours=-3)]})
    ev = events([("spot", 1, 1)], kind="live")
    ev["ts_start"] = [saturday + pd.Timedelta(minutes=5)]
    ev["ts_end"] = ev["ts_start"]
    t = live_weekend_rows(ev, sessions, PAIRS)
    weekend, weekday = row(t, "weekend", "weekend", "spot"), row(t, "weekend", "weekday", "spot")
    assert (weekend.minutes_in_level, weekday.minutes_in_level) == (180, 180) and weekend.n_events == 1
    assert weekend.events_per_1000_pair_minutes == pytest.approx(1/(180*6)*1000) and set(t.source) == {"live_calendar"}
    assert row(t, "weekend", "weekday", "perp").n_events == 0


def _write_inputs(tmp_path, fingerprint="fp-test", kind="synthetic"):
    n = 400
    f = flags(n, high_vol=[(0, 39)])
    f.attrs["data_kind"] = kind
    tables = tmp_path/"tables"
    tables.mkdir()
    write_parquet(f, tmp_path/"regime_flags.parquet", "D5")
    ev = events([("perp", 5, 2), ("spot", 100, 1)], kind=kind)
    ev.to_parquet(tables/"gap_events_historical.parquet")
    manifest = {"fingerprint": fingerprint, "dev_start": str(f.ts_available.iloc[0]), "dev_end": str(f.ts_available.iloc[-1])}
    (tmp_path/"manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path/"regime_flags_receipt.json").write_text(json.dumps({"panel_fingerprint": "fp-test"}), encoding="utf-8")
    return tables


def test_cli_end_to_end_and_provenance_guards(tmp_path):
    tables = _write_inputs(tmp_path)
    args = ["--tables", str(tables), "--regime", str(tmp_path/"regime_flags.parquet"), "--manifest", str(tmp_path/"manifest.json"),
            "--live-quotes", str(tmp_path/"none.parquet")]
    table = make_t6b.main(args + ["--output-dir", str(tmp_path/"out")])
    sources = json.loads((tmp_path/"out/T6b_gap_by_regime_sources.json").read_text(encoding="utf-8"))
    assert (tmp_path/"out/T6b_gap_by_regime.csv").is_file() and set(table.source) == {"historical_dev"}
    assert sources["d5_is_official_path"] is False and sources["d5_panel_fingerprint_matches_d4"] is True
    assert set(sources["inputs"]) >= {"gap_events_historical", "regime_flags", "split_manifest"}
    with pytest.raises(ValueError, match="Synthetic"):                       # synthetic results never land in the research zone
        make_t6b.main(args + ["--output-dir", str(ROOT/"outputs/tables/probe_t6b")])
    assert not (ROOT/"outputs/tables/probe_t6b").exists()


def test_cli_refuses_a_d5_built_on_another_panel_or_source(tmp_path):
    tables = _write_inputs(tmp_path, fingerprint="a-different-panel")
    args = ["--tables", str(tables), "--regime", str(tmp_path/"regime_flags.parquet"), "--manifest", str(tmp_path/"manifest.json"),
            "--output-dir", str(tmp_path/"out")]
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        make_t6b.main(args)
    other = tmp_path/"other"
    other.mkdir()
    tables = _write_inputs(other)
    events = pd.read_parquet(tables/"gap_events_historical.parquet")
    # A valid historical file paired with a synthetic D5 must reach the
    # cross-file source check, rather than fail on conflicting labels inside D7.
    events["data_kind"] = "historical"
    events.attrs["data_kind"] = "historical"
    events.to_parquet(tables/"gap_events_historical.parquet")
    with pytest.raises(ValueError, match="share one data_kind"):               # events say historical, D5 says synthetic
        make_t6b.main(["--tables", str(tables), "--regime", str(other/"regime_flags.parquet"), "--manifest", str(other/"manifest.json"),
                       "--output-dir", str(tmp_path/"out2")])
