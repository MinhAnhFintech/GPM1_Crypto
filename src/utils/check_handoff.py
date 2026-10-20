"""TV4 read-only D1/D2/D3 intake checks, with optional D4 verification.

Example: python -m src.utils.check_handoff --input data/fixtures/multi/panel_1m_wide.parquet
    --schema D3 --expected-kind synthetic --manifest outputs/synthetic/multi/data/split_manifest.json
Pass --config only when checking the selected research venues/sample. This command
does not download data, repair files, run models, or claim/reset the final test.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

import pandas as pd

from src.synchronization.split import validate_panel_split
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json
from src.utils.schema import CONTRACT_VERSION, D2_CONTRACT_VERSION, VENUE_PATTERN, data_kind, validate_split_manifest, venues_from_panel
from src.utils.time_utils import utc_timestamp


def _check_manifest(panel, path, ratios):
    actual = validate_split_manifest(json.loads(path.read_text(encoding="utf-8")))
    validate_panel_split(panel, actual, ratios)
    counts = {part: int(((panel.index >= utc_timestamp(actual[f"{part}_start"])) &
                        (panel.index <= utc_timestamp(actual[f"{part}_end"]))).sum())
              for part in ("dev", "val", "test")}
    return {"path": str(path.resolve()), "sha256": file_sha256(path),
            "fingerprint": actual["fingerprint"], "counts": counts,
            "test_consumed": actual.get("test_consumed"),
            "test_lock_present": path.with_suffix(path.suffix+".test.lock").exists()}


def check_handoff(path, schema, expected_kind, *, config=None, manifest=None, sha256=None):
    """Return JSON-ready evidence; passed means format/provenance, not research acceptance.

    D1 may include candles outside the selected sample: the production pipeline
    filters those by availability time. D3 must already be on its regular grid
    and inside that interval. Historical sample dates never filter D2 live quotes.
    Config venue IDs must match exactly (spot/perp remain distinct).
    """
    path = Path(path)
    report = {"path": str(path.resolve()), "schema": schema, "expected_data_kind": expected_kind,
              "status": "failed", "scope": "handoff_contract", "research_acceptance_complete": False,
              "issues": [], "warnings": []}
    try:
        if schema not in {"D1", "D2", "D3"} or expected_kind not in {"synthetic", "historical", "live"}:
            raise ValueError("Choose D1/D2/D3 and an explicit synthetic/historical/live provenance")
        if manifest is not None and schema != "D3":
            raise ValueError("A D4 manifest can only be checked with its D3 panel")
        if not path.is_file():
            report.update(status="missing", issues=["Input file has not been handed over"])
            return report
        report["sha256"] = file_sha256(path)
        if sha256 is not None:
            if not isinstance(sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", sha256):
                raise ValueError("Expected SHA-256 must contain 64 hexadecimal characters")
            if report["sha256"] != sha256.lower():
                raise ValueError("Input SHA-256 differs from the sender's checksum")
        frame = read_parquet(path, schema)
        kind = data_kind(frame)
        report.update(data_kind=kind, n_rows=len(frame))
        if kind != expected_kind:
            raise ValueError(f"Expected {expected_kind} data, received {kind}")
        if "data_kind" in frame.attrs and frame.attrs["data_kind"] != kind:
            raise ValueError("data_kind column and metadata disagree")
        supported = {CONTRACT_VERSION, D2_CONTRACT_VERSION} if schema == "D2" else {CONTRACT_VERSION}
        if frame.attrs.get("contract_version", CONTRACT_VERSION) not in supported:
            raise ValueError("Unsupported contract_version metadata")
        if schema == "D2" and frame.duplicated(["ts_recv_utc", "exchange", "market_type", "symbol"]).any():
            raise ValueError("D2 has duplicate receive-time/venue/symbol keys")
        venues = (venues_from_panel(frame) if schema == "D3"
                  else (frame.exchange + "_" + frame.market_type).unique().tolist())
        report["venues"] = sorted(venues)
        times = frame.index if schema == "D3" else frame["ts_recv_utc" if schema == "D2" else "ts_available"]
        report.update(first_timestamp=times.min().isoformat(), last_timestamp=times.max().isoformat())
        cfg = load_config(config) if config else {}
        if config:
            report["config_sha256"] = file_sha256(config)
            selected = cfg.get("venues")
            if (not isinstance(selected, list) or not selected or not all(isinstance(v, str) and VENUE_PATTERN.fullmatch(v) for v in selected)
                    or len(selected) != len(set(selected))):
                raise ValueError("Config venues must be unique venue ID strings")
            if set(selected) != set(venues):
                raise ValueError(f"Venue mismatch: missing={sorted(set(selected)-set(venues))}, unexpected={sorted(set(venues)-set(selected))}")
            if schema != "D2":
                sample = cfg.get("sample", {})
                start, end = utc_timestamp(sample.get("start")), utc_timestamp(sample.get("end"))
                if end <= start:
                    raise ValueError("sample.end must be after sample.start")
                inside = (times >= start) & (times < end)
                report["sample"] = {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
                                    "n_rows_inside": int(inside.sum()), "n_rows_outside": int((~inside).sum())}
                if not inside.any() or (schema == "D3" and not inside.all()):
                    raise ValueError("D3 must be inside the sample; D1 must contain observations in it")
                if schema == "D1":
                    present = set((frame.loc[inside, "exchange"]+"_"+frame.loc[inside, "market_type"]).unique())
                    if set(selected)-present:
                        raise ValueError(f"Venues absent inside the sample: {sorted(set(selected)-present)}")
                if not inside.all():
                    report["warnings"].append("D1 includes rows outside sample; run_all filters them without modifying this file")
            else:
                report["warnings"].append("Historical sample dates do not apply to D2; use TV3 session coverage separately")
        if schema == "D3":
            if cfg:
                step = pd.Timedelta(pd.tseries.frequencies.to_offset(cfg["timeframe"]).nanos, unit="ns")
                if step <= pd.Timedelta(0) or not (frame.index == frame.index.floor(step)).all():
                    raise ValueError("D3 timestamps are off the configured grid")
                if not frame.index.to_series().diff().dropna().eq(step).all():
                    raise ValueError("D3 grid rows are missing; do not compress time")
            report["missing_rows_by_venue"] = {v: int(frame[f"missing__{v}"].sum()) for v in venues}
            if manifest is not None:
                report["manifest"] = _check_manifest(frame, Path(manifest), cfg.get("split_ratios", [.6, .2, .2]))
                if report["manifest"]["test_consumed"] or report["manifest"]["test_lock_present"]:
                    report["warnings"].append("Final test is already marked/locked; this check does not change that state")
        report["warnings"].append("File/schema checks do not establish full sample coverage, model correctness or milestone completion")
        report["status"] = "passed"
    except (ValueError, TypeError, KeyError, OSError) as error:
        report.update(status="failed", error_type=type(error).__name__)
        report["issues"].append(str(error))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--schema", required=True, choices=["D1", "D2", "D3"])
    parser.add_argument("--expected-kind", required=True, choices=["synthetic", "historical", "live"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--sha256", help="Optional sender-provided SHA-256")
    parser.add_argument("--report", type=Path, help="Optional diagnostic JSON; defaults to stdout only")
    args = parser.parse_args(argv)
    protected = [p.resolve() for p in (args.input, args.config, args.manifest) if p is not None]
    if args.manifest:
        protected.append(args.manifest.with_suffix(args.manifest.suffix+".test.lock").resolve())
    if args.report and args.report.resolve() in protected:
        parser.error("Report must not overwrite the input, config, split manifest or final-test lock")
    result = check_handoff(args.input, args.schema, args.expected_kind, config=args.config,
                           manifest=args.manifest, sha256=args.sha256)
    if args.report:
        write_json(result, args.report)
    print(json.dumps(result, ensure_ascii=True, allow_nan=False))
    return {"passed": 0, "failed": 1, "missing": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
