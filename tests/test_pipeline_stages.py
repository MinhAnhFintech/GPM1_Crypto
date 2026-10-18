"""C4.12 orchestration tested with real local subprocesses, no team modules."""
import copy
import os
import subprocess
import sys

import pytest

from src.utils.io_utils import file_sha256
from src.utils.pipeline_stages import run_pipeline_stages


@pytest.fixture
def execution(tmp_path):
    root = tmp_path / "repo with spaces"
    root.mkdir()
    snapshots = []

    def run(stages, **kwargs):
        return run_pipeline_stages(stages, root=root, data_dir=root/"data", output_dir=root/"outputs",
                                   python=sys.executable, on_update=lambda state: snapshots.append(copy.deepcopy(state)), **kwargs)

    return root, snapshots, run


def writer_stage(name="features", output="{output_dir}/result.txt"):
    # argv contains literal braces, proving token substitution preserves Python code.
    return {"name": name, "command": ["{python}", "-c",
            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(str({'rows': 3}))",
            output], "outputs": [output]}


def test_current_outputs_hashed_and_paths_with_spaces_supported(execution):
    root, snapshots, run = execution
    result = run([writer_stage()])
    output = root/"outputs/result.txt"
    assert output.read_text() == "{'rows': 3}"
    assert result[0]["outputs_sha256"] == {str(output): file_sha256(output)}
    assert snapshots[0][0]["status"] == "running"
    assert snapshots[-1][0]["status"] == "passed"


def test_relative_command_and_output_paths_use_repo_root(execution, monkeypatch, tmp_path):
    root, _, run = execution
    script = root/"build.py"
    script.write_text("from pathlib import Path\nPath('relative.txt').write_text('new')", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = run([{"name": "features", "command": ["{python}", "build.py"], "outputs": ["relative.txt"]}])
    assert result[0]["outputs_sha256"] == {str(root/"relative.txt"): file_sha256(root/"relative.txt")}
    assert not (tmp_path/"relative.txt").exists()


def test_old_output_does_not_turn_a_noop_into_success(execution):
    root, snapshots, run = execution
    old = root/"old.txt"
    old.write_text("last run", encoding="utf-8")
    with pytest.raises(ValueError, match="stale outputs"):
        run([{"name": "features", "command": ["{python}", "-c", "pass"], "outputs": ["old.txt"]}])
    assert old.read_text() == "last run"
    assert snapshots[-1][0]["status"] == "failed"


def test_rewritten_identical_bytes_are_valid(execution):
    root, _, run = execution
    path = root/"outputs/result.txt"
    path.parent.mkdir()
    path.write_text("{'rows': 3}", encoding="utf-8")
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    result = run([writer_stage()])
    assert result[0]["status"] == "passed"


def test_every_declared_output_is_required(execution):
    _, snapshots, run = execution
    stage = writer_stage()
    stage["outputs"].append("missing.txt")
    with pytest.raises(ValueError, match="did not produce"):
        run([stage])
    assert snapshots[-1][0]["status"] == "failed"


def test_failed_command_preserves_prior_evidence_and_stops_later_stages(execution):
    root, snapshots, run = execution
    bad = {"name": "price_discovery", "command": ["{python}", "-c", "raise SystemExit(7)"], "outputs": ["is.parquet"]}
    later = writer_stage("evaluation", "{output_dir}/later.txt")
    with pytest.raises(subprocess.CalledProcessError):
        run([writer_stage(), bad, later])
    assert [stage["status"] for stage in snapshots[-1]] == ["passed", "failed"]
    assert snapshots[-1][1]["return_code"] == 7
    assert not (root/"outputs/later.txt").exists()


def test_stage_timeout_is_reported(execution):
    _, snapshots, run = execution
    stage = {"name": "features", "command": ["{python}", "-c", "import time; time.sleep(10)"],
             "outputs": ["never.txt"], "timeout_s": 0.1}
    with pytest.raises(subprocess.TimeoutExpired):
        run([stage])
    assert snapshots[-1][0]["status"] == "failed"
    assert snapshots[-1][0]["error_type"] == "TimeoutExpired"


def test_disabled_and_foundation_only_never_launch_commands(execution):
    root, _, run = execution
    disabled = {"name": "price_discovery", "enabled": False}
    result = run([writer_stage(), disabled], foundation_only=True)
    assert [stage["status"] for stage in result] == ["not_run", "disabled"]
    assert not (root/"outputs/result.txt").exists()


@pytest.mark.parametrize("change", [
    {"command": "python build.py"}, {"outputs": []}, {"enabled": "false"}, {"timeout_s": -1},
    {"timeout_s": float("inf")}, {"command": [None]},
])
def test_bad_stage_config_stops_before_launch(execution, change):
    root, _, run = execution
    stage = writer_stage()
    stage.update(change)
    with pytest.raises(ValueError):
        run([stage])
    assert not (root/"outputs/result.txt").exists()


@pytest.mark.parametrize("same_name", [True, False])
def test_duplicate_stage_names_or_outputs_are_rejected_up_front(execution, same_name):
    root, _, run = execution
    first = writer_stage()
    second = writer_stage("features" if same_name else "evaluation")
    with pytest.raises(ValueError, match="Duplicate|already declared"):
        run([first, second])
    assert not (root/"outputs/result.txt").exists()
