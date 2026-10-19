"""C4.12: reproducible TV4 foundation and explicit team-stage orchestration.

Synthetic demo: python run_all.py --mode synthetic --verify
Real handoff:    python run_all.py --mode historical --config config/config.yaml --foundation-only
Full pipeline:  register pipeline_stages in config, then omit --foundation-only.
No mode downloads data, starts a collector, or consumes the final test implicitly.
"""
from __future__ import annotations

import argparse
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
from src.utils.config import configured_venue_ids, load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json, write_parquet
from src.utils.logging_utils import configure_logging
from src.utils.pipeline_stages import REQUIRED_STAGES, run_pipeline_stages, validate_output_location, validate_pipeline_stages
from src.utils.schema import data_kind, venues_from_panel
from src.utils.synthetic import write_fixtures
from src.utils.time_utils import utc_timestamp

ROOT = Path(__file__).resolve().parent


def run_pipeline(args) -> dict:
    """Build D1->D3->D4 and dev-only leadlag; run configured external stages."""
    if args.mode == "synthetic":
        work = args.data_dir or ROOT/"outputs/synthetic"/args.fixture_case/"data"
        outputs = args.output_dir or ROOT/"outputs/synthetic"/args.fixture_case
    else:
        work = args.data_dir or ROOT/"data/processed"
        outputs = args.output_dir or ROOT/"outputs"
    work, outputs = Path(work), Path(outputs)
    for destination in (work, outputs):
        validate_output_location(destination, root=ROOT, mode=args.mode)
    outputs.mkdir(parents=True, exist_ok=True)
    report = {"run_id": uuid.uuid4().hex, "mode": args.mode, "pipeline_status": "running",
              "foundation_status": "running", "team_stages": [], "pending_team_stages": sorted(REQUIRED_STAGES),
              "full_research_pipeline_complete": False, "analysis_split": "dev", "test_consumed": None}
    write_json(report, outputs/"pipeline_report.json")
    try:
        return _run_pipeline(args, work, outputs, report)
    except Exception as exc:
        report.update(pipeline_status="failed", full_research_pipeline_complete=False,
                      error_type=type(exc).__name__)
        if not isinstance(exc, subprocess.SubprocessError):
            report["reason"] = str(exc)
        if report["foundation_status"] == "running":
            report["foundation_status"] = "failed"
        write_json(report, outputs/"pipeline_report.json")
        raise


def _run_pipeline(args, work, outputs, report):
    cfg_path = args.config or ROOT/"config"/("config.example.yaml" if args.mode == "synthetic" else "config.yaml")
    cfg = load_config(cfg_path)
    validate_pipeline_stages(cfg.get("pipeline_stages", []), root=ROOT, data_dir=work,
                             output_dir=outputs, python=sys.executable, mode=args.mode)
    configure_logging()
    if args.mode == "synthetic":
        fixture_dir = ROOT/"data/fixtures"
        write_fixtures(cfg, fixture_dir)
        source = fixture_dir/args.fixture_case/"ohlcv_1m_long.parquet"
        long = read_parquet(source, "D1")
    else:
        venues = configured_venue_ids(cfg)
        if args.mode == "raw":
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
        present = set((long.exchange+"_"+long.market_type).unique())
        if present != set(venues):
            raise ValueError(f"Research venue mismatch inside sample: missing={sorted(set(venues)-present)}, unexpected={sorted(present-set(venues))}")
        if args.mode == "raw":
            write_parquet(long, source, "D1")
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
    report.update(data_kind=data_kind(panel), source=str(source), source_sha256=file_sha256(source),
                  config_sha256=file_sha256(cfg_path), panel_fingerprint=manifest["fingerprint"], rows=len(panel),
                  venues=venues_from_panel(panel), test_consumed=manifest["test_consumed"], foundation_status="passed")

    def publish_stages(results):
        report["team_stages"] = results
        report["pending_team_stages"] = sorted(REQUIRED_STAGES - {s["name"] for s in results if s["status"] == "passed"})
        write_json(report, outputs/"pipeline_report.json")

    run_pipeline_stages(cfg.get("pipeline_stages", []), root=ROOT, data_dir=work, output_dir=outputs,
                        python=sys.executable, on_update=publish_stages, foundation_only=args.foundation_only, mode=args.mode)
    missing = report["pending_team_stages"]
    report["full_research_pipeline_complete"] = args.mode != "synthetic" and not missing and not args.foundation_only
    report["pipeline_status"] = "foundation_only" if args.foundation_only else ("pending_team_stages" if missing else "passed")
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
    parser.add_argument("--foundation-only", action="store_true", help="Build the TV4 foundation without launching team stages")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            temp_root = ROOT/"tmp"
            temp_root.mkdir(parents=True, exist_ok=True)
            basetemp = temp_root/f"pytest-verify-{uuid.uuid4().hex}"
            subprocess.run([sys.executable, "-m", "pytest", "-q", "--basetemp", str(basetemp)], cwd=ROOT, check=True)
        run_pipeline(args)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Pipeline stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
