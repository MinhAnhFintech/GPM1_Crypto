"""C6.10: the trades ledger must agree with the balance movements its legs imply (USDT, BTC, P&L, fees)."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.backtest import reconcile
from src.backtest.engine import BacktestConfig, run_backtest
from src.backtest.engine_live import LiveBacktestConfig, run_backtest_live
from src.backtest.reconcile import check_structure, leg_movements, reconcile_ledger
from src.cost_model.costs import FeeSchedule

ROOT = Path(__file__).resolve().parents[1]
T0 = pd.Timestamp("2026-01-01T00:01Z")
VENUES = ["binance_spot", "okx_spot", "binance_perp", "okx_perp"]
FEES = FeeSchedule(venues={"binance_spot": {"taker_bps": 10.0}, "okx_spot": {"taker_bps": 8.0},
                           "binance_perp": {"taker_bps": 5.0}, "okx_perp": {"taker_bps": 5.5}}, slippage_bps=1.0, extra_cost_bps=0.5)


FEES_YAML = """binance:
  spot: {taker_bps: 10.0}
  perp: {taker_bps: 5.0}
okx:
  spot: {taker_bps: 8.0}
  perp: {taker_bps: 5.5}
defaults: {slippage_bps: 1.0, extra_cost_bps: 0.5}
"""


def proxy_ledger():
    index = pd.date_range(T0, periods=6, freq="1min", name="ts_available").as_unit("ns")
    p = pd.DataFrame({"close__binance_spot": [100.0]*6, "close__okx_spot": [100.4, 100.1, 100.0, 100.4, 100.2, 100.4],
                      "close__binance_perp": [200.0]*6, "close__okx_perp": [200.6, 200.5, 200.0, 200.6, 200.1, 200.0]}, index=index)
    p.attrs["data_kind"] = "synthetic"
    return run_backtest(p, BacktestConfig(fees=FEES, half_spread_bps={v: 0.5 for v in VENUES}, qty_btc=0.1)).ledger


def live_ledger():
    index = pd.date_range("2026-10-21T13:00Z", periods=4, freq="5s", name="ts_available").as_unit("ns")
    w = pd.DataFrame(index=index)
    rows = {"binance_perp": [(200.0, 200.02)]*4, "okx_perp": [(200.5, 200.52), (200.1, 200.12), (200.0, 200.02), (200.0, 200.02)]}
    for v, values in rows.items():
        w[f"bid__{v}"], w[f"ask__{v}"] = np.array(values).T
        w[f"bid_size__{v}"] = w[f"ask_size__{v}"] = 1.0
        w[f"quote_age_s__{v}"] = 1.0
    w.attrs["data_kind"] = "synthetic"
    sessions = pd.DataFrame({"session_id": ["S"], "start": [index[0]], "end": [index[-1]]})
    return run_backtest_live(w, LiveBacktestConfig(fees=FEES, qty_btc=0.1), sessions).ledger


@pytest.mark.parametrize("ledger_fn, name", [(proxy_ledger, "A"), (live_ledger, "B")])
def test_engine_ledgers_reconcile_with_balances(ledger_fn, name):
    ledger = ledger_fn()
    assert (ledger.status == "filled").sum() > 0
    table, per_venue, legs = reconcile_ledger(ledger, FEES, name)
    total = table[table.market_type == "total"].iloc[0]
    assert total.status == "ok" and total.n_filled == (ledger.status == "filled").sum()
    assert abs(total.btc_net_change) < 1e-12                                  # the two legs trade the same quantity
    assert abs(total.unexplained_usdt) < 1e-9 and total.max_abs_signal_unexplained_usdt < 1e-9
    assert total.balance_pnl_usdt == pytest.approx(total.ledger_pnl_usdt + total.explained_by_sell_fee_notional_usdt)
    assert per_venue.btc_change.sum() == pytest.approx(0, abs=1e-12) and len(legs) == 2*total.n_filled
    # spot and perp rows add up to the total row; the two groups are never mixed
    assert table[table.market_type != "total"].n_filled.sum() == total.n_filled
    assert table[table.market_type != "total"].balance_pnl_usdt.sum() == pytest.approx(total.balance_pnl_usdt)


def test_hand_computed_balance_movement():
    qty, buy, sell = 0.2, 100.0, 100.5
    ledger = pd.DataFrame([{"signal_id": "s1", "ts_decision": T0, "ts_fill": T0 + pd.Timedelta(minutes=1), "market_type": "spot",
                            "buy_exchange": "binance", "sell_exchange": "okx", "qty_btc": qty, "buy_px": buy, "sell_px": sell,
                            "buy_venue": "binance_spot", "sell_venue": "okx_spot", "status": "filled", "reject_reason": None,
                            "other_cost_usdt": 0.0}])
    fee_buy, fee_sell = 10.0/1e4*qty*buy, 8.0/1e4*qty*sell                       # fee on each leg's own notional
    expected_pnl = qty*(sell - buy) - fee_buy - fee_sell
    ledger["fees_usdt"] = (10.0 + 8.0)/1e4*qty*buy                                # the ledger charges both on the buy notional
    ledger["pnl_usdt"] = qty*(sell - buy) - ledger.fees_usdt
    table, per_venue, _ = reconcile_ledger(ledger, FEES, "hand")
    total = table[table.market_type == "total"].iloc[0]
    assert total.balance_pnl_usdt == pytest.approx(expected_pnl) and total.status == "ok"
    assert total.difference_usdt == pytest.approx(-8.0/1e4*qty*(sell - buy))     # exactly the documented term
    usdt = dict(zip(per_venue.venue, per_venue.usdt_change))
    assert usdt["binance_spot"] == pytest.approx(-(qty*buy + fee_buy)) and usdt["okx_spot"] == pytest.approx(qty*sell - fee_sell)
    assert dict(zip(per_venue.venue, per_venue.btc_change)) == {"binance_spot": qty, "okx_spot": -qty}


def test_tampered_pnl_is_detected():
    ledger = proxy_ledger()
    ledger.loc[ledger.status == "filled", "pnl_usdt"] *= 0.5
    table, _, _ = reconcile_ledger(ledger, FEES, "A")
    total = table[table.market_type == "total"].iloc[0]
    assert total.status == "MISMATCH" and abs(total.unexplained_usdt) > 1e-3


def test_tampered_price_is_detected():
    ledger = proxy_ledger()
    ledger.loc[ledger.index[ledger.status == "filled"][0], "sell_px"] += 5.0         # price moved, pnl untouched
    table, _, _ = reconcile_ledger(ledger, FEES, "A")
    assert table[table.market_type == "total"].iloc[0].status == "MISMATCH"


def test_rejected_signal_that_moved_money_is_detected():
    ledger = proxy_ledger()
    assert (ledger.status == "rejected").any()                                      # the last signals have no fill minute
    ledger.loc[ledger.status == "rejected", "fees_usdt"] = 1.0
    table, _, _ = reconcile_ledger(ledger, FEES, "A")
    total = table[table.market_type == "total"].iloc[0]
    assert total.rejected_with_money_moved > 0 and total.status == "MISMATCH"


def test_structure_checks():
    ledger = proxy_ledger()
    assert check_structure(ledger) == []
    cross = ledger.copy()
    cross.loc[cross.index[cross.market_type == "spot"][0], "sell_venue"] = "okx_perp"      # a spot buy sold on a perp venue
    assert "pairs spot with perp" in " ".join(check_structure(cross))
    early = ledger.copy()
    idx = early.index[early.status == "filled"][0]
    early.loc[idx, "ts_fill"] = early.loc[idx, "ts_decision"] - pd.Timedelta(seconds=1)
    assert "precedes" in " ".join(check_structure(early))
    duplicate = pd.concat([ledger, ledger.iloc[[0]]], ignore_index=True)
    assert "duplicate" in " ".join(check_structure(duplicate))
    selfie = ledger.copy()
    selfie.loc[selfie.index[0], "sell_venue"] = selfie.loc[selfie.index[0], "buy_venue"]
    assert "itself" in " ".join(check_structure(selfie))


def test_legs_follow_buy_then_sell_and_other_costs_sit_on_the_buy_leg():
    ledger = proxy_ledger()
    legs = leg_movements(ledger, FEES)
    first = legs[legs.signal_id == legs.signal_id.iloc[0]]
    assert list(first.side) == ["buy", "sell"] and first.btc_change.sum() == pytest.approx(0)
    assert first.other_cost_usdt.iloc[0] > 0 and first.other_cost_usdt.iloc[1] == 0     # slippage + extra cost, once


def test_empty_ledger_reconciles_trivially():
    ledger = proxy_ledger().iloc[0:0]
    table, per_venue, legs = reconcile_ledger(ledger, FEES, "empty")
    assert list(table.status.unique()) == ["ok"] and per_venue.empty and legs.empty


def test_cli_writes_tables_and_guards_the_destination(tmp_path):
    a, b = tmp_path/"a.parquet", tmp_path/"b.parquet"
    proxy_ledger().to_parquet(a)
    live_ledger().to_parquet(b)
    fees = tmp_path/"fees.yaml"                       # the schedule the ledgers were built with; the repo's would (rightly) mismatch
    fees.write_text(FEES_YAML, encoding="utf-8")
    result = reconcile.main(["--a-ledger", str(a), "--b-ledger", str(b), "--fees", str(fees), "--output-dir", str(tmp_path/"out")])
    assert (tmp_path/"out/T5c_ledger_reconciliation.csv").is_file() and (tmp_path/"out/T5c_ledger_balances.csv").is_file()
    assert set(result.status) == {"ok"}
    live_ledger().assign(data_kind="live").to_parquet(tmp_path/"real.parquet")
    with pytest.raises(ValueError, match="mix"):
        reconcile.main(["--a-ledger", str(a), "--b-ledger", str(tmp_path/"real.parquet"), "--fees", str(fees), "--output-dir", str(tmp_path/"x")])
    with pytest.raises(ValueError, match="Synthetic"):
        reconcile.main(["--a-ledger", str(a), "--b-ledger", str(b), "--fees", str(fees), "--output-dir", str(ROOT/"outputs/tables/probe_recon")])
    assert not (ROOT/"outputs/tables/probe_recon").exists()


def test_a_different_fee_schedule_is_reported_as_mismatch(tmp_path):
    """Reconciling against fees other than the ones the ledger was built with must not pass silently."""
    other = FeeSchedule(venues={**FEES.venues, "okx_spot": {"taker_bps": 20.0}, "okx_perp": {"taker_bps": 9.0}})
    for ledger in (proxy_ledger(), live_ledger()):
        table, _, _ = reconcile_ledger(ledger, other, "x")
        assert table[table.market_type == "total"].iloc[0].status == "MISMATCH"
