"""Test C2.08: báo giá live trong dashboard (hàm thuần và bộ đọc D2, không cần mạng)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_app_live", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

VENUES = ("binance_spot", "binance_perp", "okx_spot")


def make_live(n=4):
    rows = []
    grid = pd.date_range("2026-10-20 10:00", periods=n, freq="5s", tz="UTC")
    for v in VENUES:
        for i, t in enumerate(grid):
            bid, ask = 100.0 + i, 100.1 + i
            mid = (bid + ask) / 2
            rows.append(dict(ts_recv_utc=t + pd.Timedelta(milliseconds=100), ts_grid_utc=t, ts_event_utc=t,
                             exchange=v.split("_")[0], symbol="BTC/USDT", venue=v,
                             market_type=v.rsplit("_", 1)[1], bid=bid, ask=ask, bid_size=1.0, ask_size=1.0,
                             mid=mid, spread_abs=ask - bid, spread_bps=(ask - bid) / mid * 1e4,
                             depth_bid_10bps_usdt=np.nan, depth_ask_10bps_usdt=5e6,
                             depth_truncated=False, depth_cover_bps=10.0, data_kind="live"))
    return pd.DataFrame(rows)


def write(tmp_path, frame, name="live.parquet"):
    path = tmp_path / name
    frame.to_parquet(path)
    return str(path)


def test_live_latest_takes_last_received_quote_per_venue_and_keeps_gaps():
    live = make_live(4).sample(frac=1, random_state=3)
    out = app.live_latest(live).set_index("venue")
    assert out.loc["okx_spot", "bid"] == 103.0
    expected_median = live.loc[live.venue.eq("okx_spot"), "spread_bps"].median()
    assert out.loc["okx_spot", "spread_bps_median"] == pytest.approx(expected_median)
    assert out.loc["okx_spot", "n_quotes"] == 4
    assert out["depth_bid_10bps_usdt"].isna().all()  # không lấp ô thiếu
    assert not out["depth_truncated"].any() and out["depth_cover_bps"].eq(10).all()


def test_live_latest_uses_receive_time_instead_of_grid_time():
    live = make_live(2)
    venue = live.venue.eq("okx_spot")
    older_grid = live[venue].index[0]
    newer_grid = live[venue].index[1]
    live.loc[older_grid, "ts_recv_utc"] += pd.Timedelta(seconds=10)
    out = app.live_latest(live).set_index("venue")
    assert out.loc["okx_spot", "bid"] == live.loc[older_grid, "bid"]
    assert out.loc["okx_spot", "ts_recv_utc"] == live.loc[older_grid, "ts_recv_utc"]


def test_load_live_accepts_valid_file(tmp_path):
    df, msg = app._load_live(write(tmp_path, make_live()), 1.0, VENUES)
    assert msg is None and len(df) == 12 and df["ts_grid_utc"].dt.tz is not None
    assert df["ts_recv_utc"].dt.tz is not None


def test_load_live_waits_when_file_missing(tmp_path):
    df, msg = app._load_live(str(tmp_path / "khong_co.parquet"), 0.0, VENUES)
    assert df is None and msg.startswith("Chờ file")


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d.drop(columns=["spread_bps"]), "thiếu cột"),
    (lambda d: d.assign(data_kind="historical"), "data_kind"),
    (lambda d: d.assign(venue="khac_spot"), "venue không có trong panel"),
    (lambda d: d.assign(market_type="perp"), "market_type"),
    (lambda d: d.iloc[0:0], "rỗng"),
    (lambda d: d.assign(symbol="ETH/USDT"), "symbol must be canonical BTC/USDT"),
    (lambda d: d.assign(ask=d.bid - 1), "invalid/crossed book"),
    (lambda d: d.assign(spread_bps=999.0), "inconsistent spread_bps"),
])
def test_load_live_rejects_bad_files(tmp_path, mutate, expect):
    df, msg = app._load_live(write(tmp_path, mutate(make_live()), f"bad_{expect[:3]}.parquet"), 2.0, VENUES)
    assert df is None and expect in msg