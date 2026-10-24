"""C6.06: T5 cost waterfall. Stages add up exactly (no double counting); spot and perp apart; A and B never pooled."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest import make_t5
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.engine_live import LiveBacktestConfig, run_backtest_live
from src.backtest.waterfall import T5_COLUMNS, cost_waterfall, decompose
from src.cost_model.costs import FeeSchedule, gross_gap_bps

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-01T00:01Z")
VENUES = ["binance_spot", "okx_spot", "binance_perp", "okx_perp"]
FEES = FeeSchedule(venues={"binance_spot": {"taker_bps": 10.0}, "okx_spot": {"taker_bps": 10.0},
                           "binance_perp": {"taker_bps": 5.0}, "okx_perp": {"taker_bps": 5.5}}, slippage_bps=1.0, extra_cost_bps=0.5)


def proxy_ledger(h=2.0):
    index = pd.date_range(T0, periods=4, freq="1min", name="ts_available").as_unit("ns")
    p = pd.DataFrame({"close__binance_spot": [100.0]*4, "close__okx_spot": [100.4, 100.1, 100.0, 100.4],
                      "close__binance_perp": [200.0]*4, "close__okx_perp": [200.6, 200.5, 200.0, 200.0]}, index=index)
    p.attrs["data_kind"] = "synthetic"
    return run_backtest(p, BacktestConfig(fees=FEES, half_spread_bps={v: h for v in VENUES}, qty_btc=0.1)).ledger


def live_ledger():
    index = pd.date_range("2026-10-21T13:00Z", periods=3, freq="5s", name="ts_available").as_unit("ns")
    w = pd.DataFrame(index=index)
    rows = {"binance_perp": [(200.0, 200.02)]*3, "okx_perp": [(200.5, 200.52), (200.1, 200.12), (200.0, 200.02)]}
    for v, values in rows.items():
        w[f"bid__{v}"], w[f"ask__{v}"] = np.array(values).T
        w[f"bid_size__{v}"] = w[f"ask_size__{v}"] = 1.0
        w[f"quote_age_s__{v}"] = 1.0
    w.attrs["data_kind"] = "synthetic"
    sessions = pd.DataFrame({"session_id": ["S"], "start": [index[0]], "end": [index[-1]]})
    return run_backtest_live(w, LiveBacktestConfig(fees=FEES, qty_btc=0.1), sessions).ledger


@pytest.mark.parametrize("ledger_fn", [proxy_ledger, live_ledger])
def test_stages_add_up_exactly_per_signal(ledger_fn):
    ledger = ledger_fn()
    parts = decompose(ledger)
    assert len(parts) == (ledger.status == "filled").sum() > 0
    total = parts.mid_gap_bps - parts.fee_bps - parts.spread_bps - parts.slippage_bps - parts.latency_bps
    assert np.allclose(total, parts.net_bps)
    filled = ledger[ledger.status == "filled"]
    assert np.allclose(parts.fee_bps, filled.fee_total_bps) and np.allclose(parts.slippage_bps, 1.5)
    assert np.allclose(parts.latency_bps, filled.decision_net_bps - filled.net_bps)


def test_proxy_spread_stage_is_one_half_spread_per_leg():
    h = 2.0
    ledger = proxy_ledger(h=h)
    parts, filled = decompose(ledger), ledger[ledger.status == "filled"]
    # exact: mid gap minus the gross after adding h once to each leg (~2h; the small rest is the product of the two legs)
    sell_close = filled.buy_px / (1 + h/1e4) * (1 + filled.decision_mid_gap_bps/1e4)
    expected = filled.decision_mid_gap_bps - gross_gap_bps(sell_close * (1 - h/1e4), filled.buy_px)
    assert np.allclose(parts.spread_bps, expected) and np.allclose(parts.spread_bps, 2*h, atol=0.02)
    assert np.allclose(decompose(proxy_ledger(h=0.0)).spread_bps, 0.0)


def test_live_spread_stage_comes_from_real_quotes():
    parts = decompose(live_ledger())
    # mid gap minus the gross from the touch = half of each quoted spread (0.02 USDT on 200, twice)
    assert np.allclose(parts.spread_bps, 1.0, atol=0.01)


def test_inconsistent_ledger_is_refused():
    ledger = proxy_ledger()
    ledger.loc[ledger.status == "filled", "net_bps"] += 0.5
    with pytest.raises(ValueError, match="fill gross - fees - slippage vs net"):
        decompose(ledger)
    ledger = proxy_ledger()
    ledger.loc[ledger.status == "filled", "other_cost_usdt"] = 0.0          # slippage dropped from the USDT costs
    with pytest.raises(ValueError, match="slippage stage"):
        decompose(ledger)
    ledger = proxy_ledger()
    ledger.loc[ledger.status == "filled", "pnl_usdt"] *= 0.5
    with pytest.raises(ValueError, match="pnl_usdt"):
        decompose(ledger)


def test_t5_rows_per_market_type_with_counts_and_rejections():
    ledger = proxy_ledger()
    t5 = cost_waterfall(ledger, "A", "1min")
    assert list(t5.columns) == T5_COLUMNS and list(t5.market_type) == ["spot", "perp"]
    for _, row in t5.iterrows():
        part = ledger[ledger.market_type == row.market_type]
        assert row.n_signals == len(part) and row.n_filled == (part.status == "filled").sum()
        assert row.n_positive_net == (part[part.status == "filled"].net_bps > 0).sum()
        assert row.n_positive_mid_gap >= row.n_positive_after_fees >= row.n_positive_after_spread >= row.n_positive_after_slippage
        assert row.total_pnl_usdt == pytest.approx(part.loc[part.status == "filled", "pnl_usdt"].sum())
    assert "fill_beyond_sample" in ";".join(t5.rejected_by_reason)


def test_empty_group_still_gets_a_row():
    t5 = cost_waterfall(live_ledger(), "B", "5s")
    spot = t5[t5.market_type == "spot"].iloc[0]
    assert spot.n_signals == 0 and np.isnan(spot.mean_net_bps)


def test_make_t5_refuses_mixing_and_wrong_zone(tmp_path):
    a, b = tmp_path/"a.parquet", tmp_path/"b.parquet"
    proxy_ledger().to_parquet(a)
    live_ledger().to_parquet(b)
    t5 = make_t5.main(["--a-ledger", str(a), "--b-ledger", str(b), "--output-dir", str(tmp_path/"out")])
    assert (tmp_path/"out/T5_arbitrage_net.csv").is_file() and set(t5.backtest) == {"A_proxy_close_1m_dev", "B_live_bidask"}
    real = live_ledger().assign(data_kind="live")
    real.to_parquet(tmp_path/"real.parquet")
    with pytest.raises(ValueError, match="mix"):
        make_t5.main(["--a-ledger", str(a), "--b-ledger", str(tmp_path/"real.parquet"), "--output-dir", str(tmp_path/"x")])
    with pytest.raises(ValueError, match="Synthetic"):
        make_t5.main(["--a-ledger", str(a), "--b-ledger", str(b), "--output-dir", str(ROOT/"outputs/tables/probe_t5")])
    assert not (ROOT/"outputs/tables/probe_t5").exists()
