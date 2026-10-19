"""TV4 intake gates operate on delivered files without repairing or claiming them."""
import copy
import json

import pandas as pd
import pytest

from src.synchronization.split import create_split_manifest
from src.synchronization.live import align_live_quotes
from src.utils.check_handoff import check_handoff, main
from src.utils.io_utils import file_sha256, write_json, write_parquet
from src.utils.synthetic import make_fixture


def save(tmp_path, frame, name="input.parquet"):
    path = tmp_path/name
    write_parquet(frame, path)
    return path


def config_for(tmp_path, multi, **overrides):
    panel = multi[2]
    cfg = {"venues": sorted(panel.attrs["venues"]), "timeframe": "1min", "split_ratios": [.6, .2, .2],
           "sample": {"start": panel.index[0].isoformat(),
                      "end": (panel.index[-1]+pd.Timedelta(minutes=1)).isoformat()}, **overrides}
    path = tmp_path/"config.yaml"
    path.write_text(json.dumps(cfg), encoding="utf-8")  # JSON is valid YAML.
    return path


@pytest.mark.parametrize("schema,index", [("D1", 0), ("D2", 1), ("D3", 2)])
def test_fixture_handoff_retains_provenance_and_exact_file_checksum(tmp_path, multi, schema, index):
    path = save(tmp_path, multi[index])
    before = path.read_bytes()
    report = check_handoff(path, schema, "synthetic", sha256=file_sha256(path).upper())
    assert report["status"] == "passed" and report["sha256"] == file_sha256(path)
    assert report["data_kind"] == "synthetic" and not report["research_acceptance_complete"]
    assert "binance_spot" in report["venues"] and "binance_perp" in report["venues"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("schema,index", [("D1", 0), ("D2", 1), ("D3", 2)])
def test_fixtures_cannot_be_accepted_as_historical_data(tmp_path, multi, schema, index):
    report = check_handoff(save(tmp_path, multi[index]), schema, "historical")
    assert report["status"] == "failed" and "received synthetic" in report["issues"][0]


def test_column_and_metadata_provenance_must_agree(tmp_path, multi):
    frame = multi[0].copy()
    frame.attrs["data_kind"] = "historical"
    report = check_handoff(save(tmp_path, frame), "D1", "synthetic")
    assert report["status"] == "failed" and "metadata disagree" in report["issues"][0]


@pytest.mark.parametrize("checksum", ["0"*64, "not-a-checksum"])
def test_bad_sender_checksums_are_rejected(tmp_path, multi, checksum):
    report = check_handoff(save(tmp_path, multi[0]), "D1", "synthetic", sha256=checksum)
    assert report["status"] == "failed" and "SHA-256" in report["issues"][0]


def test_duplicate_quote_receive_keys_are_not_deduplicated(tmp_path, multi):
    frame = pd.concat([multi[1], multi[1].iloc[[0]]], ignore_index=True)
    path = save(tmp_path, frame)
    before = file_sha256(path)
    report = check_handoff(path, "D2", "synthetic")
    assert report["status"] == "failed" and "duplicate" in report["issues"][0]
    assert file_sha256(path) == before


def test_corrupt_parquet_and_missing_input_are_reported_without_creating_data(tmp_path):
    path = tmp_path/"missing"/"input.parquet"
    assert check_handoff(path, "D1", "historical")["status"] == "missing"
    assert not path.parent.exists()
    path = tmp_path/"corrupt.parquet"
    path.write_bytes(b"not a parquet file")
    assert check_handoff(path, "D1", "historical")["status"] == "failed"


def test_manifest_check_preserves_consumed_state_lock_and_panel(tmp_path, multi):
    panel = save(tmp_path, multi[2])
    manifest = tmp_path/"split_manifest.json"
    value = create_split_manifest(multi[2])
    value["test_consumed"] = True
    write_json(value, manifest)
    lock = manifest.with_suffix(".json.test.lock")
    lock.write_text("existing-final-test-lock", encoding="utf-8")
    before = {p: p.read_bytes() for p in (panel, manifest, lock)}
    report = check_handoff(panel, "D3", "synthetic", manifest=manifest)
    assert report["status"] == "passed" and report["manifest"]["test_consumed"] is True
    assert report["manifest"]["test_lock_present"] is True
    assert any("already marked/locked" in warning for warning in report["warnings"])
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("field", ["fingerprint", "boundary", "counts", "ratios", "data_kind"])
def test_manifest_must_match_panel_and_chronological_split(tmp_path, multi, field):
    panel = save(tmp_path, multi[2])
    manifest = tmp_path/"split.json"
    value = create_split_manifest(multi[2])
    if field == "fingerprint": value[field] = "0"*64
    elif field == "boundary": value["dev_end"] = (pd.Timestamp(value["dev_end"])-pd.Timedelta(minutes=1)).isoformat()
    elif field == "counts": value[field]["dev"] -= 1
    elif field == "ratios": value[field] = [.5, .3, .2]
    else: value[field] = "historical"
    write_json(value, manifest)
    report = check_handoff(panel, "D3", "synthetic", manifest=manifest)
    assert report["status"] == "failed" and "D4" in report["issues"][0]
    assert not manifest.with_suffix(".json.test.lock").exists()


def test_d1_sample_uses_availability_and_does_not_trim_input(tmp_path, multi):
    panel = multi[2]
    config = config_for(tmp_path, multi, sample={"start": panel.index[0].isoformat(), "end": panel.index[5].isoformat()})
    path = save(tmp_path, multi[0])
    before = path.read_bytes()
    report = check_handoff(path, "D1", "synthetic", config=config)
    assert report["status"] == "passed"
    assert report["sample"]["n_rows_inside"] == 5*4
    assert report["sample"]["n_rows_outside"] == len(multi[0])-5*4
    assert path.read_bytes() == before


def test_d1_venue_outside_sample_does_not_count_as_delivered_inside_it(tmp_path, multi):
    panel = multi[2]
    config = config_for(tmp_path, multi, sample={"start": panel.index[0].isoformat(), "end": panel.index[5].isoformat()})
    source = multi[0]
    source = source[~((source.exchange == "okx") & (source.ts_available < panel.index[5]))]
    report = check_handoff(save(tmp_path, source), "D1", "synthetic", config=config)
    assert report["status"] == "failed" and "absent inside" in report["issues"][0]


def test_historical_sample_does_not_filter_live_quote_timestamps(tmp_path, multi):
    config = config_for(tmp_path, multi, sample={"start": "2025-01-01T00:00Z", "end": "2025-02-01T00:00Z"})
    report = check_handoff(save(tmp_path, multi[1]), "D2", "synthetic", config=config)
    assert report["status"] == "passed" and "sample" not in report


def test_five_exchange_collection_can_be_checked_and_selected_without_losing_original(tmp_path, cfg):
    # Exercise the agreed collection/research workflow on simulated D2 only.
    # These quotes retain synthetic provenance throughout, never relabelled live.
    research = [f"{exchange}_{market}" for exchange in ("binance", "okx", "bybit")
                for market in ("spot", "perp")]
    collected = research + ["kraken_spot", "coinbase_spot"]
    fixture_cfg = copy.deepcopy(cfg)
    fixture_cfg["fixtures"]["n_obs"] = 30
    fixture_cfg["fixtures"]["multi"]["lags"] = {venue: 0 for venue in collected}
    quotes = make_fixture(fixture_cfg)[1]
    original = save(tmp_path, quotes, "collected_quotes.parquet")
    checksum = file_sha256(original)

    broad = check_handoff(original, "D2", "synthetic")
    assert broad["status"] == "passed" and broad["venues"] == sorted(collected)
    assert broad["n_rows"] == 30*8
    aligned = align_live_quotes(quotes, grid_freq="1min", max_age="5s")
    assert all(f"bid__{venue}" in aligned for venue in collected)

    selected = quotes[(quotes.exchange+"_"+quotes.market_type).isin(research)].copy()
    research_file = save(tmp_path, selected, "research_quotes.parquet")
    config = tmp_path/"research.yaml"
    config.write_text(json.dumps({"venues": research, "sample": {
        "start": "2025-01-01T00:00Z", "end": "2025-02-01T00:00Z"}}), encoding="utf-8")
    checked = check_handoff(research_file, "D2", "synthetic", config=config)
    assert checked["status"] == "passed" and checked["venues"] == sorted(research)
    assert checked["n_rows"] == 30*6 and "sample" not in checked
    # The optional research gate must not silently accept the unselected pool.
    assert check_handoff(original, "D2", "synthetic", config=config)["status"] == "failed"
    assert file_sha256(original) == checksum


def test_panel_manifest_and_config_can_be_checked_together(tmp_path, multi):
    config = config_for(tmp_path, multi)
    manifest = tmp_path/"split.json"
    write_json(create_split_manifest(multi[2]), manifest)
    report = check_handoff(save(tmp_path, multi[2]), "D3", "synthetic", config=config, manifest=manifest)
    assert report["status"] == "passed" and report["manifest"]["counts"] == {"dev": 432, "val": 144, "test": 144}


@pytest.mark.parametrize("case", ["compressed", "outside_sample", "venue_mismatch"])
def test_invalid_research_panel_handoff_is_rejected(tmp_path, multi, case):
    panel = multi[2]
    options = {}
    if case == "compressed": panel = panel.drop(panel.index[10])
    elif case == "outside_sample": options["sample"] = {"start": panel.index[1].isoformat(), "end": (panel.index[-1]+pd.Timedelta(minutes=1)).isoformat()}
    else: options["venues"] = ["binance_spot", "binance_perp", "okx_spot"]
    report = check_handoff(save(tmp_path, panel), "D3", "synthetic", config=config_for(tmp_path, multi, **options))
    assert report["status"] == "failed"


@pytest.mark.parametrize("protected", ["input", "config", "manifest", "lock"])
def test_cli_report_cannot_overwrite_delivered_inputs(tmp_path, multi, protected):
    source = save(tmp_path, multi[2])
    config = config_for(tmp_path, multi)
    manifest = tmp_path/"manifest.json"
    write_json(create_split_manifest(multi[2]), manifest)
    lock = manifest.with_suffix(".json.test.lock")
    lock.write_text("existing-final-test-lock", encoding="utf-8")
    paths = {"input": source, "config": config, "manifest": manifest, "lock": lock}
    before = {p: p.read_bytes() for p in paths.values()}
    with pytest.raises(SystemExit) as error:
        main(["--input", str(source), "--schema", "D3", "--expected-kind", "synthetic",
              "--config", str(config), "--manifest", str(manifest), "--report", str(paths[protected])])
    assert error.value.code == 2
    assert all(p.read_bytes() == content for p, content in before.items())


@pytest.mark.parametrize("case,code,status", [("valid", 0, "passed"), ("mislabeled", 1, "failed"), ("missing", 2, "missing")])
def test_cli_reports_distinguish_pass_fail_and_pending_delivery(tmp_path, multi, capsys, case, code, status):
    source = tmp_path/"input.parquet" if case == "missing" else save(tmp_path, multi[0])
    report = tmp_path/"diagnostic.json"
    result = main(["--input", str(source), "--schema", "D1", "--expected-kind",
                   "historical" if case == "mislabeled" else "synthetic", "--report", str(report)])
    assert result == code
    assert json.loads(report.read_text())["status"] == status
    assert json.loads(capsys.readouterr().out)["status"] == status
