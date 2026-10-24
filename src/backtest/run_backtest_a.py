"""TV6 C6.04 runner: D3 panel (dev/val split) + live D2 half-spread calibration -> backtest A tables.

Real data (historical panel, half spread calibrated on live D2; development split by default):
    python -m src.backtest.run_backtest_a
    python -m src.backtest.run_backtest_a --split val --latency-min 2
Fixtures (synthetic panel + synthetic D2, split built in memory, outputs under outputs/synthetic/):
    python -m src.backtest.run_backtest_a --fixture-case multi
The final test split is never read here; C6.07 claims it once through claim_final_test.
Outputs (prefix backtest_a_{split}_L{L}): ledger .parquet (D8 columns), summary .csv, params .json,
plus half_spread_calibration.csv.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.cost_model.costs import load_fees
from src.synchronization.split import create_split_manifest, select_split
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json, write_parquet
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind
from .calibration import estimate_half_spread_bps, half_spread_map
from .engine import BacktestConfig, run_backtest

ROOT = Path(__file__).resolve().parents[2]
CONFIG_KEY = "backtest_a"
PAIRED_KIND = {"historical": "live", "synthetic": "synthetic"}   # panel kind -> required calibration source kind


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--panel", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--live-quotes", type=Path)
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"])
    parser.add_argument("--split", choices=["dev", "val"], default="dev")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--latency-min", type=int, help="overrides backtest_a.latency_min")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    params = load_config(args.config).get(CONFIG_KEY)
    if not params:
        raise ValueError(f"{args.config} needs a {CONFIG_KEY} section")
    fixture = ROOT/"data/fixtures"/args.fixture_case if args.fixture_case else None
    panel_path = args.panel or (fixture/"panel_1m_wide.parquet" if fixture else ROOT/"data/processed/panel_1m_wide.parquet")
    quotes_path = args.live_quotes or (fixture/"live_quotes_long.parquet" if fixture else ROOT/"data/processed/live_quotes_long.parquet")
    panel = read_parquet(panel_path, "D3")
    kind = data_kind(panel)
    if kind not in PAIRED_KIND:
        raise ValueError(f"Backtest A needs a historical or synthetic panel, got {kind}")
    if kind == "synthetic" and args.manifest is None:
        manifest = create_split_manifest(panel)                  # in memory only; fixtures have no D4 on disk
        manifest_path = None
    else:
        manifest_path = args.manifest or ROOT/"data/processed/split_manifest.json"
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    sample = select_split(panel, manifest, args.split)          # checks the D4 fingerprint; refuses the test split

    quotes = read_parquet(quotes_path, "D2")
    if data_kind(quotes) != PAIRED_KIND[kind]:
        raise ValueError(f"A {kind} panel must be calibrated on {PAIRED_KIND[kind]} quotes, got {data_kind(quotes)}")
    venues = [str(c)[len("close__"):] for c in sample if str(c).startswith("close__")]
    calibration = estimate_half_spread_bps(quotes, venues, params.get("half_spread_statistic", "median"))

    latency = args.latency_min if args.latency_min is not None else params["latency_min"]

    # Destination check BEFORE any computation or write: the shared TV4 rule keeps synthetic results out of the research
    # zone (outputs/ except outputs/synthetic/, data/raw|interim|processed) and research results out of synthetic/fixtures,
    # including sub-folders, ".." and aliases, for the folder and for every file it will receive.
    output = Path(args.output_dir or (ROOT/"outputs/synthetic"/(args.fixture_case or "custom")/"tables" if kind == "synthetic"
                                      else ROOT/"outputs/tables"))
    stem = f"backtest_a_{args.split}_L{latency}"
    ledger_path, summary_path = output/f"{stem}_ledger.parquet", output/f"{stem}_summary.csv"
    calibration_path, params_path = output/"half_spread_calibration.csv", output/f"{stem}_params.json"
    for destination in (output, ledger_path, summary_path, calibration_path, params_path):
        validate_output_location(destination, root=ROOT, mode="synthetic" if kind == "synthetic" else "historical")
    cfg = BacktestConfig(fees=load_fees(args.fees), half_spread_bps=half_spread_map(calibration), latency_min=latency,
                         entry_threshold_bps=float(params["entry_threshold_bps"]), qty_btc=float(params["qty_btc"]),
                         fee_type=params.get("fee_type", "taker"))
    result = run_backtest(sample, cfg)

    write_parquet(result.ledger, ledger_path, "D8" if len(result.ledger) else None)
    result.summary.to_csv(summary_path, index=False)
    calibration.to_csv(calibration_path, index=False)
    run = dict(result.params, split=args.split, panel=str(panel_path), panel_sha256=file_sha256(panel_path),
               manifest=str(manifest_path) if manifest_path else "in_memory", panel_fingerprint=manifest["fingerprint"],
               live_quotes=str(quotes_path), live_quotes_sha256=file_sha256(quotes_path),
               note="Backtest A is a proxy on 1-minute closes plus a calibrated half spread; not executable prices.")
    write_json(run, params_path)
    filled = result.ledger[result.ledger["status"] == "filled"]
    print(f"{stem}: {len(result.ledger)} signals, {len(filled)} filled, {int((filled['net_bps'] > 0).sum())} net>0, "
          f"PnL {filled['pnl_usdt'].sum():.2f} USDT -> {output}")
    return result


if __name__ == "__main__":
    main()
