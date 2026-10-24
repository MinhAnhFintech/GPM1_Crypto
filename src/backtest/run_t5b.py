"""TV6 C6.07 runner: T5b cost sensitivity, evaluated ONCE on the final test with frozen settings.

Preview on the validation split (allowed any number of times; nothing is locked):
    python -m src.backtest.run_t5b --split val
The single final-test evaluation (claims the test split; a second run is refused):
    python -m src.backtest.run_t5b --split test
Every setting comes from the frozen file (default config/frozen_t5b.yaml), never from config.yaml. Before the test is
claimed the runner checks the frozen input hashes (fees.yaml, live D2, panel fingerprint), the output destinations, and
runs the whole grid on the validation split as a preflight, so a code or input error stops BEFORE the test is consumed.
Backtest B uses the live sample (not part of the historical split) and is identical in both modes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.cost_model.costs import load_fees
from src.cost_model.run_gap_events import quotes_to_wide
from src.synchronization.split import claim_final_test, select_split, validate_panel_split
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind
from .calibration import estimate_half_spread_bps, half_spread_map
from .engine_live import session_windows
from .sensitivity import grid_backtest_a, grid_backtest_b, validate_frozen

ROOT = Path(__file__).resolve().parents[2]
NAMES = {"test": "T5b_cost_sensitivity.csv", "val": "T5b_cost_sensitivity_val_preview.csv"}


def check_frozen_inputs(frozen: dict, fees_path: Path, quotes_path: Path, panel_fingerprint: str) -> None:
    """Refuse to evaluate if the inputs differ from the ones named in the frozen file."""
    expected = frozen.get("inputs") or {}
    actual = {"fees_sha256": file_sha256(fees_path), "live_quotes_sha256": file_sha256(quotes_path),
              "panel_fingerprint": panel_fingerprint}
    wrong = {k: (expected.get(k), v) for k, v in actual.items() if str(expected.get(k, "")).lower() != v.lower()}
    if wrong:
        raise ValueError(f"Inputs differ from the frozen settings, refusing to evaluate: {wrong}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["val", "test"], default="val")
    parser.add_argument("--frozen", type=Path, default=ROOT/"config/frozen_t5b.yaml")
    parser.add_argument("--panel", type=Path, default=ROOT/"data/processed/panel_1m_wide.parquet")
    parser.add_argument("--manifest", type=Path, default=ROOT/"data/processed/split_manifest.json")
    parser.add_argument("--live-quotes", type=Path, default=ROOT/"data/processed/live_quotes_long.parquet")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    frozen = validate_frozen(load_config(args.frozen))
    base = frozen["base"]
    panel = read_parquet(args.panel, "D3")
    kind = data_kind(panel)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    validate_panel_split(panel, manifest)
    if args.split == "test" and (manifest.get("test_consumed") or Path(str(args.manifest) + ".test.lock").exists()):
        raise ValueError("The final test has already been consumed; T5b is evaluated only once")
    check_frozen_inputs(frozen, args.fees, args.live_quotes, manifest["fingerprint"])

    synthetic = kind == "synthetic"
    output = Path(args.output_dir or (ROOT/"outputs/synthetic/tables" if synthetic else ROOT/"outputs/tables"))
    table_path = output/NAMES[args.split]
    sources_path = table_path.with_name(table_path.stem + "_sources.json")
    for destination in (output, table_path, sources_path):
        validate_output_location(destination, root=ROOT, mode="synthetic" if synthetic else "historical")

    fees = load_fees(args.fees)
    quotes = read_parquet(args.live_quotes, "D2")
    venues = [str(c)[len("close__"):] for c in panel if str(c).startswith("close__")]
    calibration = estimate_half_spread_bps(quotes, venues, base["half_spread_statistic"])
    half_spread = half_spread_map(calibration)

    # Preflight on the validation split: the full grid must run before the final test can be claimed.
    val_table = grid_backtest_a(select_split(panel, manifest, "val"), fees, half_spread, frozen, "val")
    live_wide = quotes_to_wide(quotes, float(base["live_grid_step_s"]), float(base["live_max_age_s"]))
    b_table = grid_backtest_b(live_wide, session_windows(quotes), fees, frozen)

    if args.split == "test":
        test_rows = claim_final_test(panel, args.manifest, args.frozen)          # ONE-TIME: locks and marks test_consumed
        a_table = grid_backtest_a(test_rows, fees, half_spread, frozen, "test")
    else:
        a_table = val_table
    table = pd.concat([a_table, b_table], ignore_index=True)

    output.mkdir(parents=True, exist_ok=True)
    table.to_csv(table_path, index=False)
    final_manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    write_json({"table": table_path.name, "split": args.split, "frozen_settings": str(args.frozen),
                "frozen_settings_sha256": file_sha256(args.frozen), "test_consumed_after_run": final_manifest.get("test_consumed", False),
                "manifest_frozen_config_sha256": final_manifest.get("frozen_config_sha256"),
                "panel_fingerprint": manifest["fingerprint"], "fees_sha256": file_sha256(args.fees),
                "live_quotes_sha256": file_sha256(args.live_quotes), "half_spread_bps": half_spread,
                "note": "Sensitivity only; nothing was selected on the final test."}, sources_path)
    base_rows = table[table["is_base_scenario"]]
    print(base_rows[["backtest", "sample", "market_type", "latency", "n_signals", "n_net_positive", "mean_net_bps", "total_pnl_usdt"]]
          .round(3).to_string(index=False))
    print(f"{len(table)} rows -> {table_path}")
    return table


if __name__ == "__main__":
    main()
