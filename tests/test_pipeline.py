"""Integration checks for the TV4 handoff, provenance and honest stage status."""
import argparse
from pathlib import Path
import json
import subprocess
import sys
import pytest
from run_all import run_pipeline
from src.utils.io_utils import write_json
from src.utils.pipeline_stages import REQUIRED_STAGES


def test_fixture_pipeline_is_reproducible_and_does_not_touch_final_test(tmp_path):
    args = argparse.Namespace(config=None, mode="synthetic", fixture_case="spot_perp", data_dir=tmp_path/"data", output_dir=tmp_path/"outputs", raw_dir=None, input=None, foundation_only=False)
    first = run_pipeline(args)
    second = run_pipeline(args)
    assert first["panel_fingerprint"] == second["panel_fingerprint"]
    assert not first["test_consumed"]
    assert first["foundation_status"] == "passed"
    assert not first["full_research_pipeline_complete"]
    assert "price_discovery" in first["pending_team_stages"]
    assert (tmp_path/"outputs/figures/F1_synchronized_prices.png").is_file()


def test_dashboard_starter_renders_seven_sections():
    from streamlit.testing.v1 import AppTest
    root = Path(__file__).resolve().parents[1]
    app = AppTest.from_file(str(root/"notebooks/templates/streamlit_template.py")).run(timeout=20)
    assert not app.exception
    assert not app.error
    assert len(app.subheader) == 7


def test_foundation_only_skips_configured_team_commands(tmp_path, cfg, monkeypatch):
    marker = tmp_path/"should-not-exist.txt"
    cfg = dict(cfg, pipeline_stages=[{"name": "features", "command": [sys.executable, "-c",
               "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')", str(marker)], "outputs": [str(marker)]}])
    monkeypatch.setattr("run_all.load_config", lambda path: cfg)
    args = argparse.Namespace(config=None, mode="synthetic", fixture_case="spot_perp", data_dir=tmp_path/"data",
                              output_dir=tmp_path/"outputs", raw_dir=None, input=None, foundation_only=True)
    report = run_pipeline(args)
    assert not marker.exists()
    assert report["pipeline_status"] == "foundation_only"
    assert report["team_stages"][0]["status"] == "not_run"
    assert "features" in report["pending_team_stages"]
    assert not report["full_research_pipeline_complete"]


def test_failed_rerun_replaces_previous_success_report(tmp_path, cfg, monkeypatch):
    outputs = tmp_path/"outputs"
    report_path = outputs/"pipeline_report.json"
    write_json({"run_id": "old", "pipeline_status": "passed", "full_research_pipeline_complete": True}, report_path)
    cfg = dict(cfg, pipeline_stages=[{"name": "features", "command": [sys.executable, "-c", "raise SystemExit(7)"],
                                    "outputs": [str(outputs/"missing.parquet")]}])
    monkeypatch.setattr("run_all.load_config", lambda path: cfg)
    args = argparse.Namespace(config=None, mode="synthetic", fixture_case="spot_perp", data_dir=tmp_path/"data",
                              output_dir=outputs, raw_dir=None, input=None, foundation_only=False)
    with pytest.raises(subprocess.CalledProcessError):
        run_pipeline(args)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] != "old"
    assert report["pipeline_status"] == "failed"
    assert report["foundation_status"] == "passed"
    assert report["team_stages"][0]["return_code"] == 7
    assert not report["full_research_pipeline_complete"]


def test_missing_input_marks_foundation_failed_in_report(tmp_path):
    args = argparse.Namespace(config=tmp_path/"missing-config.yaml", mode="historical", fixture_case="multi",
                              data_dir=tmp_path/"data", output_dir=tmp_path/"outputs", raw_dir=None,
                              input=tmp_path/"missing.parquet", foundation_only=True)
    with pytest.raises(FileNotFoundError):
        run_pipeline(args)
    report = json.loads((args.output_dir/"pipeline_report.json").read_text(encoding="utf-8"))
    assert report["foundation_status"] == "failed"
    assert report["pipeline_status"] == "failed"
    assert report["test_consumed"] is None


def test_synthetic_stage_outputs_cannot_claim_complete_empirical_research(tmp_path, cfg, monkeypatch):
    stages = [{"name": name, "command": [sys.executable, "-c",
              "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('synthetic integration test')",
              str(tmp_path/f"{name}.txt")], "outputs": [str(tmp_path/f"{name}.txt")]}
              for name in sorted(REQUIRED_STAGES)]
    cfg = dict(cfg, pipeline_stages=stages)
    monkeypatch.setattr("run_all.load_config", lambda path: cfg)
    args = argparse.Namespace(config=None, mode="synthetic", fixture_case="spot_perp", data_dir=tmp_path/"data",
                              output_dir=tmp_path/"outputs", raw_dir=None, input=None, foundation_only=False)
    report = run_pipeline(args)
    assert report["pending_team_stages"] == []
    assert all(stage["status"] == "passed" for stage in report["team_stages"])
    assert not report["full_research_pipeline_complete"]
    assert not report["test_consumed"]
