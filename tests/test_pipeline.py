"""Integration checks for the TV4 handoff, provenance and honest stage status."""
import argparse
from pathlib import Path
import json
import pytest
from run_all import run_pipeline


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
