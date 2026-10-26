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
            rows.append(dict(ts_recv_utc=t + pd.Timedelta(seconds=2), ts_grid_utc=t, venue=v,
                             market_type=v.rsplit("_", 1)[1], bid=100.0 + i, ask=100.1 + i,
                             mid=100.05 + i, spread_abs=0.1, spread_bps=1.0 + i, depth_bid_10bps_usdt=np.nan,
                             depth_ask_10bps_usdt=5e6, depth_truncated=(i % 2 == 0), data_kind="live"))
    return pd.DataFrame(rows)


def write(tmp_path, frame, name="live.parquet"):
    path = tmp_path / name
    frame.to_parquet(path)
    return str(path)


def test_live_latest_takes_last_receive_time_per_venue_and_keeps_gaps():
    live = make_live(4).sample(frac=1, random_state=3)  # thứ tự dòng bị xáo
    out = app.live_latest(live).set_index("venue")
    assert out.loc["okx_spot", "bid"] == 103.0  # dòng cuối theo ts_recv_utc, không theo thứ tự trong file
    assert out.loc["okx_spot", "spread_bps_median"] == pytest.approx(2.5)
    assert out.loc["okx_spot", "n_quotes"] == 4
    assert out["depth_bid_10bps_usdt"].isna().all()  # không lấp ô thiếu


def test_load_live_accepts_valid_file(tmp_path):
    df, msg = app._load_live(write(tmp_path, make_live()), 1.0, VENUES)
    assert msg is None and len(df) == 12 and df["ts_grid_utc"].dt.tz is not None


def test_load_live_waits_when_file_missing(tmp_path):
    df, msg = app._load_live(str(tmp_path / "khong_co.parquet"), 0.0, VENUES)
    assert df is None and msg.startswith("Chờ file")


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d.drop(columns=["spread_bps"]), "thiếu cột"),
    (lambda d: d.assign(data_kind="historical"), "data_kind"),
    (lambda d: d.assign(venue="khac_spot"), "venue không có trong panel"),
    (lambda d: d.assign(market_type="perp"), "market_type"),
    (lambda d: d.iloc[0:0], "rỗng"),
])
def test_load_live_rejects_bad_files(tmp_path, mutate, expect):
    df, msg = app._load_live(write(tmp_path, mutate(make_live()), f"bad_{expect[:3]}.parquet"), 2.0, VENUES)
    assert df is None and expect in msg

def test_latest_quote_is_chosen_by_receive_time_not_grid_time():
    live = make_live(3)
    # báo giá có giờ lưới SỚM hơn nhưng được nhận SAU: đó mới là thông tin có sẵn mới nhất
    late = live[(live.venue == "okx_spot") & (live.bid == 100.0)].index[0]
    live.loc[late, "ts_recv_utc"] = live["ts_recv_utc"].max() + pd.Timedelta(minutes=1)
    out = app.live_latest(live).set_index("venue")
    assert out.loc["okx_spot", "bid"] == 100.0


def test_latest_quote_keeps_the_truncated_depth_flag():
    out = app.live_latest(make_live(4)).set_index("venue")
    assert out["depth_truncated"].dtype == bool and out.loc["okx_spot", "depth_truncated"] == False   # i=3 là lẻ


def test_truncated_depth_is_shown_as_lower_bound():
    df = pd.DataFrame({"venue": ["a_spot", "b_spot"], "depth_bid_10bps_usdt": [5e6, 7e6],
                       "depth_ask_10bps_usdt": [np.nan, 7e6], "depth_truncated": [True, False]})
    out = app.pretty(df)
    assert out.loc[0, "depth_bid_10bps_usdt"].startswith("≥ ") and out.loc[0, "depth_ask_10bps_usdt"] == "–"
    assert not out.loc[1, "depth_bid_10bps_usdt"].startswith("≥") and out.loc[0, "depth_truncated"] == "Bị cắt"


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: d.assign(bid=-1.0), "bid"),
    (lambda d: d.assign(bid=d["ask"] + 1.0), "sổ lệnh chéo"),
    (lambda d: d.assign(ts_recv_utc=d["ts_recv_utc"].dt.tz_localize(None)), "múi giờ"),
    (lambda d: d.assign(ts_grid_utc=d["ts_grid_utc"].dt.tz_localize(None)), "múi giờ"),
    (lambda d: d.drop(columns=["depth_truncated"]), "thiếu cột"),
    (lambda d: d.drop(columns=["ts_recv_utc"]), "thiếu cột"),
])
def test_load_live_rejects_invalid_quotes(tmp_path, mutate, expect):
    df, msg = app._load_live(write(tmp_path, mutate(make_live()), f"inv_{abs(hash(expect)) % 999}.parquet"), 3.0, VENUES)
    assert df is None and expect in msg


def test_load_live_rejects_synthetic_metadata_even_if_column_says_live(tmp_path):
    frame = make_live()
    frame.attrs["data_kind"] = "synthetic"
    import pyarrow as pa, pyarrow.parquet as pq
    table = pa.Table.from_pandas(frame)
    path = tmp_path / "meta.parquet"
    pq.write_table(table, path)
    df, msg = app._load_live(str(path), 4.0, VENUES)
    assert df is None and "synthetic" in msg
