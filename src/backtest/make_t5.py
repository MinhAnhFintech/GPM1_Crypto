"""TV6 C6.06 runner: backtest A + backtest B ledgers -> outputs/tables/T5_arbitrage_net.csv.

    python -m src.backtest.make_t5
Defaults read the base runs: backtest A on the development split at backtest_a.latency_min, and backtest B
(outputs/tables/trades_ledger.parquet) at backtest_b.latency_s. A sources JSON next to the table records
each ledger's SHA-256 so the table can be traced to the exact runs.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json
from src.utils.pipeline_stages import validate_output_location
from .waterfall import cost_waterfall

ROOT = Path(__file__).resolve().parents[2]
T5_NAME = "T5_arbitrage_net.csv"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--a-ledger", type=Path, help="backtest A ledger (default: dev split at the configured latency)")
    parser.add_argument("--b-ledger", type=Path, help="backtest B ledger (default: outputs/tables/trades_ledger.parquet)")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    latency_a, latency_b = int(cfg["backtest_a"]["latency_min"]), float(cfg["backtest_b"]["latency_s"])
    a_path = args.a_ledger or ROOT/f"outputs/tables/backtest_a_dev_L{latency_a}_ledger.parquet"
    b_path = args.b_ledger or ROOT/"outputs/tables/trades_ledger.parquet"
    ledgers = {"A": read_parquet(a_path, None), "B": read_parquet(b_path, None)}
    kinds = {name: (l["data_kind"].iloc[0] if len(l) else l.attrs.get("data_kind")) for name, l in ledgers.items()}
    synthetic = {name for name, kind in kinds.items() if kind == "synthetic"}
    if synthetic and len(synthetic) != len(ledgers):
        raise ValueError(f"Do not mix synthetic and research ledgers in one table: {kinds}")
    output = Path(args.output_dir or (ROOT/"outputs/synthetic/tables" if synthetic else ROOT/"outputs/tables"))
    table_path, sources_path = output/T5_NAME, output/"T5_arbitrage_net_sources.json"
    for destination in (output, table_path, sources_path):
        validate_output_location(destination, root=ROOT, mode="synthetic" if synthetic else "historical")

    import pandas as pd
    t5 = pd.concat([cost_waterfall(ledgers["A"], "A_proxy_close_1m_dev", f"{latency_a}min"),
                    cost_waterfall(ledgers["B"], "B_live_bidask", f"{latency_b:g}s")], ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    t5.to_csv(table_path, index=False)
    write_json({"table": T5_NAME, "ledgers": {name: {"path": str(path), "sha256": file_sha256(path), "data_kind": kinds[name]}
                                              for name, path in (("A", a_path), ("B", b_path))},
                "note": "Stages add up exactly per filled signal: mid gap - fees - spread - slippage - latency = net."},
               sources_path)
    print(t5[["backtest", "market_type", "n_signals", "n_filled", "mean_mid_gap_bps", "mean_fee_bps", "mean_spread_bps",
              "mean_latency_bps", "mean_net_bps", "n_positive_net", "total_pnl_usdt"]].round(3).to_string(index=False))
    return t5


if __name__ == "__main__":
    main()
