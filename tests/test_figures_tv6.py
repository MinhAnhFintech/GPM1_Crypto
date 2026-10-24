"""C6.08: F4 (gap duration CCDF) and F5 (cumulative P&L) - data transforms, rendering and output guard."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest import make_figures
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.engine_live import LiveBacktestConfig, run_backtest_live
from src.backtest.figures import THEMES, ccdf, cumulative_pnl, plot_cumulative_pnl, plot_gap_duration
from src.cost_model.costs import FeeSchedule
from src.cost_model.gap_events import detect_gap_events, write_gap_events

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-01T00:01Z")
PNG = b"\x89PNG\r\n\x1a\n"
FEES = FeeSchedule(venues={v: {"taker_bps": 5.0} for v in ["binance_spot", "okx_spot", "binance_perp", "okx_perp"]})


def test_ccdf_by_hand():
    curve = ccdf([60, 60, 60, 120, 120, 600])
    assert list(curve.duration_s) == [60, 120, 600]
    assert list(curve.share_at_least) == pytest.approx([1.0, 0.5, 1/6])       # 6/6, 3/6, 1/6 last at least x
    assert ccdf([]).empty


def events(kind="synthetic", step=60, market="spot"):
    gaps = pd.DataFrame({"ts_available": pd.date_range(T0, periods=40, freq=f"{step}s").as_unit("ns"),
                         "buy_exchange": "binance", "sell_exchange": "okx", "market_type": market,
                         "gross_gap_bps": ([0.0, 6.0, 6.0, 0.0] * 10), "net_gap_bps": ([-20.0, -14.0, -14.0, -20.0] * 10)})
    gaps.attrs["data_kind"] = kind
    return detect_gap_events(gaps, 5.0, step)


def proxy_ledger():
    index = pd.date_range(T0, periods=6, freq="1min", name="ts_available").as_unit("ns")
    p = pd.DataFrame({"close__binance_spot": [100.0]*6, "close__okx_spot": [100.4, 100.1, 100.0, 100.4, 100.2, 100.4],
                      "close__binance_perp": [200.0]*6, "close__okx_perp": [200.6, 200.5, 200.0, 200.6, 200.1, 200.0]}, index=index)
    p.attrs["data_kind"] = "synthetic"
    cfg = BacktestConfig(fees=FEES, half_spread_bps={v: 0.5 for v in p.columns.str[len("close__"):]}, qty_btc=0.1)
    return run_backtest(p, cfg).ledger, (index[0], index[-1])


def live_ledger():
    index = pd.date_range("2026-10-21T13:00Z", periods=4, freq="5s", name="ts_available").as_unit("ns")
    w = pd.DataFrame(index=index)
    for v, rows in {"binance_perp": [(200.0, 200.02)]*4,
                    "okx_perp": [(200.5, 200.52), (200.1, 200.12), (200.0, 200.02), (200.0, 200.02)]}.items():
        w[f"bid__{v}"], w[f"ask__{v}"] = np.array(rows).T
        w[f"bid_size__{v}"] = w[f"ask_size__{v}"] = 1.0
        w[f"quote_age_s__{v}"] = 1.0
    w.attrs["data_kind"] = "synthetic"
    sessions = pd.DataFrame({"session_id": ["S"], "start": [index[0]], "end": [index[-1]]})
    return run_backtest_live(w, LiveBacktestConfig(fees=FEES, qty_btc=0.1), sessions).ledger, (index[0], index[-1])


def test_cumulative_pnl_is_the_running_sum_in_fill_order():
    ledger, _ = proxy_ledger()
    for market in ("spot", "perp"):
        curve = cumulative_pnl(ledger, market)
        part = ledger[(ledger.market_type == market) & (ledger.status == "filled")].sort_values("ts_fill")
        assert np.allclose(curve.pnl_usdt, part.pnl_usdt.cumsum()) and curve.ts_fill.is_monotonic_increasing


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_figures_render_to_png_in_both_themes(tmp_path, theme):
    spot, perp = events(market="spot"), events(market="perp")
    both = pd.concat([spot, perp], ignore_index=True)
    both.attrs["data_kind"] = "synthetic"
    f4 = tmp_path/f"F4_{theme}.png"
    summary = plot_gap_duration({"Lịch sử": (both, 60.0), "Live": (both.assign(duration_s=both.duration_s/12), 5.0)}, 5.0, f4, theme)
    assert f4.read_bytes()[:8] == PNG and f4.stat().st_size > 10_000 and len(summary) == 4
    a, window_a = proxy_ledger()
    b, window_b = live_ledger()
    f5 = tmp_path/f"F5_{theme}.png"
    totals = plot_cumulative_pnl({"A": (a, window_a, 0.1), "B": (b, window_b, 0.1)}, f5, theme)
    assert f5.read_bytes()[:8] == PNG and f5.stat().st_size > 10_000
    assert totals[("B", "perp–perp")] == pytest.approx(b[b.status == "filled"].pnl_usdt.sum())


def test_group_without_trades_is_named_not_drawn(tmp_path):
    ledger, window = live_ledger()                       # perp only: spot has no signals
    assert cumulative_pnl(ledger, "spot").empty
    totals = plot_cumulative_pnl({"B": (ledger, window, 0.1)}, tmp_path/"F5.png")
    assert ("B", "spot–spot") not in totals and ("B", "perp–perp") in totals


def test_theme_colours_are_the_validated_pair_and_distinct():
    assert THEMES["light"]["spot"] == "#2a78d6" and THEMES["light"]["perp"] == "#eb6834"
    assert THEMES["dark"]["spot"] == "#3987e5" and THEMES["dark"]["perp"] == "#d95926"
    for theme in THEMES.values():
        assert theme["spot"] != theme["perp"] and theme["surface"] not in (theme["spot"], theme["perp"])


def _tables(tmp_path):
    tables = tmp_path/"tables"
    tables.mkdir()
    both = pd.concat([events(market="spot"), events(market="perp")], ignore_index=True)
    both.attrs["data_kind"] = "synthetic"
    write_gap_events(both, tables/"gap_events_historical.parquet")
    write_gap_events(both, tables/"gap_events.parquet")
    a, window_a = proxy_ledger()
    b, window_b = live_ledger()
    a.to_parquet(tables/"backtest_a_dev_L1_ledger.parquet")
    b.to_parquet(tables/"trades_ledger.parquet")
    for name, window in (("backtest_a_dev_L1_params.json", window_a), ("backtest_b_L5s_params.json", window_b)):
        (tables/name).write_text(f'{{"first_ts": "{window[0]}", "last_ts": "{window[1]}"}}', encoding="utf-8")
    return tables


def test_runner_writes_figures_and_refuses_the_research_zone(tmp_path):
    tables = _tables(tmp_path)
    make_figures.main(["--tables", str(tables), "--output-dir", str(tmp_path/"figs")])
    assert (tmp_path/"figs/F4_gap_duration.png").read_bytes()[:8] == PNG and (tmp_path/"figs/F5_cumulative_pnl.png").is_file()
    with pytest.raises(ValueError, match="Synthetic"):
        make_figures.main(["--tables", str(tables), "--output-dir", str(ROOT/"outputs/figures/probe_figs")])
    assert not (ROOT/"outputs/figures/probe_figs").exists()


def test_runner_refuses_to_mix_synthetic_and_research_inputs(tmp_path):
    tables = _tables(tmp_path)
    live = pd.read_parquet(tables/"trades_ledger.parquet").assign(data_kind="live")
    live.to_parquet(tables/"trades_ledger.parquet")
    with pytest.raises(ValueError, match="mix"):
        make_figures.main(["--tables", str(tables), "--output-dir", str(tmp_path/"figs")])
