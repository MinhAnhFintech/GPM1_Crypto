"""TV4 live handoff: irregular receive times -> causal quotes -> TV6 D7."""
import numpy as np
import pandas as pd
import pytest

from src.cost_model.costs import FeeSchedule, load_fees
from src.cost_model.gap_events import detect_gap_events
from src.cost_model.pairs import compute_gaps
from src.cost_model.run_gap_events import main as gap_main, quotes_to_wide
from src.synchronization.live import QUOTE_METRICS, align_live_quotes
from src.utils.io_utils import read_parquet, write_parquet
from src.utils.schema import validate_frame

START = pd.Timestamp("2026-01-01T00:00Z")


def quotes(seconds, bids=None, *, exchange="binance", market_type="spot"):
    times = pd.DatetimeIndex([START + pd.Timedelta(seconds=s) for s in seconds]).as_unit("ns")
    bids = np.asarray(bids if bids is not None else [100.0] * len(times), dtype=float)
    asks = bids + 0.2
    mid = (bids + asks) / 2
    return pd.DataFrame({
        "ts_recv_utc": times, "ts_event_utc": times - pd.Timedelta(milliseconds=100),
        "exchange": exchange, "market_type": market_type, "symbol": "BTC/USDT",
        "bid": bids, "ask": asks, "bid_size": 1.0, "ask_size": 2.0,
        "mid": mid, "spread_abs": asks - bids, "spread_bps": (asks - bids) / mid * 10000,
        "depth_bid_10bps_usdt": 1000.0, "depth_ask_10bps_usdt": 2000.0,
        "depth_censored": False, "data_kind": "synthetic",
    })


def align(frame, *, max_age="10s"):
    return align_live_quotes(frame, grid_freq="5s", max_age=max_age)


def test_receive_time_controls_availability_without_rounding_backwards():
    source = quotes([1, 9, 11], [100, 200, 300])
    source["ts_event_utc"] = pd.Series([START] * 3, dtype="datetime64[ns, UTC]")
    source["ts_grid_utc"] = source.ts_recv_utc.dt.floor("5s")
    source["ts_available"] = source.ts_event_utc
    panel = align(source)
    assert panel.index.tolist() == [START + pd.Timedelta(seconds=s) for s in [0, 5, 10]]
    assert panel.loc[START, "missing__binance_spot"]
    assert np.isnan(panel.loc[START, "bid__binance_spot"])
    assert panel.loc[START + pd.Timedelta(seconds=5), "bid__binance_spot"] == 100
    assert panel.loc[START + pd.Timedelta(seconds=10), "bid__binance_spot"] == 200
    assert 300 not in panel.bid__binance_spot.values
    assert panel.index.max() <= source.ts_recv_utc.max()
    known = panel.source_ts_recv_utc__binance_spot.dropna()
    assert (known <= known.index).all()


def test_age_boundary_and_all_stale_quote_values_are_masked():
    source = pd.concat([quotes([0]), quotes([0, 5, 10, 15], exchange="okx")], ignore_index=True)
    panel = align(source)
    assert not panel.loc[START + pd.Timedelta(seconds=10), "missing__binance_spot"]
    stale = panel.loc[START + pd.Timedelta(seconds=15)]
    assert stale.missing__binance_spot
    assert stale[[f"{metric}__binance_spot" for metric in QUOTE_METRICS]].isna().all()
    assert pd.isna(stale.depth_censored__binance_spot)
    assert stale.source_ts_recv_utc__binance_spot == START
    assert stale.quote_age_s__binance_spot == 15
    assert not stale.missing__okx_spot


def test_audit_fields_keep_event_time_and_unknown_event_time():
    source = quotes([1, 6, 11])
    source.loc[0, "ts_event_utc"] = pd.NaT
    panel = align(source)
    first = panel.loc[START + pd.Timedelta(seconds=5)]
    assert first.source_ts_recv_utc__binance_spot == START + pd.Timedelta(seconds=1)
    assert pd.isna(first.source_ts_event_utc__binance_spot)
    assert first.quote_age_s__binance_spot == 4
    assert not first.depth_censored__binance_spot


def test_same_exchange_spot_perp_are_independent():
    source = pd.concat([quotes([0], [100]), quotes([0, 15], [200, 300], market_type="perp")], ignore_index=True)
    panel = align(source, max_age="5s")
    assert panel.loc[START, "bid__binance_spot"] == 100
    assert panel.loc[START, "bid__binance_perp"] == 200
    assert panel.loc[START + pd.Timedelta(seconds=15), "missing__binance_spot"]
    assert panel.loc[START + pd.Timedelta(seconds=15), "bid__binance_perp"] == 300


def test_future_append_and_input_order_do_not_change_past_panel():
    source = quotes([1, 6, 11], [100, 110, 120])
    original = source.copy(deep=True)
    before = align(source)
    extended = align(pd.concat([source, quotes([21], [999])], ignore_index=True))
    pd.testing.assert_frame_equal(before, extended.loc[before.index])
    pd.testing.assert_frame_equal(before, align(source.iloc[::-1]))
    pd.testing.assert_frame_equal(source, original)


def test_duplicate_receive_time_within_venue_is_not_silently_deduplicated():
    with pytest.raises(ValueError, match="duplicate receive-time"):
        align(quotes([1, 1, 11], [100, 999, 110]))


