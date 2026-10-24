"""TV6 output guard (TV4 review of C6.04): results never cross provenance zones and a refused run leaves old files untouched."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.backtest import run_backtest_a
from src.cost_model import gap_events
from src.cost_model.gap_events import detect_gap_events, write_gap_events
from src.synchronization.split import create_split_manifest

REAL = Path(__file__).resolve().parents[1]
FIXTURES = REAL/"data/fixtures/multi"
LEDGER = "backtest_a_dev_L1_ledger.parquet"


def tree(path: Path) -> dict:
    """{relative path: sha256} of every file below path (empty dict when it does not exist)."""
    if not path.exists():
        return {}
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A scratch 'repository' root; the runner and writer judge destinations against it, inputs still come from the real repo."""
    root = tmp_path/"repo"
    for folder in ("outputs/tables", "outputs/synthetic", "data/processed", "data/interim", "data/raw", "data/fixtures"):
        (root/folder).mkdir(parents=True)
    monkeypatch.setattr(run_backtest_a, "ROOT", root)
    monkeypatch.setattr(gap_events, "ROOT", root)
    return root


def backtest_args(output: Path, panel=None, manifest=None, quotes=None):
    args = ["--panel", str(panel or FIXTURES/"panel_1m_wide.parquet"), "--live-quotes", str(quotes or FIXTURES/"live_quotes_long.parquet"),
            "--config", str(REAL/"config/config.yaml"), "--fees", str(REAL/"config/fees.yaml"), "--output-dir", str(output)]
    return args + (["--manifest", str(manifest)] if manifest else [])


@pytest.mark.parametrize("relative", [
    "data/processed/backtest_probe",              # TV4 case 1: research data folder (used to overwrite an existing ledger)
    "outputs/tables/nested_probe",                # TV4 case 2: sub-folder of the official tables
    "outputs/tables",                              # the folder itself
    "outputs/synthetic/../tables/probe",          # '..' that resolves into the official tables
    "outputs/synthetic/../../data/processed/p",   # '..' that resolves into research data
    "data/interim",
    "data/raw/ohlcv",
])
def test_synthetic_backtest_is_refused_and_existing_files_survive(fake_repo, relative):
    target = fake_repo/relative
    target.mkdir(parents=True, exist_ok=True)
    old = target/LEDGER
    old.write_bytes(b"existing study ledger")
    before = tree(fake_repo)
    with pytest.raises(ValueError, match="Synthetic"):
        run_backtest_a.main(backtest_args(target))
    assert old.read_bytes() == b"existing study ledger"
    assert tree(fake_repo) == before                  # nothing was created, changed or removed anywhere


def test_synthetic_backtest_is_written_under_outputs_synthetic_and_scratch(fake_repo, tmp_path):
    for target in (fake_repo/"outputs/synthetic/multi/tables", tmp_path/"scratch"):
        run_backtest_a.main(backtest_args(target))
        assert (target/LEDGER).is_file() and (target/"half_spread_calibration.csv").is_file()
        assert json.loads((target/"backtest_a_dev_L1_params.json").read_text(encoding="utf-8"))["data_kind"] == "synthetic"


def historical_inputs(tmp_path):
    """The multi fixture relabelled as historical panel + live D2 with its own split manifest (engineering stand-in)."""
    panel = pd.read_parquet(FIXTURES/"panel_1m_wide.parquet")
    panel.attrs["data_kind"] = "historical"
    panel.to_parquet(tmp_path/"hist_panel.parquet")
    manifest = create_split_manifest(pd.read_parquet(tmp_path/"hist_panel.parquet"), path=tmp_path/"hist_manifest.json")
    pd.read_parquet(FIXTURES/"live_quotes_long.parquet").assign(data_kind="live").to_parquet(tmp_path/"live_d2.parquet", index=False)
    return tmp_path/"hist_panel.parquet", tmp_path/"hist_manifest.json", tmp_path/"live_d2.parquet", manifest


@pytest.mark.parametrize("relative", ["outputs/synthetic/results", "outputs/synthetic", "data/fixtures/multi"])
def test_research_backtest_cannot_write_into_synthetic_or_fixture_zones(fake_repo, tmp_path, relative):
    panel, manifest, quotes, _ = historical_inputs(tmp_path)
    target = fake_repo/relative
    target.mkdir(parents=True, exist_ok=True)
    (target/LEDGER).write_bytes(b"synthetic result")
    before = tree(fake_repo)
    with pytest.raises(ValueError, match="Research output"):
        run_backtest_a.main(backtest_args(target, panel, manifest, quotes))
    assert tree(fake_repo) == before


def test_research_backtest_may_write_official_tables(fake_repo, tmp_path):
    panel, manifest, quotes, _ = historical_inputs(tmp_path)
    run_backtest_a.main(backtest_args(fake_repo/"outputs/tables", panel, manifest, quotes))
    assert (fake_repo/"outputs/tables"/LEDGER).is_file()


def small_events(kind):
    gaps = pd.DataFrame({"ts_available": pd.date_range("2026-01-01T00:01Z", periods=3, freq="1min").as_unit("ns"),
                         "buy_exchange": "binance", "sell_exchange": "okx", "market_type": "spot",
                         "gross_gap_bps": [0.0, 6.0, 0.0], "net_gap_bps": [-20.0, -14.0, -20.0]})
    gaps.attrs["data_kind"] = kind
    return detect_gap_events(gaps, threshold_bps=5.0, grid_step_s=60)


@pytest.mark.parametrize("relative", ["data/processed/gap_events.parquet", "outputs/tables/nested/gap_events.parquet",
                                      "outputs/synthetic/../tables/gap_events.parquet", "outputs/tables/gap_events.parquet"])
def test_synthetic_gap_events_are_refused_and_existing_files_survive(fake_repo, relative):
    target = fake_repo/relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"existing events")
    before = tree(fake_repo)
    with pytest.raises(ValueError, match="Synthetic"):
        write_gap_events(small_events("synthetic"), target)
    assert target.read_bytes() == b"existing events" and tree(fake_repo) == before


def test_gap_events_zones_follow_provenance(fake_repo, tmp_path):
    write_gap_events(small_events("synthetic"), fake_repo/"outputs/synthetic/multi/tables/gap_events.parquet")
    write_gap_events(small_events("synthetic"), tmp_path/"scratch/gap_events.parquet")
    write_gap_events(small_events("historical"), fake_repo/"outputs/tables/gap_events.parquet")
    with pytest.raises(ValueError, match="Research output"):
        write_gap_events(small_events("historical"), fake_repo/"outputs/synthetic/multi/tables/gap_events.parquet.bak")
