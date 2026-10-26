import json
import numpy as np
import pandas as pd
import pytest
from src.price_discovery.leadlag import lead_lag_xcorr
from src.synchronization.split import create_split_manifest, select_split, claim_final_test
from src.utils.io_utils import frame_fingerprint, write_parquet, read_parquet
from src.utils.schema import validate_frame
from src.utils.synthetic import make_fixture
from src.synchronization.normalize import normalize_raw_ohlcv
from src.utils.time_utils import utc_timestamp


@pytest.mark.parametrize("case", ["multi", "spot_perp"])
def test_fixture_recovers_known_lag(cfg, case):
    d1, d2, panel, truth = make_fixture(cfg, case)
    validate_frame(d1, "D1")
    validate_frame(d2, "D2")
    validate_frame(panel, "D3")
    leader = truth["known_leader"]
    for follower, lag in truth["lags_minutes"].items():
        if follower == leader:
            continue
        returns = pd.DataFrame({leader: panel[f"log_close__{leader}"].diff(), follower: panel[f"log_close__{follower}"].diff()})
        result = lead_lag_xcorr(returns, cfg["max_lag"])
        peak = result.loc[result.correlation.idxmax()]
        assert peak.lag == lag
        assert peak.correlation > .98


def test_split_counts_boundaries_and_lock(multi, tmp_path):
    panel = multi[2]
    path = tmp_path/"split_manifest.json"
    manifest = create_split_manifest(panel, path=path)
    assert manifest["counts"] == {"dev": 432, "val": 144, "test": 144}
    assert pd.Timestamp(manifest["dev_end"]) < pd.Timestamp(manifest["val_start"])
    assert pd.Timestamp(manifest["val_end"]) < pd.Timestamp(manifest["test_start"])
    assert len(select_split(panel, manifest)) == 432
    with pytest.raises(ValueError, match="claim_final_test"):
        select_split(panel, manifest, "test")
    frozen = tmp_path/"frozen.yaml"
    frozen.write_text("configuration: frozen", encoding="utf-8")
    assert len(claim_final_test(panel, path, frozen)) == 144
    assert create_split_manifest(panel, path=path)["test_consumed"] is True
    with pytest.raises(ValueError, match="already"):
        claim_final_test(panel, path, frozen)


def test_fingerprint_detects_changes(multi):
    original = multi[2]
    changed = original.copy()
    changed.iloc[0, 0] += 1
    assert frame_fingerprint(changed) != frame_fingerprint(original)
    manifest = create_split_manifest(original)
    with pytest.raises(ValueError, match="fingerprint"):
        select_split(changed, manifest)


def test_parquet_preserves_utc_index_metadata(multi, tmp_path):
    path = tmp_path/"panel.parquet"
    write_parquet(multi[2], path, "D3")
    loaded = read_parquet(path, "D3")
    pd.testing.assert_frame_equal(loaded, multi[2], check_freq=False)
    assert loaded.attrs == multi[2].attrs
    assert frame_fingerprint(loaded) == frame_fingerprint(multi[2])


def test_provenance_cannot_mix(multi):
    d1 = multi[0].copy()
    d1.loc[0, "data_kind"] = "historical"
    with pytest.raises(ValueError, match="provenance"):
        validate_frame(d1, "D1")


def test_gap_not_collapsed_and_constant_series():
    idx = pd.date_range("2026-01-01", periods=8, freq="1min", tz="UTC").as_unit("ns")
    frame = pd.DataFrame({"a_spot": [1, 2, np.nan, 4, 5, 6, 7, 8], "b_perp": 1.0}, index=idx)
    result = lead_lag_xcorr(frame, 1)
    assert result.correlation.isna().all()
    with pytest.raises(ValueError, match="regular grid"):
        lead_lag_xcorr(frame.drop(idx[2]), 1)


def test_binance_availability_is_open_plus_minute():
    raw = pd.DataFrame({"open_time_utc": pd.to_datetime(["2026-01-01T00:00Z"], utc=True), "close_time_utc": pd.to_datetime(["2026-01-01T00:00:59.999Z"], utc=True), "open": [100.0], "high": [102.0], "low": [99.0], "close": [101.0], "volume": [2.0], "quote_volume": [201.0]})
    d1 = normalize_raw_ohlcv(raw, exchange="binance", market_type="spot")
    assert d1.ts_available.iloc[0] == pd.Timestamp("2026-01-01T00:01Z")


def test_time_conversion_rejects_naive():
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_timestamp("2026-01-01")
    assert utc_timestamp("2026-01-01T07:00+07:00") == pd.Timestamp("2026-01-01T00:00Z")


def test_write_fixtures_keeps_unchanged_files(cfg, tmp_path):
    """Regenerating identical fixtures must not rewrite them (bytes also carry the pandas version)."""
    from src.utils.io_utils import file_sha256
    from src.utils.synthetic import write_fixtures
    write_fixtures(cfg, tmp_path)
    files = sorted(tmp_path.rglob("*.parquet")) + sorted(tmp_path.rglob("truth.json"))
    before = {p: (p.stat().st_mtime_ns, file_sha256(p)) for p in files}
    write_fixtures(cfg, tmp_path)
    assert {p: (p.stat().st_mtime_ns, file_sha256(p)) for p in files} == before

    # Changed content is still replaced, and truth.json follows the new bytes.
    panel = tmp_path/"multi"/"panel_1m_wide.parquet"
    stale = read_parquet(panel, "D3")
    column = next(c for c in stale if c.startswith("volume_base__"))
    stale.loc[stale.index[0], column] *= 2
    write_parquet(stale, panel, "D3")
    write_fixtures(cfg, tmp_path)
    fresh = read_parquet(panel, "D3")
    assert fresh[column].iloc[0] != stale[column].iloc[0]
    truth = json.loads((tmp_path/"multi"/"truth.json").read_text(encoding="utf-8"))
    assert truth["files_sha256"]["panel_1m_wide.parquet"] == file_sha256(panel)
