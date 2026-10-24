"""TV6 C6.09 runner: gap events + D5 regime flags -> outputs/tables/T6b_gap_by_regime.csv.

    python -m src.backtest.make_t6b [--regime data/processed/regime_flags.parquet]
Reads the historical gap events (development split), the D5 flags (TV1) bound to the D4 manifest, the base backtest A
ledger (signals per regime) and, for the weekend comparison on the live sample, the live gap events and live D2 sessions.
A sources JSON records every input's SHA-256 and whether D5 is the official file or a local rebuild.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.cost_model.pairs import generate_pairs, split_venue
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json
from src.utils.pipeline_stages import validate_output_location
from .engine_live import session_windows
from .regime_gap import GROUPS, T6B_COLUMNS, gap_by_regime, live_weekend_rows

ROOT = Path(__file__).resolve().parents[2]
CANONICAL_D5 = ROOT/"data/processed/regime_flags.parquet"
T6B_NAME = "T6b_gap_by_regime.csv"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tables", type=Path, default=ROOT/"outputs/tables")
    parser.add_argument("--regime", type=Path, default=CANONICAL_D5)
    parser.add_argument("--receipt", type=Path, help="D5 receipt (default: next to D5, *_receipt.json)")
    parser.add_argument("--manifest", type=Path, default=ROOT/"data/processed/split_manifest.json")
    parser.add_argument("--live-quotes", type=Path, default=ROOT/"data/processed/live_quotes_long.parquet")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    tables = Path(args.tables)
    events = read_parquet(tables/"gap_events_historical.parquet", "D7")
    flags = read_parquet(args.regime, "D5")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    kinds = {"events": events["data_kind"].iloc[0] if len(events) else events.attrs.get("data_kind"),
             "D5": flags.attrs.get("data_kind")}
    if len(set(kinds.values())) != 1:
        raise ValueError(f"Gap events and D5 must share one data_kind: {kinds}")
    synthetic = kinds["events"] == "synthetic"

    receipt_path = args.receipt or args.regime.with_name(args.regime.stem + "_receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.is_file() else None
    if receipt is not None and receipt.get("panel_fingerprint") != manifest["fingerprint"]:
        raise ValueError("D5 receipt was built on a different panel than the D4 manifest (fingerprint mismatch)")
    if len(flags) and not (flags["ts_available"].min() <= pd.Timestamp(manifest["dev_start"]) and flags["ts_available"].max() >= pd.Timestamp(manifest["dev_end"])):
        raise ValueError("D5 does not cover the development window of the D4 manifest")

    output = Path(args.output_dir or (ROOT/"outputs/synthetic/tables" if synthetic else ROOT/"outputs/tables"))
    table_path, sources_path = output/T6B_NAME, output/"T6b_gap_by_regime_sources.json"
    for destination in (output, table_path, sources_path):
        validate_output_location(destination, root=ROOT, mode="synthetic" if synthetic else "historical")

    venues = cfg["venues"]
    pairs = generate_pairs(venues)
    pairs_per_group = {g: sum(split_venue(b)[1] == g for b, _ in pairs) for g in GROUPS}
    a_ledger_path = tables/f"backtest_a_dev_L{int(cfg['backtest_a']['latency_min'])}_ledger.parquet"
    ledger_a = read_parquet(a_ledger_path) if a_ledger_path.is_file() else None
    window = (pd.Timestamp(manifest["dev_start"]), pd.Timestamp(manifest["dev_end"]))
    parts = [gap_by_regime(events, flags, window, pairs_per_group, ledger_a)]
    inputs = {"gap_events_historical": tables/"gap_events_historical.parquet", "regime_flags": args.regime,
              "split_manifest": args.manifest}
    live_path = tables/"gap_events.parquet"
    if live_path.is_file() and args.live_quotes.is_file():
        live_events = read_parquet(live_path, "D7")
        if (live_events["data_kind"].iloc[0] if len(live_events) else "live") == "live":
            parts.append(live_weekend_rows(live_events, session_windows(read_parquet(args.live_quotes, "D2")), pairs_per_group))
            inputs.update(gap_events_live=live_path, live_quotes=args.live_quotes)
    if ledger_a is not None:
        inputs["backtest_a_ledger"] = a_ledger_path
    table = pd.concat(parts, ignore_index=True)[T6B_COLUMNS]

    output.mkdir(parents=True, exist_ok=True)
    table.to_csv(table_path, index=False)
    write_json({"table": T6B_NAME, "d5_is_official_path": args.regime.resolve() == CANONICAL_D5.resolve(),
                "d5_receipt": str(receipt_path) if receipt is not None else None,
                "d5_panel_fingerprint_matches_d4": True if receipt is not None else "receipt_missing",
                "window": [str(window[0]), str(window[1])], "pairs_per_group": pairs_per_group,
                "inputs": {name: {"path": str(path), "sha256": file_sha256(path)} for name, path in inputs.items()},
                "note": "Descriptive comparison; events cluster in time so rate ratios are not tested for significance."}, sources_path)
    shown = table[table["market_type"] == "perp"][["source", "dimension", "level", "minutes_in_level", "n_events", "events_per_1000_pair_minutes",
                                                   "rate_ratio_vs_reference", "median_duration_s", "n_any_net_positive"]]
    print(shown.round(3).to_string(index=False))
    return table


if __name__ == "__main__":
    main()
