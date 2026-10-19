"""Regressions from TV4's lead review: destination, split and venue integrity."""
import argparse
import copy
import json
from pathlib import Path
import sys

import pandas as pd
import pytest

import run_all
from src.synchronization.split import create_split_manifest, select_split, claim_final_test
from src.utils.io_utils import write_json
from src.utils.pipeline_stages import run_pipeline_stages, validate_output_location


def args_for(tmp_path, mode="synthetic", **overrides):
    values = dict(mode=mode, config=None, fixture_case="multi", data_dir=tmp_path/"scratch-data",
                  output_dir=tmp_path/"scratch-outputs", raw_dir=None, input=tmp_path/"reader.parquet",
                  foundation_only=True)
    values.update(overrides)
    return argparse.Namespace(**values)


@pytest.mark.parametrize("option,path", [
    ("data_dir", "data/processed"), ("data_dir", "data/interim"), ("data_dir", "data/raw"),
    ("data_dir", "data"), ("data_dir", "."), ("output_dir", "outputs"),
    ("output_dir", "outputs/tables"), ("output_dir", "outputs/figures"),
    ("output_dir", "outputs/synthetic/../tables"),
])
def test_synthetic_research_destinations_rejected_before_any_write(tmp_path, monkeypatch, option, path):
    root = tmp_path/"repo"
    marker = root/"data/processed/panel_1m_wide.parquet"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"existing study panel")
    old_report = root/"outputs/pipeline_report.json"
    old_report.parent.mkdir()
    old_report.write_bytes(b"existing study report")
    monkeypatch.setattr(run_all, "ROOT", root)
    with pytest.raises(ValueError, match="Synthetic"):
        run_all.run_pipeline(args_for(tmp_path, **{option: root/path}))
    assert marker.read_bytes() == b"existing study panel"
    assert old_report.read_bytes() == b"existing study report"
    assert not (root/"outputs/synthetic").exists()
    assert not (tmp_path/"scratch-outputs").exists()


@pytest.mark.parametrize("mode", ["historical", "raw"])
def test_research_cannot_write_synthetic_results_or_fixtures(tmp_path, mode):
    for path in (tmp_path/"outputs/synthetic/results", tmp_path/"data/fixtures/multi"):
        with pytest.raises(ValueError, match="Research output"):
            validate_output_location(path, root=tmp_path, mode=mode)
    validate_output_location(tmp_path/"outputs", root=tmp_path, mode=mode)
    validate_output_location(tmp_path/"data/processed", root=tmp_path, mode=mode)


def test_alias_to_research_destination_is_rejected(tmp_path):
    actual = tmp_path/"data/processed"
    actual.mkdir(parents=True)
    alias = tmp_path/"outputs/synthetic-link"
    alias.parent.mkdir()
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable on this machine")
    with pytest.raises(ValueError, match="Synthetic"):
        validate_output_location(alias, root=tmp_path, mode="synthetic")


def test_custom_synthetic_directory_and_scratch_remain_supported(tmp_path):
    validate_output_location(tmp_path/"outputs/synthetic/multi/data", root=tmp_path, mode="synthetic")
    validate_output_location(tmp_path/"scratch/results", root=tmp_path, mode="synthetic")


