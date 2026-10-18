"""C6.03: gap event detection and duration (RQ4). Hand-made gap series, no real data."""
import numpy as np
import pandas as pd
import pytest

from src.cost_model.gap_events import EVENT_COLUMNS, detect_gap_events, write_gap_events
from src.cost_model.pairs import compute_gaps
from src.cost_model.costs import FeeSchedule
from src.utils.schema import validate_frame

STEP_S = 60
THRESHOLD = 5.0
START = pd.Timestamp("2026-01-01T00:01Z")


def gaps(gross, buy="binance", sell="okx", market_type="spot", net=None, minutes=None, kind="synthetic"):
    """One pair's gap series on a 1-minute grid (or at the given minute offsets)."""
    minutes = range(len(gross)) if minutes is None else minutes
    times = pd.DatetimeIndex([START + pd.Timedelta(minutes=m) for m in minutes]).as_unit("ns")
    frame = pd.DataFrame({"ts_available": times, "buy_exchange": buy, "sell_exchange": sell, "market_type": market_type,
                          "gross_gap_bps": np.asarray(gross, dtype=float),
                          "net_gap_bps": np.asarray(gross if net is None else net, dtype=float) - (0 if net is not None else 20.0)})
    frame.attrs["data_kind"] = kind
    return frame


def detect(frame, **kw):
    return detect_gap_events(frame, kw.pop("threshold", THRESHOLD), kw.pop("step", STEP_S), **kw)


def test_three_consecutive_points_make_one_event():
    events = detect(gaps([0, 6, 7, 8, 0]))
    assert len(events) == 1
    event = events.iloc[0]
    assert event.n_obs == 3 and event.duration_s == 3 * STEP_S
    assert event.ts_start == START + pd.Timedelta(minutes=1) and event.ts_end == START + pd.Timedelta(minutes=3)
    assert event.gross_bps == 6 and event.max_gross_gap_bps == 8 and event.mean_gross_gap_bps == pytest.approx(7)


def test_threshold_is_inclusive():
    assert len(detect(gaps([0, THRESHOLD, 0]))) == 1
    assert detect(gaps([0, THRESHOLD - 1e-9, 0])).empty


def test_one_point_below_threshold_splits_two_events():
    events = detect(gaps([6, 6, 1, 6, 0]))
    assert list(events.n_obs) == [2, 1]


def test_time_hole_splits_events():
    events = detect(gaps([6, 6, 6, 6], minutes=[0, 1, 3, 4]))     # minute 2 is missing
    assert list(events.n_obs) == [2, 2]
    assert list(events.duration_s) == [2 * STEP_S, 2 * STEP_S]


def test_nan_inside_cluster_breaks_event():
    events = detect(gaps([6, np.nan, 6, 0]))
    assert len(events) == 2 and (events.n_obs == 1).all()


def test_single_point_duration_is_grid_step():
    event = detect(gaps([0, 9, 0])).iloc[0]
    assert event.n_obs == 1 and event.duration_s == STEP_S


def test_censoring_flags():
    events = detect(gaps([6, 0, 0, 6, 6]))
    assert list(events.censored_start) == [True, False]
    assert list(events.censored_end) == [False, True]
    assert not detect(gaps([0, 6, 0])).iloc[0].censored_end


def test_any_net_positive():
    gross_only = detect(gaps([0, 10, 10, 0], net=[-10, -6, -1, -10])).iloc[0]
    assert gross_only.any_net_positive == False                 # noqa: E712 - numpy bool
    assert gross_only.net_bps == -6 and gross_only.max_net_gap_bps == -1
    assert detect(gaps([0, 10, 30, 0], net=[-10, -6, 2, -10])).iloc[0].any_net_positive == True  # noqa: E712


def test_directions_independent_and_market_type_kept():
    forward = gaps([6, 6, 0], buy="binance", sell="okx")
    backward = gaps([0, 6, 6], buy="okx", sell="binance")
    perp = gaps([6, 0, 0], buy="binance", sell="okx", market_type="perp")
    events = detect(pd.concat([forward, backward, perp], ignore_index=True), data_kind="synthetic")
    by_pair = events.set_index(["market_type", "buy_exchange", "sell_exchange"])
    assert by_pair.loc[("spot", "binance", "okx")].n_obs == 2
    assert by_pair.loc[("spot", "okx", "binance")].n_obs == 2
    assert by_pair.loc[("perp", "binance", "okx")].n_obs == 1
    assert set(events.market_type) == {"spot", "perp"}


