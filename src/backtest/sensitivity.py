"""TV6 C6.07: T5b cost sensitivity - fees x latency x size (slippage for the proxy).

Every scenario re-runs the SAME engines with one changed input, so the table isolates the effect of that cost:
  * fee multiplier m: every venue's taker fee x m (1 = schedule, 0.5 = deep discount, 0 = no fee: the gross upper bound);
  * latency: minutes for backtest A, seconds for backtest B;
  * size: backtest A is a close proxy without depth, so size only scales P&L; its size dimension is a slippage
    allowance (bps per trade). Backtest B observes the touch size, so size there is real (a fill larger than the
    touch is rejected).
Nothing is chosen from these results: the grid and all base settings are fixed beforehand in a frozen file, and
backtest A is evaluated on the final test exactly once (see run_t5b).
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from src.cost_model.costs import FeeSchedule
from .engine import BacktestConfig, run_backtest
from .engine_live import LiveBacktestConfig, run_backtest_live

T5B_COLUMNS = ["backtest", "sample", "market_type", "fee_multiplier", "latency", "slippage_bps", "qty_btc", "is_base_scenario",
               "n_signals", "n_filled", "n_rejected", "n_net_positive", "share_net_positive", "mean_net_bps", "median_net_bps",
               "total_pnl_usdt", "total_fees_usdt", "rejected_by_reason"]
REQUIRED = {"base": {"entry_threshold_bps", "fee_type", "half_spread_statistic", "live_grid_step_s", "live_max_age_s",
                     "base_latency_min", "base_latency_s", "base_qty_btc"},
            "grid": {"fee_multipliers", "backtest_a", "backtest_b"}}


def validate_frozen(frozen: dict) -> dict:
    """Check the frozen settings file has every key the evaluation needs (nothing falls back to config.yaml)."""
    for section, keys in REQUIRED.items():
        missing = keys - set(frozen.get(section) or {})
        if missing:
            raise ValueError(f"frozen settings: {section} lacks {sorted(missing)}")
    grid = frozen["grid"]
    if not {"latency_min", "slippage_bps", "qty_btc"} <= set(grid["backtest_a"]):
        raise ValueError("frozen settings: grid.backtest_a needs latency_min, slippage_bps, qty_btc")
    if not {"latency_s", "qty_btc"} <= set(grid["backtest_b"]):
        raise ValueError("frozen settings: grid.backtest_b needs latency_s, qty_btc")
    if any(m < 0 for m in grid["fee_multipliers"]):
        raise ValueError("fee multipliers must be >= 0")
    return frozen


def scale_fees(fees: FeeSchedule, multiplier: float) -> FeeSchedule:
    """Same schedule with every taker fee x multiplier (maker is not used in T5b and is dropped)."""
    venues = {venue: {"taker_bps": spec["taker_bps"] * multiplier} for venue, spec in fees.venues.items()}
    return FeeSchedule(venues=venues, slippage_bps=fees.slippage_bps, extra_cost_bps=fees.extra_cost_bps,
                       assumed_half_spread_bps=fees.assumed_half_spread_bps)


def summarise(ledger: pd.DataFrame, scenario: dict) -> list:
    """One row per market_type (spot, perp) for one scenario."""
    rows = []
    for market_type in ("spot", "perp"):
        part = ledger[ledger["market_type"] == market_type]
        filled = part[part["status"] == "filled"]
        reasons = part[part["status"] == "rejected"]["reject_reason"].value_counts().to_dict()
        rows.append({**scenario, "market_type": market_type, "n_signals": len(part), "n_filled": len(filled),
                     "n_rejected": int((part["status"] == "rejected").sum()), "n_net_positive": int((filled["net_bps"] > 0).sum()),
                     "share_net_positive": float((filled["net_bps"] > 0).mean()) if len(filled) else np.nan,
                     "mean_net_bps": float(filled["net_bps"].mean()) if len(filled) else np.nan,
                     "median_net_bps": float(filled["net_bps"].median()) if len(filled) else np.nan,
                     "total_pnl_usdt": float(filled["pnl_usdt"].sum()), "total_fees_usdt": float(filled["fees_usdt"].sum()),
                     "rejected_by_reason": ";".join(f"{k}={v}" for k, v in sorted(reasons.items()))})
    return rows


def grid_backtest_a(sample: pd.DataFrame, fees: FeeSchedule, half_spread: dict, frozen: dict, sample_name: str) -> pd.DataFrame:
    """Backtest A over fee multiplier x latency (minutes) x slippage (bps) on one split of the panel."""
    base, grid = frozen["base"], frozen["grid"]
    rows = []
    for m, latency, slippage in itertools.product(grid["fee_multipliers"], grid["backtest_a"]["latency_min"],
                                                  grid["backtest_a"]["slippage_bps"]):
        qty = float(grid["backtest_a"]["qty_btc"])
        cfg = BacktestConfig(fees=scale_fees(fees, m), half_spread_bps=half_spread, latency_min=int(latency),
                             entry_threshold_bps=float(base["entry_threshold_bps"]), qty_btc=qty,
                             fee_type=base["fee_type"], slippage_bps=float(slippage), extra_cost_bps=0.0)
        scenario = {"backtest": "A_proxy_close_1m", "sample": sample_name, "fee_multiplier": float(m), "latency": f"{int(latency)}min",
                    "slippage_bps": float(slippage), "qty_btc": qty,
                    "is_base_scenario": bool(m == 1 and int(latency) == int(base["base_latency_min"]) and float(slippage) == 0
                                             and qty == float(base["base_qty_btc"]))}
        rows += summarise(run_backtest(sample, cfg).ledger, scenario)
    return pd.DataFrame(rows, columns=T5B_COLUMNS)


def grid_backtest_b(wide: pd.DataFrame, sessions: pd.DataFrame, fees: FeeSchedule, frozen: dict) -> pd.DataFrame:
    """Backtest B over fee multiplier x latency (seconds) x size (BTC) on the whole live sample."""
    base, grid = frozen["base"], frozen["grid"]
    rows = []
    for m, latency, qty in itertools.product(grid["fee_multipliers"], grid["backtest_b"]["latency_s"], grid["backtest_b"]["qty_btc"]):
        cfg = LiveBacktestConfig(fees=scale_fees(fees, m), grid_step_s=float(base["live_grid_step_s"]), latency_s=float(latency),
                                 entry_threshold_bps=float(base["entry_threshold_bps"]), qty_btc=float(qty), fee_type=base["fee_type"],
                                 slippage_bps=0.0, extra_cost_bps=0.0,
                                 require_top_size=bool(grid["backtest_b"].get("require_top_size", True)))
        scenario = {"backtest": "B_live_bidask", "sample": "live", "fee_multiplier": float(m), "latency": f"{float(latency):g}s",
                    "slippage_bps": 0.0, "qty_btc": float(qty),
                    "is_base_scenario": bool(m == 1 and float(latency) == float(base["base_latency_s"]) and float(qty) == float(base["base_qty_btc"]))}
        rows += summarise(run_backtest_live(wide, cfg, sessions).ledger, scenario)
    return pd.DataFrame(rows, columns=T5B_COLUMNS)