def test_stage_preflight_rejects_unsafe_later_output_before_first_command(tmp_path):
    marker = tmp_path/"command-ran.txt"
    old = tmp_path/"outputs/tables/gap_events.parquet"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"real events")
    command = [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')", str(marker)]
    stages = [{"name": "features", "command": command, "outputs": ["outputs/synthetic/features.txt"]},
              {"name": "cost_backtest", "command": command, "outputs": [str(old)]}]
    with pytest.raises(ValueError, match="Synthetic"):
        run_pipeline_stages(stages, root=tmp_path, data_dir=tmp_path/"outputs/synthetic/data",
                            output_dir=tmp_path/"outputs/synthetic", python=sys.executable,
                            on_update=lambda state: None, mode="synthetic")
    assert not marker.exists() and old.read_bytes() == b"real events"


def test_unsafe_stage_is_rejected_before_fixture_or_panel_generation(tmp_path, cfg, monkeypatch):
    root = tmp_path/"repo"
    monkeypatch.setattr(run_all, "ROOT", root)
    cfg = dict(cfg, pipeline_stages=[{"name": "features", "command": [sys.executable, "-c", "pass"],
                                     "outputs": ["data/processed/features.parquet"]}])
    monkeypatch.setattr(run_all, "load_config", lambda path: cfg)
    monkeypatch.setattr(run_all, "write_fixtures", lambda *a: pytest.fail("Preflight must precede fixture generation"))
    with pytest.raises(ValueError, match="Synthetic"):
        run_all.run_pipeline(args_for(tmp_path))
    report = json.loads((tmp_path/"scratch-outputs/pipeline_report.json").read_text())
    assert report["pipeline_status"] == "failed"
    assert not (root/"data").exists()


@pytest.mark.parametrize("entry", ["reuse", "select", "claim"])
@pytest.mark.parametrize("corruption", ["boundaries", "counts", "ratios"])
def test_every_split_entry_point_rejects_inconsistent_frozen_manifest(tmp_path, multi, entry, corruption):
    panel = multi[2]
    value = copy.deepcopy(create_split_manifest(panel))
    if corruption == "boundaries":
        value.update(dev_end=panel.index[650].isoformat(), val_start=panel.index[651].isoformat(),
                     val_end=panel.index[680].isoformat(), test_start=panel.index[681].isoformat())
    elif corruption == "counts": value["counts"]["dev"] = 651
    else: value["ratios"] = [.5, .3, .2]
    path = tmp_path/"split_manifest.json"
    write_json(value, path)
    frozen = tmp_path/"frozen.yaml"
    frozen.write_text("config: frozen", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="D4"):
        if entry == "reuse": create_split_manifest(panel, path=path)
        elif entry == "select": select_split(panel, value, "dev")
        else: claim_final_test(panel, path, frozen)
    assert path.read_bytes() == before
    assert not path.with_suffix(".json.test.lock").exists()


class ReachedAlignment(Exception):
    pass


@pytest.mark.parametrize("mode", ["raw", "historical"])
@pytest.mark.parametrize("case", ["matching", "missing", "extra", "missing_inside_sample", "duplicate_config", "dict_config"])
def test_production_venue_checks_apply_before_publication(tmp_path, multi, cfg, monkeypatch, mode, case):
    # Control-flow test only: mock the provenance check and stop at alignment.
    # The frame remains synthetic; no historical-labelled fixture is published.
    source, panel = multi[0].copy(), multi[2]
    selected = sorted(panel.attrs["venues"])
    production_cfg = dict(cfg, venues=selected, sample={"start": panel.index[0].isoformat(), "end": panel.index[5].isoformat()})
    if case == "missing": source = source[source.exchange != "okx"]
    elif case == "extra": production_cfg["venues"] = [v for v in selected if v != "okx_spot"]
    elif case == "missing_inside_sample": source = source[~((source.exchange == "okx") & (source.ts_available < panel.index[5]))]
    elif case == "duplicate_config": production_cfg["venues"] = selected + [selected[0]]
    elif case == "dict_config": production_cfg["venues"] = [{"exchange": "binance", "market_type": "spot"}]
    monkeypatch.setattr(run_all, "load_config", lambda path: production_cfg)
    monkeypatch.setattr(run_all, "read_parquet", lambda *a: source)
    monkeypatch.setattr(run_all, "load_raw_directory", lambda *a, **kw: source)
    monkeypatch.setattr(run_all, "data_kind", lambda frame: "historical")
    writes = []
    monkeypatch.setattr(run_all, "write_parquet", lambda *a: writes.append(a))
    def capture(frame, *a):
        assert sorted((frame.exchange+"_"+frame.market_type).unique()) == selected
        assert frame.data_kind.eq("synthetic").all()
        raise ReachedAlignment()
    monkeypatch.setattr(run_all, "align_previous_tick", capture)
    args = args_for(tmp_path, mode)
    if case == "matching":
        with pytest.raises(ReachedAlignment):
            run_all._run_pipeline(args, args.data_dir, args.output_dir, {})
    else:
        with pytest.raises(ValueError, match="venue"):
            run_all._run_pipeline(args, args.data_dir, args.output_dir, {})
        assert not writes
    assert not args.data_dir.exists() and not args.input.exists()
