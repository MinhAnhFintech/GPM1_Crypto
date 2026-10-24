"""TV6 C6.06: T5 cost waterfall (gross -> fees -> spread -> slippage -> latency -> net), spot-spot and perp-perp apart.

Stages are applied in the order of the assignment: gross (mid gap), fees, spread, slippage, latency.

For every FILLED signal the realised net is split into additive stages that never overlap (each cost is
counted once):
    mid_gap_bps       gross: gap between the two mids at the decision (closes for backtest A, (bid+ask)/2 for B)
  - fee_bps           = fee_buy + fee_sell (taker)
  - spread_bps        = mid_gap - decision gross (crossing to the touch: half spread per leg;
                        A: calibrated assumption, B: real quoted spread)
  - slippage_bps      = slippage + extra cost (0 in the base scenario)
  - latency_bps       = decision net - realised net (price move and spread change during the L delay)
  = net_bps           realised net at the fill
so mid_gap - fee - spread - slippage - latency == net exactly. The table reports stage means (bps), how many
filled signals are still positive after each stage, rejections and PnL, one row per backtest and market_type.
The two backtests stay on separate rows (historical proxy vs live sample); they are never pooled.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STAGES = ["mid_gap_bps", "fee_bps", "spread_bps", "slippage_bps", "latency_bps", "net_bps"]
T5_COLUMNS = ["backtest", "method", "data_kind", "market_type", "latency", "qty_btc", "n_signals", "n_filled", "n_rejected",
              "mean_mid_gap_bps", "mean_fee_bps", "mean_spread_bps", "mean_slippage_bps", "mean_latency_bps", "mean_net_bps",
              "n_positive_mid_gap", "n_positive_after_fees", "n_positive_after_spread", "n_positive_after_slippage",
              "n_positive_net", "total_fees_usdt", "total_pnl_usdt", "rejected_by_reason"]
TOLERANCE_BPS = 1e-6
BPS = 10_000.0


def decompose(ledger: pd.DataFrame) -> pd.DataFrame:
    """Per filled signal: the additive stages above (bps one-way, relative to the buy price).

    The stages add up by construction, so consistency is checked against INDEPENDENT ledger fields; any mismatch
    means a cost would be missing or counted twice and raises:
      * slippage stage == other_cost_usdt converted back to bps of the buy notional;
      * fill side: gross_bps - fee - slippage == net_bps;
      * pnl_usdt == net_bps / 10_000 * qty_btc * buy_px.
    """
    filled = ledger[ledger["status"] == "filled"]
    notional = filled["qty_btc"] * filled["buy_px"]
    parts = pd.DataFrame({"market_type": filled["market_type"],
                          "mid_gap_bps": filled["decision_mid_gap_bps"],
                          "fee_bps": filled["fee_total_bps"],
                          "spread_bps": filled["decision_mid_gap_bps"] - filled["decision_gross_bps"],
                          "slippage_bps": filled["decision_gross_bps"] - filled["fee_total_bps"] - filled["decision_net_bps"],
                          "latency_bps": filled["decision_net_bps"] - filled["net_bps"],
                          "net_bps": filled["net_bps"]})
    checks = {
        "slippage stage vs other_cost_usdt": parts.slippage_bps - filled["other_cost_usdt"] / notional * BPS,
        "fill gross - fees - slippage vs net": filled["gross_bps"] - parts.fee_bps - parts.slippage_bps - filled["net_bps"],
        "pnl_usdt vs net_bps": (filled["pnl_usdt"] - filled["net_bps"] / BPS * notional) / notional * BPS,
        "stages vs net": parts.mid_gap_bps - parts.fee_bps - parts.spread_bps - parts.slippage_bps - parts.latency_bps - parts.net_bps,
    }
    broken = [name for name, residual in checks.items() if (residual.abs() > TOLERANCE_BPS).any()]
    if broken:
        raise ValueError(f"Ledger is internally inconsistent ({', '.join(broken)}); a cost would be missing or double counted")
    return parts


def cost_waterfall(ledger: pd.DataFrame, backtest: str, latency: str) -> pd.DataFrame:
    """T5 rows for one backtest ledger (one row per market_type present in the run, spot and perp apart)."""
    parts = decompose(ledger)
    method = ledger["method"].iloc[0] if len(ledger) else ledger.attrs.get("method")
    kind = ledger["data_kind"].iloc[0] if len(ledger) else ledger.attrs.get("data_kind")
    rows = []
    for market_type in ("spot", "perp"):
        signals = ledger[ledger["market_type"] == market_type]
        p = parts[parts["market_type"] == market_type]
        after = p.mid_gap_bps.copy()
        positive = {"n_positive_mid_gap": int((after > 0).sum())}
        for stage, name in (("fee_bps", "after_fees"), ("spread_bps", "after_spread"), ("slippage_bps", "after_slippage")):
            after = after - p[stage]
            positive[f"n_positive_{name}"] = int((after > 0).sum())
        rejected = signals[signals["status"] == "rejected"]["reject_reason"].value_counts().to_dict()
        rows.append({"backtest": backtest, "method": method, "data_kind": kind, "market_type": market_type, "latency": latency,
                     "qty_btc": float(ledger["qty_btc"].iloc[0]) if len(ledger) else np.nan,
                     "n_signals": len(signals), "n_filled": len(p), "n_rejected": int((signals["status"] == "rejected").sum()),
                     **{f"mean_{s}": float(p[s].mean()) if len(p) else np.nan for s in STAGES},
                     **positive, "n_positive_net": int((p.net_bps > 0).sum()),
                     "total_fees_usdt": float(signals.loc[signals.status == "filled", "fees_usdt"].sum()),
                     "total_pnl_usdt": float(signals.loc[signals.status == "filled", "pnl_usdt"].sum()),
                     "rejected_by_reason": ";".join(f"{k}={v}" for k, v in sorted(rejected.items()))})
    return pd.DataFrame(rows, columns=T5_COLUMNS)
