"""--with-quote của C3.04: attach_volume_quote và quote_check. Không gọi mạng, không cần exchange giả."""
import numpy as np
import pandas as pd
import pytest

from src.collectors.historical import ccxt_ohlcv as H

MIN = 60_000
START = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1000)


def raw(n=4, low=99.0, high=102.0, volume=2.0):
    ts = pd.to_datetime([START + i * MIN for i in range(n)], unit="ms", utc=True).astype("datetime64[ns, UTC]")
    return pd.DataFrame({"ts_open_utc": ts, "open": 100.0, "high": high, "low": low, "close": 101.0,
                         "volume_base": volume, "volume_quote": np.float64("nan")})


def quotes(n=4, price=100.0, volume=2.0):
    return {START + i * MIN: price * volume for i in range(n)}


# ------------------------------------------------------------------ attach_volume_quote
def test_attach_maps_quote_by_open_time_and_leaves_everything_else_untouched():
    df = raw()
    q = {START: 200.0, START + MIN: 202.0, START + 2 * MIN: 204.0, START + 3 * MIN: 206.0}
    out = H.attach_volume_quote(df, q)
    assert out["volume_quote"].tolist() == [200.0, 202.0, 204.0, 206.0]
    assert out["volume_quote"].dtype == "float64"
    pd.testing.assert_frame_equal(out.drop(columns="volume_quote"), df.drop(columns="volume_quote"))
    assert df["volume_quote"].isna().all()                       # không sửa df đầu vào


def test_attach_missing_minute_stays_nan_not_filled_or_interpolated():
    q = quotes()
    del q[START + MIN]
    out = H.attach_volume_quote(raw(), q)
    assert np.isnan(out["volume_quote"].iloc[1])
    assert out["volume_quote"].iloc[[0, 2, 3]].tolist() == [200.0, 200.0, 200.0]


def test_attach_ignores_quotes_outside_the_frame_and_keeps_row_count_and_order():
    q = {**quotes(), START - MIN: 1.0, START + 99 * MIN: 2.0}
    df = raw()
    out = H.attach_volume_quote(df, q)
    assert len(out) == len(df) and out["ts_open_utc"].is_monotonic_increasing
    assert out["volume_quote"].notna().all() and (out["volume_quote"] == 200.0).all()


def test_attach_with_empty_quote_gives_all_nan_float64():
    out = H.attach_volume_quote(raw(), {})
    assert out["volume_quote"].isna().all() and out["volume_quote"].dtype == "float64"


# ------------------------------------------------------------------ quote_check
def test_quote_check_clean_data_reports_zero():
    out = H.attach_volume_quote(raw(), quotes(price=100.0))
    assert H.quote_check(out) == {"n_quote_missing": 0, "n_quote_vwap_outside_hl": 0}


def test_quote_check_counts_missing_and_outside_separately():
    q = quotes()
    del q[START]                                                  # thiếu một phút
    q[START + MIN] = 50.0 * 2.0                                   # giá bình quân 50, ngoài [99, 102]
    out = H.attach_volume_quote(raw(), q)
    assert H.quote_check(out) == {"n_quote_missing": 1, "n_quote_vwap_outside_hl": 1}   # NaN không bị đếm là "ngoài"


@pytest.mark.parametrize("vwap,expected_outside", [
    (102.0, 0),          # đúng high
    (102.0 * 1.0009, 0),  # trong dung sai +0,1%
    (102.0 * 1.002, 1),   # vượt dung sai
    (99.0 * 0.9991, 0),   # trong dung sai -0,1% quanh low
    (99.0 * 0.998, 1),    # thấp hơn dung sai
])
def test_quote_check_tolerance_band(vwap, expected_outside):
    df = raw(n=1, volume=1.0)
    df["volume_quote"] = vwap
    assert H.quote_check(df)["n_quote_vwap_outside_hl"] == expected_outside


def test_quote_check_zero_volume_minutes_are_not_flagged():
    df = raw(volume=0.0)
    df["volume_quote"] = 0.0
    assert H.quote_check(df) == {"n_quote_missing": 0, "n_quote_vwap_outside_hl": 0}


def test_quote_check_catches_contract_units_in_volume_base():
    # volume_base bị nhầm là số HỢP ĐỒNG (1 hợp đồng = 0.01 BTC): 200 hợp đồng = 2 BTC, nhưng quote vẫn là 200 USDT
    # -> quote/base = 1, xa khoảng giá ~100: phải bị bắt hàng loạt
    df = H.attach_volume_quote(raw(volume=200.0), quotes(price=100.0, volume=2.0))
    chk = H.quote_check(df)
    assert chk["n_quote_vwap_outside_hl"] == len(df) > 0.001 * len(df)