"""C6.04: backtest A (close proxy). Hand-made panels; timing, costs, rejections and D8 shape."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest.calibration import estimate_half_spread_bps, half_spread_map
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest import run_backtest_a
from src.cost_model.costs import FeeSchedule, gross_gap_bps
from src.utils.schema import validate_frame

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-01T00:01Z")
FEES = FeeSchedule(venues={v: {"taker_bps": 5.0} for v in ["binance_spot", "okx_spot", "binance_perp", "okx_perp"]})


def panel(closes: dict, missing: dict | None = None) -> pd.DataFrame:
    """closes = {venue: [close per minute]}; missing = {venue: [bool per minute]} (close set NaN where True)."""
    n = len(next(iter(closes.values())))
    index = pd.date_range(T0, periods=n, freq="1min", name="ts_available").as_unit("ns")
    frame = pd.DataFrame(index=index)
    for venue, values in closes.items():
        flags = np.asarray((missing or {}).get(venue, [False]*n))
        frame[f"close__{venue}"] = np.where(flags, np.nan, np.asarray(values, dtype=float))
        frame[f"missing__{venue}"] = flags
    frame.attrs["data_kind"] = "synthetic"
    return frame


def cfg(h=0.0, venues=("binance_spot", "okx_spot"), **kw):
    return BacktestConfig(fees=FEES, half_spread_bps={v: h for v in venues}, **kw)


def only(ledger, buy, sell):
    return ledger[(ledger.buy_venue == buy) & (ledger.sell_venue == sell)].reset_index(drop=True)


def test_decision_uses_t_and_fill_uses_t_plus_latency():
    p = panel({"binance_spot": [100, 100, 100], "okx_spot": [100.5, 100, 100]})
    trade = only(run_backtest(p, cfg(latency_min=1)).ledger, "binance_spot", "okx_spot").iloc[0]
    assert trade.ts_decision == T0 and trade.ts_fill == T0 + pd.Timedelta(minutes=1)
    assert trade.decision_gross_bps == pytest.approx(50.0) and trade.decision_net_bps == pytest.approx(40.0)
    assert trade.status == "filled" and trade.gross_bps == pytest.approx(0.0) and trade.net_bps == pytest.approx(-10.0)
    assert trade.pnl_usdt < 0                       # the gap closed during the latency: proxy loss


def test_no_look_ahead_future_prices_do_not_change_decision():
    base = panel({"binance_spot": [100, 100, 100], "okx_spot": [100.5, 100, 100]})
    later = panel({"binance_spot": [100, 90, 300], "okx_spot": [100.5, 200, 50]})
    a = only(run_backtest(base, cfg()).ledger, "binance_spot", "okx_spot").iloc[0]
    b = only(run_backtest(later, cfg()).ledger, "binance_spot", "okx_spot").iloc[0]
    assert (a.ts_decision, a.decision_gross_bps, a.decision_net_bps) == (b.ts_decision, b.decision_gross_bps, b.decision_net_bps)


def test_latency_moves_fill_time_only():
    p = panel({"binance_spot": [100, 100, 100, 100], "okx_spot": [100.5, 100, 100.3, 100]})
    trade = only(run_backtest(p, cfg(latency_min=2)).ledger, "binance_spot", "okx_spot").iloc[0]
    assert trade.ts_fill == T0 + pd.Timedelta(minutes=2) and trade.gross_bps == pytest.approx(30.0)
    assert trade.latency_min == 2 and trade.signal_id.endswith("_L2")


def test_half_spread_added_once_per_leg():
    h = 2.0
    p = panel({"binance_spot": [100, 100], "okx_spot": [100.5, 100.5]})
    trade = only(run_backtest(p, cfg(h=h)).ledger, "binance_spot", "okx_spot").iloc[0]
    ask_buy, bid_sell = 100 * (1 + h/1e4), 100.5 * (1 - h/1e4)
    assert trade.buy_px == pytest.approx(ask_buy) and trade.sell_px == pytest.approx(bid_sell)
    assert trade.gross_bps == pytest.approx(gross_gap_bps(bid_sell, ask_buy))
    assert trade.half_spread_buy_bps == h and trade.half_spread_sell_bps == h


def test_fees_both_legs_and_pnl_identity():
    fees = FeeSchedule(venues={"binance_spot": {"taker_bps": 4.0}, "okx_spot": {"taker_bps": 9.0}}, slippage_bps=1.0)
    p = panel({"binance_spot": [100, 100], "okx_spot": [100.5, 100.4]})
    c = BacktestConfig(fees=fees, half_spread_bps={"binance_spot": 0.5, "okx_spot": 0.5}, qty_btc=0.2)
    t = only(run_backtest(p, c).ledger, "binance_spot", "okx_spot").iloc[0]
    notional = t.qty_btc * t.buy_px
    assert t.fee_total_bps == 13.0 and t.net_bps == pytest.approx(t.gross_bps - 13.0 - 1.0)
    assert t.fees_usdt == pytest.approx(13.0/1e4 * notional) and t.other_cost_usdt == pytest.approx(1.0/1e4 * notional)
    assert t.pnl_usdt == pytest.approx(t.qty_btc * (t.sell_px - t.buy_px) - t.fees_usdt - t.other_cost_usdt)
    assert t.pnl_usdt == pytest.approx(t.net_bps/1e4 * notional)


def test_missing_fill_and_end_of_sample_are_rejected():
    missing = panel({"binance_spot": [100, 100, 100], "okx_spot": [100.5, 100.5, 100]},
                    missing={"okx_spot": [False, True, False]})
    t = only(run_backtest(missing, cfg()).ledger, "binance_spot", "okx_spot").iloc[0]
    assert (t.status, t.reject_reason) == ("rejected", "missing_fill_price")
    assert pd.isna(t.ts_fill) and np.isnan(t.buy_px) and t.pnl_usdt == 0 and t.fees_usdt == 0
    end = panel({"binance_spot": [100, 100], "okx_spot": [100, 100.5]})
    t = only(run_backtest(end, cfg()).ledger, "binance_spot", "okx_spot").iloc[-1]
    assert (t.status, t.reject_reason) == ("rejected", "fill_beyond_sample")


def test_no_new_signal_on_pair_until_previous_fill():
    p = panel({"binance_spot": [100]*6, "okx_spot": [100.5]*6})
    ledger = only(run_backtest(p, cfg(latency_min=2)).ledger, "binance_spot", "okx_spot")
    assert list(ledger.ts_decision) == [T0, T0 + pd.Timedelta(minutes=3)]


def test_no_spot_perp_pairs_even_with_large_gap():
    p = panel({"binance_spot": [100, 100], "binance_perp": [110, 110], "okx_perp": [110, 110]})
    result = run_backtest(p, cfg(venues=("binance_spot", "binance_perp", "okx_perp")))
    assert result.ledger.empty
    assert set(result.summary.market_type) == {"perp"}
    assert not any("spot" in b and "perp" in s or "perp" in b and "spot" in s for b, s in result.params["pairs"])


def test_entry_threshold_filters_signals():
    p = panel({"binance_spot": [100, 100], "okx_spot": [100.5, 100.5]})
    assert run_backtest(p, cfg(entry_threshold_bps=45.0)).ledger.empty
    assert len(only(run_backtest(p, cfg(entry_threshold_bps=35.0)).ledger, "binance_spot", "okx_spot")) == 1


def test_missing_half_spread_raises():
    p = panel({"binance_spot": [100, 100], "okx_spot": [100.5, 100.5]})
    with pytest.raises(KeyError, match="okx_spot"):
        run_backtest(p, BacktestConfig(fees=FEES, half_spread_bps={"binance_spot": 0.1}))


def test_ledger_matches_d8_and_is_labelled_proxy():
    p = panel({"binance_spot": [100, 100, 100], "okx_spot": [100.5, 100.5, 100.5]})   # t0 fills at t1; t2 has no t3
    result = run_backtest(p, cfg(latency_min=1))
    ledger = result.ledger
    assert set(ledger.status) == {"filled", "rejected"}
    validate_frame(ledger, "D8")
    assert ledger.signal_id.is_unique and set(ledger.method) == {"proxy_close_1m"} and set(ledger.data_kind) == {"synthetic"}
    assert str(ledger.ts_decision.dtype) == "datetime64[ns, UTC]" and str(ledger.ts_fill.dtype) == "datetime64[ns, UTC]"
    assert result.params["method"] == "proxy_close_1m" and result.params["latency_min"] == 1


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        cfg(latency_min=-1)
    with pytest.raises(ValueError):
        cfg(qty_btc=0)
    with pytest.raises(ValueError):
        BacktestConfig(fees=FEES, half_spread_bps={"binance_spot": -1.0})


def test_half_spread_calibration_is_median_spread_over_two():
    d2 = pd.read_parquet(ROOT/"data/fixtures/multi/live_quotes_long.parquet")
    cal = estimate_half_spread_bps(d2, ["binance_spot", "okx_spot"])
    venue = d2.exchange + "_" + d2.market_type
    for v in ["binance_spot", "okx_spot"]:
        assert half_spread_map(cal)[v] == pytest.approx(d2.loc[venue == v, "spread_bps"].median() / 2)
    assert set(cal.source_data_kind) == {"synthetic"}
    with pytest.raises(KeyError, match="kraken_spot"):
        estimate_half_spread_bps(d2, ["kraken_spot"])


def test_runner_on_fixtures_writes_synthetic_outputs(tmp_path):
    result = run_backtest_a.main(["--fixture-case", "multi", "--output-dir", str(tmp_path/"synthetic"/"multi")])
    out = tmp_path/"synthetic"/"multi"
    for name in ["backtest_a_dev_L1_ledger.parquet", "backtest_a_dev_L1_summary.csv", "backtest_a_dev_L1_params.json",
                 "half_spread_calibration.csv"]:
        assert (out/name).is_file()
    assert result.params["data_kind"] == "synthetic"
    with pytest.raises(ValueError, match="outputs/synthetic"):
        run_backtest_a.main(["--fixture-case", "multi", "--output-dir", str(ROOT/"outputs/tables")])
