"""TV6 C6.10: reconcile a trades ledger (D8) against the balance movements its two legs imply.

Capital is pre-positioned on every venue, so each FILLED signal moves balances like this (per BTC unit qty):
    buy venue :  USDT -= qty * buy_px  * (1 + fee_buy/10_000)      BTC += qty
    sell venue:  USDT += qty * sell_px * (1 - fee_sell/10_000)      BTC -= qty
    slippage + extra cost (other_cost_usdt) is an extra USDT outflow booked on the buy venue.
The balance-based P&L (sum of USDT changes; BTC must net to zero because the two legs are the same quantity) is then
compared with the ledger's own pnl_usdt. This check is independent of the engine's bps formulas: it uses prices,
quantities and per-venue fees only. Rejected signals must not move any balance.

Known, documented difference: the ledger charges BOTH fees in bps of the BUY notional (the contract defines every bps
quantity relative to the buy price), while an exchange charges the sell-leg fee on the SELL notional. Per signal that
is exactly  fee_sell_bps/10_000 * qty * (sell_px - buy_px)  USDT; the table reports it so it is never hidden.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.cost_model.costs import BPS_PER_UNIT, DEFAULT_FEE_TYPE, FeeSchedule

TOLERANCE_USDT = 1e-9
RECON_COLUMNS = ["backtest", "market_type", "n_signals", "n_filled", "n_rejected", "ledger_pnl_usdt", "balance_pnl_usdt",
                 "difference_usdt", "explained_by_sell_fee_notional_usdt", "unexplained_usdt", "max_abs_signal_unexplained_usdt",
                 "ledger_fees_usdt", "balance_fees_usdt", "btc_net_change", "rejected_with_money_moved", "status"]
VENUE_COLUMNS = ["backtest", "venue", "usdt_change", "btc_change", "n_legs"]


def leg_movements(ledger: pd.DataFrame, fees: FeeSchedule, fee_type: str = DEFAULT_FEE_TYPE) -> pd.DataFrame:
    """One row per leg of every FILLED signal: venue, USDT and BTC change, and the fee paid on that leg (USDT)."""
    filled = ledger[ledger["status"] == "filled"]
    rows = []
    for signal in filled.itertuples():
        fee_buy, fee_sell = fees.fee_bps(signal.buy_venue, fee_type), fees.fee_bps(signal.sell_venue, fee_type)
        buy_fee = signal.qty_btc * signal.buy_px * fee_buy / BPS_PER_UNIT
        sell_fee = signal.qty_btc * signal.sell_px * fee_sell / BPS_PER_UNIT
        rows.append({"signal_id": signal.signal_id, "market_type": signal.market_type, "venue": signal.buy_venue, "side": "buy",
                     "usdt_change": -(signal.qty_btc * signal.buy_px + buy_fee + signal.other_cost_usdt),
                     "btc_change": signal.qty_btc, "fee_usdt": buy_fee, "other_cost_usdt": signal.other_cost_usdt})
        rows.append({"signal_id": signal.signal_id, "market_type": signal.market_type, "venue": signal.sell_venue, "side": "sell",
                     "usdt_change": signal.qty_btc * signal.sell_px - sell_fee, "btc_change": -signal.qty_btc,
                     "fee_usdt": sell_fee, "other_cost_usdt": 0.0})
    return pd.DataFrame(rows, columns=["signal_id", "market_type", "venue", "side", "usdt_change", "btc_change", "fee_usdt",
                                       "other_cost_usdt"])


def check_structure(ledger: pd.DataFrame) -> list:
    """Structural problems of a ledger (empty list when clean): spot-perp legs, time order, rejected rows that moved money."""
    problems = []
    same_type = ledger["buy_venue"].str.rsplit("_", n=1).str[1] == ledger["sell_venue"].str.rsplit("_", n=1).str[1]
    if not same_type.all():
        problems.append("a signal pairs spot with perp")
    if ((ledger["buy_venue"].str.rsplit("_", n=1).str[1]) != ledger["market_type"]).any():
        problems.append("market_type disagrees with the venues")
    if (ledger["buy_venue"] == ledger["sell_venue"]).any():
        problems.append("a venue trades with itself")
    filled = ledger[ledger["status"] == "filled"]
    if (filled["ts_fill"] < filled["ts_decision"]).any() or filled["ts_fill"].isna().any():
        problems.append("a fill precedes its decision or has no fill time")
    if (filled[["buy_px", "sell_px", "qty_btc"]] <= 0).any().any():
        problems.append("a filled signal has a nonpositive price or quantity")
    if not ledger["signal_id"].is_unique:
        problems.append("duplicate signal_id")
    return problems


def reconcile_ledger(ledger: pd.DataFrame, fees: FeeSchedule, backtest: str, fee_type: str = DEFAULT_FEE_TYPE):
    """(table per market_type + total, per-venue balance changes, leg movements) for one ledger.

    status is 'ok' when the only gap between the ledger and the balances is the documented sell-fee-notional term
    and BTC nets to zero, no rejected signal moved money and the structure is clean; otherwise 'MISMATCH'.
    """
    problems = check_structure(ledger)
    legs = leg_movements(ledger, fees, fee_type)
    rejected = ledger[ledger["status"] == "rejected"]
    money_on_rejected = rejected[["pnl_usdt", "fees_usdt", "other_cost_usdt"]].abs().sum(axis=1) > 0
    rows = []
    for market_type in ("spot", "perp", "total"):
        scope = ledger if market_type == "total" else ledger[ledger["market_type"] == market_type]
        filled = scope[scope["status"] == "filled"]
        leg = legs[legs["signal_id"].isin(filled["signal_id"])]
        balance_pnl = float(leg["usdt_change"].sum())
        ledger_pnl = float(filled["pnl_usdt"].sum())
        # per signal: balance pnl vs ledger pnl, and the documented term
        per_signal = leg.groupby("signal_id")["usdt_change"].sum().reindex(filled["signal_id"]).to_numpy()
        diff = per_signal - filled["pnl_usdt"].to_numpy()
        explained = np.array([-(fees.fee_bps(r.sell_venue, fee_type) / BPS_PER_UNIT) * r.qty_btc * (r.sell_px - r.buy_px)
                              for r in filled.itertuples()])
        unexplained = diff - explained
        btc = float(leg["btc_change"].sum())
        moved = int(money_on_rejected[scope.index.intersection(rejected.index)].sum())
        ok = (not problems) and abs(btc) <= TOLERANCE_USDT and moved == 0 and (len(filled) == 0 or np.abs(unexplained).max() < 1e-7)
        rows.append({"backtest": backtest, "market_type": market_type, "n_signals": len(scope), "n_filled": len(filled),
                     "n_rejected": int((scope["status"] == "rejected").sum()), "ledger_pnl_usdt": ledger_pnl,
                     "balance_pnl_usdt": balance_pnl, "difference_usdt": balance_pnl - ledger_pnl,
                     "explained_by_sell_fee_notional_usdt": float(explained.sum()), "unexplained_usdt": float(unexplained.sum()),
                     "max_abs_signal_unexplained_usdt": float(np.abs(unexplained).max()) if len(unexplained) else 0.0,
                     "ledger_fees_usdt": float(filled["fees_usdt"].sum()), "balance_fees_usdt": float(leg["fee_usdt"].sum()),
                     "btc_net_change": btc, "rejected_with_money_moved": moved, "status": "ok" if ok else "MISMATCH"})
    table = pd.DataFrame(rows, columns=RECON_COLUMNS)
    per_venue = (legs.groupby("venue").agg(usdt_change=("usdt_change", "sum"), btc_change=("btc_change", "sum"),
                                           n_legs=("signal_id", "size")).reset_index().assign(backtest=backtest)[VENUE_COLUMNS]
                 if len(legs) else pd.DataFrame(columns=VENUE_COLUMNS))
    table.attrs["structure_problems"] = problems
    return table, per_venue, legs


RECON_NAME, BALANCES_NAME = "T5c_ledger_reconciliation.csv", "T5c_ledger_balances.csv"


def main(argv=None):
    """CLI: reconcile the base backtest A (dev) and B ledgers; writes T5c tables after the output-location check."""
    import argparse
    from pathlib import Path
    from src.cost_model.costs import load_fees
    from src.utils.config import load_config
    from src.utils.io_utils import read_parquet
    from src.utils.pipeline_stages import validate_output_location
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--a-ledger", type=Path)
    parser.add_argument("--b-ledger", type=Path, default=root/"outputs/tables/trades_ledger.parquet")
    parser.add_argument("--config", type=Path, default=root/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=root/"config/fees.yaml")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    a_path = args.a_ledger or root/f"outputs/tables/backtest_a_dev_L{int(cfg['backtest_a']['latency_min'])}_ledger.parquet"
    ledgers = {"A_proxy_close_1m_dev": read_parquet(a_path), "B_live_bidask": read_parquet(args.b_ledger)}
    kinds = {name: (l["data_kind"].iloc[0] if len(l) else l.attrs.get("data_kind")) for name, l in ledgers.items()}
    synthetic = {n for n, k in kinds.items() if k == "synthetic"}
    if synthetic and len(synthetic) != len(ledgers):
        raise ValueError(f"Do not mix synthetic and research ledgers in one table: {kinds}")
    output = Path(args.output_dir or (root/"outputs/synthetic/tables" if synthetic else root/"outputs/tables"))
    for destination in (output, output/RECON_NAME, output/BALANCES_NAME):
        validate_output_location(destination, root=root, mode="synthetic" if synthetic else "historical")
    fees = load_fees(args.fees)
    tables, balances = [], []
    for name, ledger in ledgers.items():
        table, per_venue, _ = reconcile_ledger(ledger, fees, name)
        tables.append(table)
        balances.append(per_venue)
    result = pd.concat(tables, ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    result.to_csv(output/RECON_NAME, index=False)
    pd.concat(balances, ignore_index=True).to_csv(output/BALANCES_NAME, index=False)
    print(result[["backtest", "market_type", "n_filled", "ledger_pnl_usdt", "balance_pnl_usdt", "difference_usdt",
                  "unexplained_usdt", "btc_net_change", "status"]].round(8).to_string(index=False))
    return result


if __name__ == "__main__":
    main()
