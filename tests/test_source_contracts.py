"""TV4 source consistency gates must protect both readers and atomic writers."""
import pandas as pd
import pytest

from src.utils.io_utils import read_parquet, write_parquet
from src.utils.schema import data_kind, validate_frame


@pytest.mark.parametrize("schema,index", [("D1", 0), ("D2", 1), ("D3", 2)])
def test_conflicting_column_and_metadata_are_rejected(multi, schema, index):
    frame = multi[index].copy()
    frame["data_kind"] = "synthetic"
    frame.attrs["data_kind"] = "historical"
    with pytest.raises(ValueError, match="column and metadata disagree"):
        validate_frame(frame, schema)


@pytest.mark.parametrize("schema,index", [("D1", 0), ("D2", 1)])
def test_io_rejects_conflicting_source_without_replacing_accepted_file(tmp_path, multi, schema, index):
    good = multi[index].copy()
    target = write_parquet(good, tmp_path / "accepted.parquet", schema)
    before = target.read_bytes()
    bad = good.copy()
    bad.attrs["data_kind"] = "live"
    with pytest.raises(ValueError, match="column and metadata disagree"):
        write_parquet(bad, target, schema)
    assert target.read_bytes() == before
    raw = tmp_path / "untrusted.parquet"
    bad.to_parquet(raw)
    with pytest.raises(ValueError, match="column and metadata disagree"):
        read_parquet(raw, schema)


@pytest.mark.parametrize("kind", ["synthetic", "historical", "live"])
def test_consistent_legacy_and_metadata_only_sources_remain_supported(kind):
    frame = pd.DataFrame({"data_kind": [kind]})
    assert data_kind(frame) == kind
    frame.attrs["data_kind"] = kind
    assert data_kind(frame) == kind
    assert data_kind(frame.drop(columns="data_kind")) == kind


@pytest.mark.parametrize("metadata", [None, "", "unknown", ["synthetic"]])
def test_present_invalid_metadata_is_not_hidden_by_a_valid_column(metadata):
    frame = pd.DataFrame({"data_kind": ["synthetic"]})
    frame.attrs["data_kind"] = metadata
    with pytest.raises(ValueError, match="metadata"):
        data_kind(frame)


@pytest.mark.parametrize("metadata", [None, "unknown"])
def test_invalid_metadata_only_label_is_checked_on_optional_source_schemas(metadata):
    frame = pd.DataFrame({
        "ts_available": pd.date_range("2026-01-01T00:00Z", periods=2, freq="1min").as_unit("ns"),
        "flag_high_vol": False, "flag_weekend": False, "flag_event": False,
        "regime_bull_bear": "bull",
    })
    frame.attrs["data_kind"] = metadata
    with pytest.raises(ValueError, match="metadata"):
        validate_frame(frame, "D5")


def funding_frame():
    return pd.DataFrame({
        "ts_available": pd.date_range("2026-10-16T00:00Z", periods=2, freq="1D").as_unit("ns"),
        "exchange": "binance", "market_type": "perp", "symbol": "BTC/USDT",
        "funding_rate": [0.0001, -0.0001], "funding_interval_h": 8.0,
        "open_interest_base": [10.0, 20.0], "open_interest_usdt": [1e6, 2e6],
        "data_kind": "synthetic", "in_live_window": [False, True],
    })


def test_d9_live_window_flag_does_not_change_provenance_and_roundtrips(tmp_path):
    frame = funding_frame()
    frame.attrs.update(contract_version="1.1", data_kind="synthetic")
    path = write_parquet(frame, tmp_path / "funding.parquet", "D9")
    loaded = read_parquet(path, "D9")
    assert loaded.in_live_window.tolist() == [False, True]
    assert data_kind(loaded) == "synthetic"
    legacy = frame.drop(columns="in_live_window")
    legacy.attrs["contract_version"] = "1.0"
    validate_frame(legacy, "D9")
    legacy.attrs["contract_version"] = "1.1"
    with pytest.raises(ValueError, match="in_live_window"):
        validate_frame(legacy, "D9")


@pytest.mark.parametrize("bad", [["false", "true"], [0, 1], [False, None]])
def test_d9_rejects_nonboolean_or_missing_live_window_labels(bad):
    frame = funding_frame()
    frame["in_live_window"] = bad
    with pytest.raises(ValueError, match="in_live_window must be boolean"):
        validate_frame(frame, "D9")
