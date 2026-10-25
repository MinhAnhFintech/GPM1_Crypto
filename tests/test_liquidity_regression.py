"""C3.07: A_live / B dùng biến của mẫu live (rv_live, depth_share), cùng quan sát, B = A_live + spread + log_depth."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("liquidity_regression", ROOT / "notebooks" / "tv3" / "liquidity_regression.py")
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)

VENUES = ["binance_spot", "binance_perp", "okx_spot", "okx_perp", "bybit_spot", "bybit_perp"]


def fake(n_sessions=12, minutes=25, seed=0, drop_depth_session=None):
    rng = np.random.default_rng(seed)
    rows_lm, rows_li = [], []
    for s in range(n_sessions):
        sid = f"2026-10-{17 + s // 2}_S{s % 2 + 1}"
        t0 = pd.Timestamp("2026-10-17", tz="UTC") + pd.Timedelta(hours=3 * s)
        base = 70000 + 100 * rng.normal()
        for i, v in enumerate(VENUES):
            level = 1e6 * (i + 1) * (1 + 0.3 * rng.random())
            mid = base * np.exp(np.cumsum(rng.normal(0, 1e-4, minutes)))
            for m in range(minutes):
                d = np.log(level) if sid != drop_depth_session else np.nan
                rows_lm.append({"venue": v, "session_id": sid, "t": t0 + pd.Timedelta(minutes=m), "spread_bps": 0.01 + 0.001 * i,
                                "log_depth": d, "mid_last": mid[m], "n_ticks": 10})
            rows_li.append({"session_id": sid, "venue": v, "is_val": 0.1 + 0.05 * i + 0.01 * rng.normal()})
    return pd.DataFrame(rows_li), pd.DataFrame(rows_lm)


def test_depth_share_sums_to_one_per_session_and_is_not_volume():
    li, lm = fake()
    m, excluded = lr.model_b_frame(li, lm, 5)
    assert not excluded and "vol_share" not in m.columns
    assert np.allclose(m.groupby("session_id")["depth_share"].sum(), 1.0)
    assert (m["depth_share"] > 0).all()
    assert m.loc[m.venue == "okx_spot", "depth_share"].mean() < m.loc[m.venue == "bybit_perp", "depth_share"].mean()


def test_session_without_depth_is_excluded_whole_with_reason():
    li, lm = fake(drop_depth_session="2026-10-18_S1")
    m, excluded = lr.model_b_frame(li, lm, 5)
    assert "2026-10-18_S1" in excluded and "độ sâu" in excluded["2026-10-18_S1"]
    assert "2026-10-18_S1" not in set(m.session_id)
    assert np.allclose(m.groupby("session_id")["depth_share"].sum(), 1.0)


def test_a_live_and_b_use_same_rows_and_b_adds_exactly_spread_and_depth():
    li, lm = fake()
    m, _ = lr.model_b_frame(li, lm, 5)
    a_x = ["rv_live", "depth_share"]
    base_x = a_x + ["spread_bps", "log_depth"]
    a = lr.fit(m, "A_live", a_x + ["is_perp"], a_x, 0, "cluster", group_col="session_id")
    b = lr.fit(m, "B", base_x + ["is_perp"], base_x, 0, "cluster", group_col="session_id")
    assert a.n_obs.iloc[0] == b.n_obs.iloc[0] == len(m) and a.n_groups.iloc[0] == b.n_groups.iloc[0] == 12
    ta, tb = set(a.term), set(b.term)
    assert tb - ta == {"spread_bps (z)", "log_depth (z)"} and ta <= tb
    assert "vol_share_w (z)" not in tb and "rv_60m (z)" not in tb
    assert (b["cov"] == "cluster").all() and (a["cov"] == "cluster").all()


# ---------------------------------------------------------------- các lỗi P2 đã sửa
def raw_quotes(n=40, seed=1):
    rng = np.random.default_rng(seed)
    rows = []
    t0 = pd.Timestamp("2026-10-17 00:00:00", tz="UTC")
    for v in ["binance_spot", "okx_spot"]:
        mid = 70000 * np.exp(np.cumsum(rng.normal(0, 1e-4, n * 6)))
        for i in range(n * 6):
            ts = t0 + pd.Timedelta(seconds=10 * i)
            rows.append({"venue": v, "session_id": "S1", "ts_recv_utc": ts, "ts_grid_utc": ts - pd.Timedelta(seconds=3),
                         "bid": mid[i] - 0.5, "ask": mid[i] + 0.5, "depth_bid_5bps_usdt": 1e6, "depth_ask_5bps_usdt": 1e6,
                         "depth_cover_bps": 10.0, "data_kind": "live"})
    return pd.DataFrame(rows)


def test_rv_live_does_not_depend_on_file_row_order():
    q = raw_quotes()
    a = lr.session_rv(lr.live_minutes(q, 5, []))
    b = lr.session_rv(lr.live_minutes(q.sample(frac=1.0, random_state=3).reset_index(drop=True), 5, []))
    pd.testing.assert_frame_equal(a.sort_values("venue").reset_index(drop=True), b.sort_values("venue").reset_index(drop=True))


def test_minutes_use_receive_time_not_grid_time():
    q = raw_quotes(n=5)
    # quote có ts_grid ở phút 0 nhưng NHẬN ở phút 1 phải nằm ở phút 1
    q.loc[0, "ts_recv_utc"] = pd.Timestamp("2026-10-17 00:00:59", tz="UTC")
    q.loc[0, "ts_grid_utc"] = pd.Timestamp("2026-10-17 00:00:57", tz="UTC")
    q.loc[1, "ts_recv_utc"] = pd.Timestamp("2026-10-17 00:01:02", tz="UTC")
    q.loc[1, "ts_grid_utc"] = pd.Timestamp("2026-10-17 00:00:59", tz="UTC")     # grid còn ở phút 0
    lm = lr.live_minutes(q, 5, [])
    first_min = lm[(lm.venue == "binance_spot")].sort_values("t")
    assert first_min["t"].iloc[0] == pd.Timestamp("2026-10-17 00:00:00", tz="UTC")
    # phút 0 có 6 tick theo ts_grid (cả quote thứ hai), nhưng theo ts_recv chỉ 5: quote thứ hai nhận ở 00:01:02 thuộc phút 1
    assert int(first_min["n_ticks"].iloc[0]) == 5


def test_missing_ts_recv_is_refused():
    q = raw_quotes(n=3).drop(columns="ts_recv_utc")
    try:
        lr.live_minutes(q, 5, [])
        raise AssertionError("phải từ chối")
    except SystemExit as exc:
        assert "ts_recv_utc" in str(exc)


def test_feature_window_uses_all_win_observations_including_first_minute():
    t = pd.date_range("2026-03-01", periods=30, freq="min", tz="UTC")
    feat = pd.DataFrame({"t": t, "venue": "v", "vol_share": np.arange(30, dtype=float), "rv_60m": 1.0})
    out = lr.feature_windows(feat, 10).set_index("t")
    assert out["vol_share_w"].iloc[-1] == np.mean(np.arange(20, 30))             # đúng 10 phút, gồm cả phút đầu cửa sổ


def test_window_from_window_start_end_counts_1440_observations():
    import types
    end = pd.date_range("2026-03-02", periods=5, freq="60min", tz="UTC")
    df = pd.DataFrame({"window_end": end, "window_start": end - pd.Timedelta(minutes=1439), "venue": "v", "is_mid": 0.5,
                       "status": "ok"})
    a = types.SimpleNamespace(time_col=None, is_col=None, venue_col=None, window_min=None, step_min=None)
    _, win, step = lr.tidy_is(df, a)
    assert win == 1440.0 and step == 60.0


def test_session_with_missing_venue_is_excluded_whole():
    li, lm = fake()
    li = li[~((li.session_id == "2026-10-19_S2") & (li.venue == "okx_perp"))]
    m, excluded = lr.model_b_frame(li, lm, 5)
    assert "2026-10-19_S2" in excluded and "okx_perp" in excluded["2026-10-19_S2"]
    assert "2026-10-19_S2" not in set(m.session_id)
    assert np.allclose(m.groupby("session_id")["depth_share"].sum(), 1.0)


def test_unlabeled_features_refused_unless_flag():
    df = pd.DataFrame({"a": [1]})
    out = Path("outputs/tables/T4.csv")
    try:
        lr.guard_input(df, "features_1m", out, required=True)
        raise AssertionError("phải từ chối")
    except SystemExit:
        pass
    lr.guard_input(df, "features_1m", out, required=False)           # --allow-unlabeled-features: chỉ cảnh báo