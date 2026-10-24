"""TV6 C6.08 runner: F4 (gap duration) and F5 (cumulative P&L) from the official result tables.

    python -m src.backtest.make_figures [--theme light|dark]
Reads outputs/tables/gap_events.parquet (live), gap_events_historical.parquet, the backtest A ledger (dev, base latency)
and trades_ledger.parquet (backtest B). Writes outputs/figures/F4_gap_duration.png and F5_cumulative_pnl.png after the
shared output-location check; synthetic inputs go to outputs/synthetic/figures/.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.utils.config import load_config
from src.utils.io_utils import read_parquet
from src.utils.pipeline_stages import validate_output_location
from .figures import plot_cumulative_pnl, plot_gap_duration

ROOT = Path(__file__).resolve().parents[2]


def _kind(frame):
    return frame["data_kind"].iloc[0] if len(frame) else frame.attrs.get("data_kind")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tables", type=Path, default=ROOT/"outputs/tables")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--theme", choices=["light", "dark"], default="light")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    gap, a_cfg, b_cfg = cfg["gap_events"], cfg["backtest_a"], cfg["backtest_b"]
    tables = Path(args.tables)
    events_hist = read_parquet(tables/"gap_events_historical.parquet", "D7")
    events_live = read_parquet(tables/"gap_events.parquet", "D7")
    a_stem = f"backtest_a_dev_L{int(a_cfg['latency_min'])}"
    ledger_a, ledger_b = read_parquet(tables/f"{a_stem}_ledger.parquet"), read_parquet(tables/"trades_ledger.parquet")
    params_a = json.loads((tables/f"{a_stem}_params.json").read_text(encoding="utf-8"))
    params_b = json.loads((tables/f"backtest_b_L{float(b_cfg['latency_s']):g}s_params.json").read_text(encoding="utf-8"))
    kinds = {_kind(f) for f in (events_hist, events_live, ledger_a, ledger_b)}
    synthetic = kinds == {"synthetic"}
    if synthetic is False and "synthetic" in kinds:
        raise ValueError(f"Do not mix synthetic and research inputs in one figure: {sorted(map(str, kinds))}")
    output = Path(args.output_dir or (ROOT/"outputs/synthetic/figures" if synthetic else ROOT/"outputs/figures"))
    f4, f5 = output/"F4_gap_duration.png", output/"F5_cumulative_pnl.png"
    for destination in (output, f4, f5):
        validate_output_location(destination, root=ROOT, mode="synthetic" if synthetic else "historical")

    summary = plot_gap_duration(
        {"Lịch sử (nến 1 phút, tập phát triển)": (events_hist, float(gap["grid_step_s"])),
         "Live (bid/ask thật, 13 phiên)": (events_live, float(gap["live_grid_step_s"]))},
        float(gap["gap_threshold_bps"]), f4, args.theme)
    totals = plot_cumulative_pnl(
        {f"Backtest A · proxy close 1 phút, độ trễ {a_cfg['latency_min']} phút": (ledger_a, (pd.Timestamp(params_a["first_ts"]), pd.Timestamp(params_a["last_ts"])), float(a_cfg["qty_btc"])),
         f"Backtest B · live bid/ask, độ trễ {b_cfg['latency_s']:g} s": (ledger_b, (pd.Timestamp(params_b["first_ts"]), pd.Timestamp(params_b["last_ts"])), float(b_cfg["qty_btc"]))},
        f5, args.theme)
    for row in summary:
        print("F4", row)
    for key, value in totals.items():
        print("F5", key, round(value, 2))
    print(f"-> {f4}\n-> {f5}")


if __name__ == "__main__":
    main()
