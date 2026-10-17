"""C4.07: test the time invariants, not a copy of the implementation."""
import numpy as np
import pandas as pd
import pytest
from src.synchronization.align import align_previous_tick


def candles(times, closes, exchange="binance", market_type="spot"):
    values = np.array(closes, dtype=float)
    return pd.DataFrame({"ts_available": pd.to_datetime(times, utc=True, format="ISO8601").astype("datetime64[ns, UTC]"), "exchange": exchange, "market_type": market_type, "symbol": "BTC/USDT", "open": values, "high": values+1, "low": values-1, "close": values, "volume_base": 1.0, "volume_quote": values, "data_kind": "synthetic"})


def test_no_future_data():
    raw = candles(["2026-01-01T00:00:30Z", "2026-01-01T00:01:30Z"], [100, 200])
    panel = align_previous_tick(raw)
    assert np.isnan(panel.loc["2026-01-01T00:00Z", "close__binance_spot"])
    assert panel.loc["2026-01-01T00:01Z", "close__binance_spot"] == 100
    assert panel.loc["2026-01-01T00:02Z", "close__binance_spot"] == 200


def test_max_age_boundary_and_stale_missing():
    left = candles(["2026-01-01T00:00Z"], [100])
    right = candles(["2026-01-01T00:00Z", "2026-01-01T00:06Z"], [200, 300], "okx")
    panel = align_previous_tick(pd.concat([left, right]), max_age="5min")
    assert panel.loc["2026-01-01T00:05Z", "close__binance_spot"] == 100
    assert bool(panel.loc["2026-01-01T00:06Z", "missing__binance_spot"])
    assert panel.loc["2026-01-01T00:06Z", ["close__binance_spot", "log_close__binance_spot", "volume_base__binance_spot"]].isna().all()


def test_previous_tick_never_interpolates():
    panel = align_previous_tick(candles(["2026-01-01T00:00Z", "2026-01-01T00:02Z"], [100, 300]))
    assert panel.loc["2026-01-01T00:01Z", "close__binance_spot"] == 100
    assert 200 not in panel.close__binance_spot.to_numpy()


def test_utc_required_and_preserved():
    raw = candles(["2026-01-01T00:00Z", "2026-01-01T00:01Z"], [100, 200])
    assert str(align_previous_tick(raw).index.dtype) == "datetime64[ns, UTC]"
    raw.ts_available = raw.ts_available.dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        align_previous_tick(raw)


def test_venue_identity_and_duplicates():
    spot = candles(["2026-01-01T00:00Z"], [100])
    perp = candles(["2026-01-01T00:00Z"], [200], market_type="perp")
    panel = align_previous_tick(pd.concat([spot, perp]))
    assert panel.close__binance_spot.iloc[0] == 100
    assert panel.close__binance_perp.iloc[0] == 200
    with pytest.raises(ValueError, match="duplicate"):
        align_previous_tick(pd.concat([spot, spot]))


def test_future_append_does_not_change_past():
    initial = candles(["2026-01-01T00:00Z", "2026-01-01T00:02Z"], [100, 110])
    future = candles(["2026-01-01T00:08Z"], [900])
    before = align_previous_tick(initial)
    after = align_previous_tick(pd.concat([initial, future])).loc[before.index]
    pd.testing.assert_frame_equal(before, after)


def test_zero_age_requires_exact_timestamp():
    raw = candles(["2026-01-01T00:00:30Z", "2026-01-01T00:02Z"], [100, 200])
    panel = align_previous_tick(raw, max_age="0s")
    assert panel.missing__binance_spot.iloc[:2].all()
    assert panel.close__binance_spot.iloc[2] == 200
