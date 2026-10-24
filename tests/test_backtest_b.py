"""C6.05: backtest B on live bid/ask. Hand-made aligned quotes; timing, rejections, costs and D8 shape."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest import run_backtest_b
from src.backtest.engine_live import LiveBacktestConfig, market_summary, run_backtest_live, session_windows
from src.cost_model.costs import FeeSchedule, gross_gap_bps
from src.utils.schema import validate_frame

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-10-21T13:00:00Z")
FEES = FeeSchedule(venues={v: {"taker_bps": 5.0} for v in ["binance_spot", "okx_spot", "binance_perp", "okx_perp"]})


def wide(quotes: dict, n: int, step_s=5, missing: dict | None = None, sizes: dict | None = None) -> pd.DataFrame:
    """quotes = {venue: [(bid, ask) per grid step]} on a 5 s UTC grid, ages 1 s, sizes 1 BTC unless given."""
    index = pd.date_range(T0, periods=n, freq=f"{step_s}s", name="ts_available").as_unit("ns")
    frame = pd.DataFrame(index=index)
    for venue, rows in quotes.items():
        bid, ask = np.array(rows, dtype=float).T
        frame[f"bid__{venue}"], frame[f"ask__{venue}"] = bid, ask
        size = np.asarray((sizes or {}).get(venue, [1.0]*n), dtype=float)
        frame[f"bid_size__{venue}"], frame[f"ask_size__{venue}"] = size, size
        frame[f"quote_age_s__{venue}"] = 1.0
        frame[f"missing__{venue}"] = np.asarray((missing or {}).get(venue, [False]*n))
    frame.attrs["data_kind"] = "synthetic"
    return frame


def one_session(n, step_s=5, end_offset_s=None):
    end = T0 + pd.Timedelta(seconds=step_s*(n-1) if end_offset_s is None else end_offset_s)
    return pd.DataFrame({"session_id": ["20261021_S2"], "start": [T0], "end": [end]})


def cfg(**kw):
    return LiveBacktestConfig(fees=FEES, **{"grid_step_s": 5.0, "latency_s": 5.0, "qty_btc": 0.1, **kw})


def pair(ledger, buy="binance_spot", sell="okx_spot"):
    return ledger[(ledger.buy_venue == buy) & (ledger.sell_venue == sell)].reset_index(drop=True)


GAP = {"binance_spot": [(100.0, 100.01)]*4, "okx_spot": [(100.2, 100.21), (100.15, 100.16), (100.0, 100.01), (100.0, 100.01)]}


def test_decision_at_t_fill_at_t_plus_latency_with_real_quotes_and_no_extra_spread():
    w = wide(GAP, 4)
    t = pair(run_backtest_live(w, cfg(), one_session(4)).ledger).iloc[0]
    assert t.ts_decision == T0 and t.ts_fill == T0 + pd.Timedelta(seconds=5)
    assert t.decision_gross_bps == pytest.approx(gross_gap_bps(100.2, 100.01))
    assert (t.buy_px, t.sell_px) == (100.01, 100.15)                        # quotes at t + 5 s, as quoted
    assert t.gross_bps == pytest.approx(gross_gap_bps(100.15, 100.01))      # spread already inside bid/ask
    assert t.net_bps == pytest.approx(t.gross_bps - 10.0) and t.status == "filled"


def test_future_quotes_do_not_change_the_decision():
    later = {"binance_spot": GAP["binance_spot"], "okx_spot": [GAP["okx_spot"][0], (90, 90.01), (300, 300.01), (50, 50.01)]}
    a = pair(run_backtest_live(wide(GAP, 4), cfg(), one_session(4)).ledger).iloc[0]
    b = pair(run_backtest_live(wide(later, 4), cfg(), one_session(4)).ledger).iloc[0]
    assert (a.ts_decision, a.decision_gross_bps, a.decision_net_bps) == (b.ts_decision, b.decision_gross_bps, b.decision_net_bps)


def test_latency_must_land_on_the_grid():
    with pytest.raises(ValueError, match="multiple"):
        cfg(latency_s=7.0)
    t = pair(run_backtest_live(wide(GAP, 4), cfg(latency_s=10.0), one_session(4)).ledger).iloc[0]
    assert t.ts_fill == T0 + pd.Timedelta(seconds=10) and t.signal_id.endswith("_L10s")


@pytest.mark.parametrize("case, reason", [("beyond", "fill_beyond_sample"), ("session", "fill_after_session_end"),
                                          ("missing", "missing_fill_quote"), ("size", "insufficient_top_size")])
def test_rejections_carry_a_reason_and_no_fill(case, reason):
    n = 4 if case != "beyond" else 1
    quotes = {v: rows[:n] for v, rows in GAP.items()}
    missing = {"okx_spot": [False, True, False, False]} if case == "missing" else None
    sizes = {"okx_spot": [1.0, 0.05, 1.0, 1.0]} if case == "size" else None
    sessions = one_session(n, end_offset_s=3) if case == "session" else one_session(n)
    t = pair(run_backtest_live(wide(quotes, n, missing=missing, sizes=sizes), cfg(), sessions).ledger).iloc[0]
    assert (t.status, t.reject_reason) == ("rejected", reason)
    assert pd.isna(t.ts_fill) and np.isnan(t.buy_px) and t.pnl_usdt == 0 and t.fees_usdt == 0


def test_top_size_check_can_be_switched_off():
    w = wide(GAP, 4, sizes={"okx_spot": [1.0, 0.05, 1.0, 1.0]})
    t = pair(run_backtest_live(w, cfg(require_top_size=False), one_session(4)).ledger).iloc[0]
    assert t.status == "filled" and t.sell_top_size_btc == 0.05


def test_no_new_signal_until_previous_fill():
    w = wide({"binance_spot": [(100.0, 100.01)]*6, "okx_spot": [(100.2, 100.21)]*6}, 6)
    ledger = pair(run_backtest_live(w, cfg(latency_s=10.0), one_session(6)).ledger)
    assert list(ledger.ts_decision) == [T0, T0 + pd.Timedelta(seconds=15)]


def test_spot_and_perp_never_mix_and_are_summarised_separately():
    quotes = {"binance_spot": [(100.0, 100.01)]*3, "okx_spot": [(100.2, 100.21)]*3,
              "binance_perp": [(110.0, 110.01)]*3, "okx_perp": [(110.3, 110.31)]*3}
    result = run_backtest_live(wide(quotes, 3), cfg(), one_session(3))
    assert all(b.rsplit("_", 1)[1] == s.rsplit("_", 1)[1] for b, s in result.params["pairs"])
    assert (result.ledger.buy_venue.str.rsplit("_", n=1).str[1] == result.ledger.market_type).all()
    by_market = market_summary(result.ledger)
    assert set(by_market.market_type) == {"spot", "perp"} and by_market.n_signals.sum() == len(result.ledger)


def test_pnl_identity_and_d8_shape():
    fees = FeeSchedule(venues={"binance_spot": {"taker_bps": 4.0}, "okx_spot": {"taker_bps": 9.0}}, slippage_bps=1.0)
    w = wide({"binance_spot": [(100.0, 100.01)]*3, "okx_spot": [(100.3, 100.31)]*3}, 3)
    result = run_backtest_live(w, LiveBacktestConfig(fees=fees, qty_btc=0.2), one_session(3))
    validate_frame(result.ledger, "D8")
    t = pair(result.ledger).iloc[0]
    notional = t.qty_btc * t.buy_px
    assert t.fees_usdt == pytest.approx(13.0/1e4 * notional) and t.other_cost_usdt == pytest.approx(1.0/1e4 * notional)
    assert t.pnl_usdt == pytest.approx(t.qty_btc * (t.sell_px - t.buy_px) - t.fees_usdt - t.other_cost_usdt)
    assert result.ledger.signal_id.is_unique and set(result.ledger.method) == {"live_bidask"}


def test_session_windows_from_d2():
    d2 = pd.read_parquet(ROOT/"data/fixtures/multi/live_quotes_long.parquet")
    assert list(session_windows(d2).session_id) == ["all"]
    two = pd.concat([d2.assign(session_id="S1"), d2.assign(session_id="S2", ts_recv_utc=d2.ts_recv_utc + pd.Timedelta(days=1))])
    windows = session_windows(two)
    assert list(windows.session_id) == ["S1", "S2"] and windows.end.iloc[0] < windows.start.iloc[1]


def test_runner_on_fixtures_and_output_guard(tmp_path, monkeypatch):
    result = run_backtest_b.main(["--fixture-case", "multi", "--latency-s", "60", "--output-dir", str(tmp_path/"syn")])
    assert (tmp_path/"syn/backtest_b_L60s_ledger.parquet").is_file() and not (tmp_path/"syn/trades_ledger.parquet").exists()
    assert (tmp_path/"syn/backtest_b_L60s_by_market.csv").is_file() and result.params["data_kind"] == "synthetic"
    root = tmp_path/"repo"
    target = root/"data/processed/probe"
    target.mkdir(parents=True)
    (target/"trades_ledger.parquet").write_bytes(b"existing ledger")
    monkeypatch.setattr(run_backtest_b, "ROOT", root)
    with pytest.raises(ValueError, match="Synthetic"):
        run_backtest_b.main(["--quotes", str(ROOT/"data/fixtures/multi/live_quotes_long.parquet"), "--latency-s", "60",
                             "--config", str(ROOT/"config/config.yaml"), "--fees", str(ROOT/"config/fees.yaml"),
                             "--output-dir", str(target)])
    assert (target/"trades_ledger.parquet").read_bytes() == b"existing ledger" and len(list(target.iterdir())) == 1
