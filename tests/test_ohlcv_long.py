"""Test C2.03: ts_available = gio mo + 1 phut, cot dung D1, cat khoang mau, bat bien D1."""
import numpy as np
import pandas as pd
import pytest

from src.features.ohlcv_long import D1_COLS, build_long

START = pd.Timestamp("2026-01-01T00:01:00Z")
END = pd.Timestamp("2026-01-01T00:11:00Z")


def write_raw(raw_dir, venue, n=15, **override):
    exchange, market_type = venue.split("_", 1)
    t = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    px = 100.0 + np.arange(n) * 0.01
    df = pd.DataFrame({
        "ts_open_utc": t, "venue": venue, "exchange": exchange, "market_type": market_type,
        "symbol": "BTC/USDT", "open": px, "high": px + 0.05, "low": px - 0.05, "close": px,
        "volume_base": 1.0, "volume_quote": px, "data_kind": "historical",
    })
    for col, val in override.items():
        df[col] = val
    df.to_parquet(raw_dir / f"{venue}_BTCUSDT_1m.parquet", index=False)


def test_columns_order_and_available_time(tmp_path):
    write_raw(tmp_path, "binance_spot")
    out = build_long(tmp_path, ["binance_spot"], "BTC/USDT", START, END)
    assert list(out.columns) == D1_COLS
    # nen mo luc 00:00 co ts_available = 00:01 (da dong); nen mo luc 00:09 -> 00:10; 00:10 -> 00:11 bi loai (end loai tru)
    assert out["ts_available"].min() == pd.Timestamp("2026-01-01T00:01:00Z")
    assert out["ts_available"].max() == pd.Timestamp("2026-01-01T00:10:00Z")
    assert len(out) == 10
    assert str(out["ts_available"].dtype) == "datetime64[ns, UTC]"


def test_merge_two_venues_unique_key(tmp_path):
    write_raw(tmp_path, "binance_spot")
    write_raw(tmp_path, "binance_perp")
    out = build_long(tmp_path, ["binance_spot", "binance_perp"], "BTC/USDT", START, END)
    assert len(out) == 20
    assert set(out["market_type"]) == {"spot", "perp"}
    assert not out.duplicated(["ts_available", "exchange", "market_type", "symbol"]).any()


def test_venue_mismatch_raises(tmp_path):
    write_raw(tmp_path, "okx_spot", market_type="perp")  # file ten spot nhung noi dung perp
    with pytest.raises(ValueError, match="khong khop"):
        build_long(tmp_path, ["okx_spot"], "BTC/USDT", START, END)


def test_duplicates_are_reported_not_removed(tmp_path):
    write_raw(tmp_path, "bybit_spot")
    p = tmp_path / "bybit_spot_BTCUSDT_1m.parquet"
    df = pd.read_parquet(p)
    pd.concat([df, df.iloc[[3]]]).to_parquet(p, index=False)
    with pytest.raises(ValueError, match="trung khoa"):
        build_long(tmp_path, ["bybit_spot"], "BTC/USDT", START, END)


def test_bad_price_raises(tmp_path):
    write_raw(tmp_path, "okx_perp")
    p = tmp_path / "okx_perp_BTCUSDT_1m.parquet"
    df = pd.read_parquet(p)
    df.loc[2, "close"] = 0.0
    df.to_parquet(p, index=False)
    with pytest.raises(ValueError, match="vi pham D1"):
        build_long(tmp_path, ["okx_perp"], "BTC/USDT", START, END)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_long(tmp_path, ["bybit_perp"], "BTC/USDT", START, END)