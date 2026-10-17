"""C4.12: reproducible TV4 foundation and explicit team-stage orchestration.

Synthetic demo: python run_all.py --mode synthetic --verify
Real handoff:    python run_all.py --mode historical --config config/config.yaml --foundation-only
Full pipeline:  register pipeline_stages in config, then omit --foundation-only.
No mode downloads data, starts a collector, or consumes the final test implicitly.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import subprocess
import sys
import uuid

import numpy as np
import pandas as pd
from src.price_discovery.leadlag import lead_lag_xcorr
from src.synchronization.align import align_previous_tick
from src.synchronization.normalize import load_raw_directory
from src.synchronization.split import create_split_manifest, select_split
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json, write_parquet
from src.utils.logging_utils import configure_logging
from src.utils.schema import data_kind, venues_from_panel
from src.utils.synthetic import write_fixtures
from src.utils.time_utils import utc_timestamp

ROOT = Path(__file__).resolve().parent


def run_pipeline(args) -> dict:
    """Build D1->D3->D4 and dev-only leadlag; run configured external stages."""
    cfg_path = args.config or ROOT/"config"/("config.example.yaml" if args.mode == "synthetic" else "config.yaml")
    cfg = load_config(cfg_path)
    configure_logging()
    if args.mode == "synthetic":
        fixture_dir = ROOT/"data/fixtures"
        write_fixtures(cfg, fixture_dir)
        source = fixture_dir/args.fixture_case/"ohlcv_1m_long.parquet"
        work = args.data_dir or ROOT/"outputs/synthetic"/args.fixture_case/"data"
        outputs = args.output_dir or ROOT/"outputs/synthetic"/args.fixture_case
        long = read_parquet(source, "D1")
    else:
        work = args.data_dir or ROOT/"data/processed"
        outputs = args.output_dir or ROOT/"outputs"
        if args.mode == "raw":
            venues = cfg.get("venues")
            if not venues:
                raise ValueError("Raw mode requires a finalized venues list in config")
            long = load_raw_directory(args.raw_dir or ROOT/"data/raw/ohlcv", venues=venues)
            source = ROOT/"data/interim/ohlcv_1m_long.parquet"
        else:
            source = args.input or ROOT/"data/interim/ohlcv_1m_long.parquet"
            long = read_parquet(source, "D1")
        if data_kind(long) != "historical":
            raise ValueError("Production modes require historical data; synthetic is separate")
        start, end = cfg.get("sample", {}).get("start"), cfg.get("sample", {}).get("end")
        if start is None or end is None:
            raise ValueError("Production needs explicit aware sample.start/end (end exclusive)")
        start, end = utc_timestamp(start), utc_timestamp(end)
        if end <= start:
            raise ValueError("sample.end must be after sample.start")
        long = long[long.ts_available.ge(start) & long.ts_available.lt(end)].copy()
        if long.empty:
            raise ValueError("No candles in the configured availability interval")
        if args.mode == "raw":
            write_parquet(long, source, "D1")
    work, outputs = Path(work), Path(outputs)
    if args.mode == "synthetic" and outputs.resolve() == (ROOT/"outputs").resolve():
        raise ValueError("Keep synthetic outputs in a separate subdirectory")
    panel = align_previous_tick(long, cfg["timeframe"], cfg["max_age"])
    work.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    # Check the existing frozen split before replacing the panel it describes.
    manifest = create_split_manifest(panel, cfg["split_ratios"], path=work/"split_manifest.json")
    write_parquet(panel, work/"panel_1m_wide.parquet", "D3")
    dev = select_split(panel, manifest, "dev")
    returns = pd.DataFrame({v: np.log(dev[f"close__{v}"]).diff() for v in venues_from_panel(panel)}, index=dev.index)
    xcorr = lead_lag_xcorr(returns, int(cfg["max_lag"]))
    table_dir, figure_dir = outputs/"tables", outputs/"figures"
    table_dir.mkdir(parents=True, exist_ok=True)
    xcorr.to_csv(table_dir/"T2_leadlag_xcorr.csv", index=False)
    quality = [{"venue": v, "n_grid_rows": len(panel), "n_missing": int(panel[f"missing__{v}"].sum()), "missing_pct": float(panel[f"missing__{v}"].mean()*100), "data_kind": data_kind(panel)} for v in venues_from_panel(panel)]
    pd.DataFrame(quality).to_csv(table_dir/"T0_alignment_quality.csv", index=False)
    from notebooks.templates.plot_template import plot_prices, plot_leadlag
    plot_prices(dev, figure_dir/"F1_synchronized_prices.png")
    plot_leadlag(xcorr, figure_dir/"F6_leadlag_xcorr.png", data_kind=data_kind(panel))
    if args.mode == "synthetic":
        from notebooks.templates.regression_template import fixture_regression
        fixture_regression(dev, venues_from_panel(panel)[0]).to_csv(table_dir/"template_ols_hac_demo.csv", index=False)
    stage_results = []
    stages = cfg.get("pipeline_stages", [])
    # All research stages must declare coverage; an empty list never counts as
    # a finished empirical pipeline for other members' unfinished modules.
    required = {"features", "regime_flags", "price_discovery", "cost_backtest", "evaluation"}
    for stage in stages:
        if stage.get("enabled", True):
            command = [str(value).format(python=sys.executable, data_dir=str(work.resolve()), output_dir=str(outputs.resolve())) for value in stage["command"]]
            subprocess.run(command, cwd=ROOT, check=True, shell=False)
            expected = [Path(str(p).format(data_dir=str(work), output_dir=str(outputs))) for p in stage.get("outputs", [])]
            if not expected or any(not p.is_file() for p in expected):
                raise ValueError(f"Stage {stage['name']} did not produce its declared outputs")
            stage_results.append({"name": stage["name"], "status": "passed", "outputs_sha256": {str(p): file_sha256(p) for p in expected}})
    missing = sorted(required-{s["name"] for s in stage_results})
    report = {"mode": args.mode, "data_kind": data_kind(panel), "source": str(source), "source_sha256": file_sha256(source), "config_sha256": file_sha256(cfg_path), "panel_fingerprint": manifest["fingerprint"], "rows": len(panel), "venues": venues_from_panel(panel), "analysis_split": "dev", "test_consumed": manifest["test_consumed"], "foundation_status": "passed", "team_stages": stage_results, "pending_team_stages": missing, "full_research_pipeline_complete": not missing}
    write_json(report, outputs/"pipeline_report.json")
    logging.getLogger("gpm1").info("Foundation passed: %s; %s rows; final test untouched", outputs, len(panel))
    if args.mode != "synthetic" and missing and not args.foundation_only:
        raise ValueError(f"TV4 foundation passed, full pipeline awaits team stages: {', '.join(missing)}. See pipeline_report.json")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["synthetic", "historical", "raw"], default="synthetic")
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"], default="multi")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--foundation-only", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            temp_root = ROOT/"tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            basetemp = temp_root/f"pytest-verify-{uuid.uuid4().hex}"
            subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(basetemp)], cwd=ROOT, check=True)
        run_pipeline(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Pipeline stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
