"""Test C2.02: du lieu gia lap co SAN loi da biet, ham phai dem dung tung loi."""
import numpy as np
import pandas as pd
import pytest

from src.features.ohlcv_quality import check_venue, default_jump_bps, main, read_raw, to_utc


def make_clean(n=20):
    t = pd.date_range("2026-01-01", periods=n, freq="1min", tz="UTC")
    px = 100.0 + np.arange(n) * 0.01
    return pd.DataFrame({
        "open_time_utc": t, "open": px, "high": px + 0.05, "low": px - 0.05,
        "close": px, "volume": 1.0,
    })


def test_clean_data_has_no_errors():
    r = check_venue(make_clean(), "binance_spot")
    assert r["coverage_status"] == "observed_range"
    assert r["n_rows"] == 20 and r["expected_candles"] == 20
    for k in ("missing_candles", "duplicate_candles", "misaligned_ts", "nonpositive_price_rows",
              "high_lt_low_rows", "ohlc_inconsistent_rows", "negative_volume_rows", "jump_rows"):
        assert r[k] == 0, k


def test_missing_candles_and_gaps():
    df = make_clean().drop(index=[3, 4, 10])  # thieu 2 nen lien tiep + 1 nen le
    r = check_venue(df, "okx_spot")
    assert r["missing_candles"] == 3
    assert r["n_gaps"] == 2
    assert r["longest_gap_min"] == 2
    assert r["jump_rows"] == 0  # khoang thieu khong bi tinh la nhay gia


def test_duplicates_are_counted_not_removed():
    df = pd.concat([make_clean(), make_clean().iloc[[5]]], ignore_index=True)
    r = check_venue(df, "bybit_spot")
    assert r["duplicate_candles"] == 1
    assert r["n_rows"] == 21
    assert r["missing_candles"] == 0


def test_price_errors():
    df = make_clean()
    df.loc[2, "close"] = 0.0           # gia <= 0
    df.loc[6, ["high", "low"]] = [99.0, 101.0]   # high < low
    r = check_venue(df, "binance_perp")
    assert r["nonpositive_price_rows"] == 1
    assert r["high_lt_low_rows"] == 1
    assert r["ohlc_inconsistent_rows"] >= 1


def test_jump_detected_only_above_threshold():
    df = make_clean()
    df.loc[10:, ["open", "high", "low", "close"]] *= 1.10   # nhay +10% (~953 bps)
    assert check_venue(df, "okx_perp", jump_bps=300)["jump_rows"] == 1
    assert check_venue(df, "okx_perp", jump_bps=2000)["jump_rows"] == 0


def test_to_utc_handles_ms_and_us():
    ms = pd.Series([1_767_225_600_000])
    us = pd.Series([1_767_225_600_000_000])
    assert to_utc(ms).iloc[0] == to_utc(us).iloc[0] == pd.Timestamp("2026-01-01", tz="UTC")


def test_default_jump_bps_reads_config_and_handles_empty_quality(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("quality:\n  jump_bps: 450\n", encoding="utf-8")
    assert default_jump_bps(config) == 450.0

    config.write_text("quality:\n", encoding="utf-8")
    assert default_jump_bps(config) == 300.0


def test_read_raw_accepts_binance_schema(tmp_path):
    df = make_clean().rename(columns={"open_time_utc": "open_time_utc"})
    df["quote_volume"] = 5.0
    p = tmp_path / "binance_spot_BTCUSDT_1m.parquet"
    df.to_parquet(p, index=False)
    out = read_raw(p)
    assert list(out.columns) == ["open_time_utc", "open", "high", "low", "close", "volume"]


@pytest.mark.parametrize("case", ["empty", "no_valid_minute"])
def test_no_valid_minute_has_unknown_coverage_instead_of_clean_counts(case):
    df = make_clean(0 if case == "empty" else 20)
    if case == "no_valid_minute":
        df["open_time_utc"] += pd.Timedelta(seconds=1)
    original = df.copy(deep=True)
    r = check_venue(df, "binance_spot")
    assert r["coverage_status"] == case
    assert r["n_rows"] == len(df)
    assert r["misaligned_ts"] == len(df)
    for key in ("first_open_utc", "last_open_utc", "expected_candles", "missing_candles",
                "missing_pct", "n_gaps", "longest_gap_min"):
        assert r[key] is None, key
    pd.testing.assert_frame_equal(df, original)


@pytest.mark.parametrize("case", ["empty", "no_valid_minute"])
def test_cli_reports_bad_venue_and_continues_to_other_venues(tmp_path, case):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    bad = make_clean(0 if case == "empty" else 20)
    if case == "no_valid_minute":
        bad["open_time_utc"] += pd.Timedelta(seconds=1)
    bad_path = raw_dir / "binance_perp_BTCUSDT_1m.parquet"
    bad.to_parquet(bad_path, index=False)
    make_clean().to_parquet(raw_dir / "binance_spot_BTCUSDT_1m.parquet", index=False)
    before = bad_path.read_bytes()
    output = tmp_path / "T0.csv"
    main(["--raw-dir", str(raw_dir), "--out", str(output), "--jump-bps", "300"])
    table = pd.read_csv(output).set_index("venue")
    assert set(table.index) == {"binance_spot", "binance_perp"}
    assert table.loc["binance_perp", "coverage_status"] == case
    assert table.loc["binance_perp", "misaligned_ts"] == len(bad)
    assert pd.isna(table.loc["binance_perp", "missing_candles"])
    assert pd.isna(table.loc["binance_perp", "first_open_utc"])
    assert table.loc["binance_spot", "coverage_status"] == "observed_range"
    assert table.loc["binance_spot", "missing_candles"] == 0
    assert bad_path.read_bytes() == before