def test_utc_and_provenance_are_validated():
    source = quotes([1, 6, 11])
    naive = source.copy()
    naive["ts_recv_utc"] = naive.ts_recv_utc.dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        align(naive)
    mixed = source.copy()
    mixed.loc[0, "data_kind"] = "live"
    with pytest.raises(ValueError, match="provenance"):
        align(mixed)
    source.attrs["data_kind"] = "historical"
    with pytest.raises(ValueError, match="metadata disagree"):
        align(source)


@pytest.mark.parametrize("freq", ["0s", "-5s", "MS"])
def test_invalid_grids_rejected(freq):
    with pytest.raises(ValueError, match="grid_freq"):
        align_live_quotes(quotes([1, 6]), grid_freq=freq, max_age="5s")


@pytest.mark.parametrize("max_age", ["-1s", "NaT", None])
def test_invalid_age_limits_rejected(max_age):
    with pytest.raises(ValueError, match="max_age"):
        align(quotes([1, 6]), max_age=max_age)


def test_zero_age_requires_exact_receive_time():
    panel = align(quotes([1, 5, 11], [100, 200, 300]), max_age="0s")
    assert panel.loc[START + pd.Timedelta(seconds=5), "bid__binance_spot"] == 200
    assert panel.loc[START + pd.Timedelta(seconds=10), "missing__binance_spot"]


def test_parquet_keeps_source_timestamps_and_nullable_depth_flag(tmp_path):
    panel = align(quotes([1, 6, 11]))
    path = tmp_path/"aligned_quotes.parquet"
    write_parquet(panel, path)
    loaded = read_parquet(path)
    pd.testing.assert_frame_equal(loaded, panel, check_freq=False)
    assert loaded.attrs["data_kind"] == "synthetic"
    assert str(loaded.source_ts_recv_utc__binance_spot.dtype) == "datetime64[ns, UTC]"


def test_on_grid_fixture_gaps_match_original_pivot(multi):
    source = multi[1]
    venue = source.exchange + "_" + source.market_type
    original = source.assign(venue=venue).pivot(index="ts_recv_utc", columns="venue", values=["bid", "ask"])
    original.columns = [f"{side}__{v}" for side, v in original.columns]
    original.index.name = "ts_available"
    original.attrs["data_kind"] = "synthetic"
    fees = load_fees("config/fees.yaml")
    pd.testing.assert_frame_equal(compute_gaps(quotes_to_wide(source, 60), fees), compute_gaps(original, fees))


def test_off_grid_requires_explicit_quote_age():
    with pytest.raises(ValueError, match="live_max_age_s"):
        quotes_to_wide(quotes([1, 6, 11]), 5)


@pytest.mark.parametrize("bad", [-1, np.inf, np.nan, True])
def test_adapter_rejects_invalid_age_seconds(bad):
    with pytest.raises(ValueError, match="live_max_age_s"):
        quotes_to_wide(quotes([1, 6]), 5, max_age_s=bad)


@pytest.mark.parametrize("bad", [0, -1, np.inf, np.nan, True])
def test_adapter_rejects_invalid_grid_seconds(bad):
    with pytest.raises(ValueError, match="grid_step_s"):
        quotes_to_wide(quotes([1, 6]), bad, max_age_s=5)


def irregular_opportunity():
    return pd.concat([quotes([1, 11], [110, 110]),
                      quotes([2, 8, 12, 22], [99.8] * 4, exchange="okx")], ignore_index=True)


def test_expired_live_leg_breaks_gap_event_instead_of_extending_it():
    panel = quotes_to_wide(irregular_opportunity(), 5, max_age_s=6)
    fees = FeeSchedule(venues={v: {"taker_bps": 10} for v in ["binance_spot", "okx_spot"]})
    gaps = compute_gaps(panel, fees)
    events = detect_gap_events(gaps, threshold_bps=5, grid_step_s=5)
    assert events.ts_start.tolist() == [START + pd.Timedelta(seconds=s) for s in [5, 15]]
    assert events.duration_s.tolist() == [5.0, 5.0]
    assert events.market_type.eq("spot").all()
    assert panel.loc[START + pd.Timedelta(seconds=10), "missing__binance_spot"]


@pytest.mark.parametrize("use_override", [False, True])
def test_runner_d2_to_d7_with_irregular_receive_times(tmp_path, use_override):
    source, config, output = tmp_path/"quotes.parquet", tmp_path/"config.yaml", tmp_path/"events.parquet"
    write_parquet(irregular_opportunity(), source, "D2")
    config.write_text("gap_events:\n  gap_threshold_bps: 5\n  grid_step_s: 5\n  live_max_age_s: "
                      + ("0" if use_override else "6") + "\n", encoding="utf-8")
    argv = ["--quotes", str(source), "--config", str(config), "--output", str(output)]
    if use_override:
        argv += ["--live-max-age-s", "6"]
    events = gap_main(argv)
    validate_frame(events, "D7")
    assert len(events) == 2 and events.duration_s.eq(5).all()
    assert events.attrs["data_kind"] == "synthetic"
    loaded = read_parquet(output, "D7")
    assert loaded.regime.isna().all() and events.regime.isna().all()
    pd.testing.assert_frame_equal(loaded.drop(columns="regime"), events.drop(columns="regime"))