def test_spot_perp_pair_rejected():
    frame = gaps([6, 6]).assign(buy_venue="binance_spot", sell_venue="binance_perp")
    with pytest.raises(ValueError, match="spot-perp"):
        detect(frame)


def test_no_spot_perp_events_from_compute_gaps():
    index = pd.date_range(START, periods=3, freq="1min", name="ts_available").as_unit("ns")
    panel = pd.DataFrame({"bid__binance_spot": [100.2] * 3, "ask__binance_spot": [100.3] * 3,
                          "bid__binance_perp": [101.0] * 3, "ask__binance_perp": [101.1] * 3,
                          "bid__okx_spot": [99.9] * 3, "ask__okx_spot": [100.0] * 3}, index=index)
    panel.attrs["data_kind"] = "synthetic"
    fees = FeeSchedule(venues={v: {"taker_bps": 5.0} for v in ["binance_spot", "binance_perp", "okx_spot"]})
    events = detect(compute_gaps(panel, fees))
    assert len(events) == 1 and events.iloc[0][["buy_exchange", "sell_exchange", "market_type"]].tolist() == ["okx", "binance", "spot"]


def test_ids_unique_utc_and_d7_schema():
    frame = pd.concat([gaps([6, 0, 6, 6]), gaps([6, 6, 0, 6], buy="okx", sell="binance")], ignore_index=True)
    frame.attrs["data_kind"] = "synthetic"
    events = detect(frame)
    assert events.event_id.is_unique
    for column in ("ts_start", "ts_end"):
        assert str(events[column].dtype) == "datetime64[ns, UTC]"
    assert list(events.columns) == EVENT_COLUMNS
    validate_frame(events, "D7")


def test_mixed_or_missing_data_kind_rejected():
    mixed = pd.concat([gaps([6]).assign(data_kind="synthetic"), gaps([6], buy="okx", sell="binance").assign(data_kind="live")])
    with pytest.raises(ValueError, match="data_kind"):
        detect(mixed)
    with pytest.raises(ValueError, match="data_kind"):
        detect(gaps([6]), data_kind="historical")              # conflicts with attrs
    unlabeled = gaps([6])
    unlabeled.attrs = {}
    with pytest.raises(ValueError, match="data_kind"):
        detect(unlabeled)


def test_naive_time_and_finer_grid_rejected():
    naive = gaps([6, 6])
    naive["ts_available"] = naive.ts_available.dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        detect(naive)
    with pytest.raises(ValueError, match="grid_step_s"):
        detect(gaps([6, 6]), step=120)


def test_no_events_gives_empty_schema():
    events = detect(gaps([0, 1, np.nan]))
    assert events.empty and list(events.columns) == EVENT_COLUMNS
    assert str(events.ts_start.dtype) == "datetime64[ns, UTC]" and events.attrs["data_kind"] == "synthetic"


def test_input_not_modified():
    frame = gaps([6, 6, 0])
    copy = frame.copy()
    detect(frame)
    pd.testing.assert_frame_equal(frame, copy)


def test_write_roundtrip_and_path_guard(tmp_path):
    events = detect(gaps([0, 6, 6]))
    path = tmp_path/"tables"/"gap_events.parquet"
    write_gap_events(events, path)
    back = pd.read_parquet(path)
    assert back.regime.isna().all() and events.regime.isna().all()          # pending D5
    pd.testing.assert_frame_equal(back[EVENT_COLUMNS].drop(columns="regime"), events.drop(columns="regime"), check_dtype=False)
    write_gap_events(detect(gaps([0, 0])), tmp_path/"empty.parquet")
    with pytest.raises(ValueError, match="outputs/synthetic"):
        write_gap_events(events, "outputs/tables/gap_events.parquet")
