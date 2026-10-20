"""TV3 -> TV4 D2 extension compatibility; all observations here are synthetic."""
import pandas as pd
import pytest

from src.synchronization.live import align_live_quotes
from src.utils.check_handoff import check_handoff
from src.utils.io_utils import write_parquet
from src.utils.schema import D2_BASE_COLUMNS, D2_COLUMNS, upgrade_d2_frame, validate_frame


def extended(multi):
    frame = multi[1].iloc[:8].copy()
    frame["session_id"] = "fixture"
    frame["ts_grid_utc"] = frame.ts_recv_utc
    frame["depth_cover_bps"] = 20.0
    for side in ("bid", "ask"):
        for band in (2, 5):
            frame[f"depth_{side}_{band}bps_usdt"] = frame[f"depth_{side}_10bps_usdt"] * band / 10
        frame[f"book_levels_{side}"] = 20
    frame["latency_ms"] = 10.0
    return upgrade_d2_frame(frame)


def test_v11_column_order_roundtrip_and_sender_handoff(multi, tmp_path):
    frame = extended(multi)
    assert list(frame.columns[:len(D2_COLUMNS)]) == D2_COLUMNS
    assert list(frame.columns[:14]) == D2_BASE_COLUMNS
    assert frame.depth_truncated.equals(frame.depth_censored)
    path = write_parquet(frame, tmp_path / "quotes.parquet", "D2")
    assert pd.read_parquet(path).attrs["contract_version"] == "1.1"
    assert check_handoff(path, "D2", "synthetic")["status"] == "passed"
    # A file claiming v1.1 must not silently move an extension into the core.
    bad = frame[["market_type"] + [c for c in frame if c != "market_type"]]
    with pytest.raises(ValueError, match="order"):
        validate_frame(bad, "D2")


def test_legacy_upgrade_preserves_values_source_and_original(multi):
    legacy = multi[1].drop(columns=["depth_truncated", "venue"])
    legacy.attrs["contract_version"] = "1.0"
    original = legacy.copy(deep=True)
    validate_frame(legacy, "D2")
    upgraded = upgrade_d2_frame(legacy)
    pd.testing.assert_frame_equal(legacy, original)
    for column in legacy:
        pd.testing.assert_series_equal(legacy[column], upgraded[column])
    assert "depth_cover_bps" not in upgraded
    assert upgraded.data_kind.eq("synthetic").all()


@pytest.mark.parametrize("column,value,error", [
    ("venue", "okx_perp", "venue"),
    ("depth_truncated", True, "disagree"),
    ("depth_truncated", 1, "boolean"),
    ("depth_bid_2bps_usdt", -1.0, "negative"),
    ("depth_bid_2bps_usdt", 1e15, "band"),
    ("depth_cover_bps", -1.0, "negative"),
    ("latency_ms", -1.0, "negative"),
    ("book_levels_bid", 2.5, "integer"),
])
def test_invalid_extensions_are_rejected(multi, column, value, error):
    frame = extended(multi)
    frame[column] = value
    with pytest.raises(ValueError, match=error):
        validate_frame(frame, "D2")


def test_new_flag_only_and_depth_extensions_follow_quote_expiry(multi):
    frame = extended(multi).drop(columns="depth_censored")
    # Keep one venue at the first tick and another through the next tick.
    first = frame.ts_recv_utc.min()
    venue = frame.venue.iloc[0]
    frame = frame[~(frame.venue.eq(venue) & frame.ts_recv_utc.gt(first))]
    panel = align_live_quotes(frame, grid_freq="1min", max_age="0s")
    assert not panel.loc[first, f"depth_truncated__{venue}"]
    assert pd.isna(panel.iloc[-1][f"depth_truncated__{venue}"])
    assert pd.isna(panel.iloc[-1][f"depth_bid_2bps_usdt__{venue}"])
    assert pd.isna(panel.iloc[-1][f"depth_cover_bps__{venue}"])


def test_missing_market_type_remains_invalid(multi):
    with pytest.raises(ValueError, match="market_type"):
        validate_frame(multi[1].drop(columns="market_type"), "D2")
