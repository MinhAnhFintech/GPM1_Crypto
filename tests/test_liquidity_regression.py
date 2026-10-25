"""Đặc tả mô hình B (C3.07): B = rv_live + spread + depth; B_no_rv, B_no_spread là độ nhạy; B_vol khi có nến. Dữ liệu giả, không mạng."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "notebooks" / "tv3"))
import liquidity_regression as lr  # noqa: E402

VENUES = ["binance_spot", "binance_perp", "okx_spot", "okx_perp", "bybit_spot", "bybit_perp"]
SESSIONS = ["20261017_S2", "20261018_S1", "20261019_S1", "20261019_S2", "20261020_S1", "20261020_S2", "20261021_S1", "20261021_S2"]
NO_DEPTH, SHORT_COVER = "20261017_S2", "20261020_S1"      # phiên không đo depth; phiên có bybit_perp bị cắt dưới 5 bps


def session_times(si, ticks=121):
    return pd.date_range("2026-10-17 13:00", periods=ticks, freq="5s", tz="UTC") + pd.Timedelta(days=si)   # 121 tick = 10 phút


def make_inputs(tmp_path, with_candles=False, ticks=121):
    rng = np.random.default_rng(11)
    t0 = pd.Timestamp("2026-01-02 00:00", tz="UTC")
    rows = []
    for v in VENUES:
        for i in range(300):
            e = t0 + pd.Timedelta(hours=i)
            lo = rng.uniform(0, 0.3)
            rows.append({"venue": v, "window_start": e - pd.Timedelta(minutes=1440), "window_end": e, "is_lower": lo,
                         "is_upper": lo + rng.uniform(0.5, 0.7), "ils": rng.uniform(0.3, 0.7), "status": "ok",
                         "data_kind": "historical"})
    pd.DataFrame(rows).to_parquet(tmp_path / "rolling_is.parquet", index=False)

    minutes = pd.date_range("2026-01-01 00:00", "2026-01-14 12:00", freq="min", tz="UTC")
    pd.concat([pd.DataFrame({"ts_utc": minutes, "venue": v, "vol_share": rng.uniform(0.1, 0.3, len(minutes)),
                             "rv_60m": rng.uniform(1e-4, 1e-3, len(minutes)), "data_kind": "historical"}) for v in VENUES]
              ).to_parquet(tmp_path / "features_1m.parquet", index=False)

    q, li, candles = [], [], []
    for si, s in enumerate(SESSIONS):
        ts = session_times(si, ticks)
        for vi, v in enumerate(VENUES):
            depth = np.nan if s == NO_DEPTH else rng.uniform(1e5, 5e5)
            cover = np.nan if s == NO_DEPTH else (4.0 if (s == SHORT_COVER and v == "bybit_perp") else 9.0)
            mid = 100.0 * np.exp(np.cumsum(rng.normal(scale=1e-4 * (1 + vi), size=len(ts))))      # mid biến động, khác nhau theo venue
            q.append(pd.DataFrame({"session_id": s, "venue": v, "data_kind": "live", "ts_grid_utc": ts, "bid": mid - 0.005,
                                   "ask": mid + 0.005, "spread_bps": rng.uniform(0.5, 1.5), "depth_bid_5bps_usdt": depth,
                                   "depth_ask_5bps_usdt": depth, "depth_cover_bps": cover}))
            lo = rng.uniform(0, 0.3)
            li.append({"session_id": s, "venue": v, "status": "ok", "is_lower": lo, "is_upper": lo + 0.6, "data_kind": "live"})
            cm = pd.date_range(ts[0].floor("min"), ts[-1].floor("min"), freq="min", tz="UTC")
            candles.append(pd.DataFrame({"ts_open_utc": cm, "venue": v, "data_kind": "historical",
                                         "volume_base": (vi + 1) * (1 + 0.2 * si * (vi % 3)) * rng.uniform(0.9, 1.1, len(cm))}))
    pd.concat(q).to_parquet(tmp_path / "live_quotes_long.parquet", index=False)
    pd.DataFrame(li).to_parquet(tmp_path / "live_is.parquet", index=False)
    if with_candles:
        pd.concat(candles).to_parquet(tmp_path / "candles.parquet", index=False)


def run_main(tmp_path, *extra, out="T4.csv"):
    path = tmp_path / out
    code = lr.main(["--rolling-is", str(tmp_path / "rolling_is.parquet"), "--features", str(tmp_path / "features_1m.parquet"),
                    "--live", str(tmp_path / "live_quotes_long.parquet"), "--live-is", str(tmp_path / "live_is.parquet"),
                    "--out", str(path), *extra])
    assert code == 0
    return pd.read_csv(path)


def terms(t4, model):
    return list(t4.loc[t4.model == model, "term"])


# ------------------------------------------------------------------ đặc tả B
def test_b_contains_a_base_volatility_plus_spread_and_depth(tmp_path):
    make_inputs(tmp_path)
    t4 = run_main(tmp_path)
    assert terms(t4, "B") == ["const", "rv_live (z)", "spread_bps (z)", "log_depth (z)", "is_perp"]
    assert "rv_live (z)" not in terms(t4, "B_no_rv") and "spread_bps (z)" in terms(t4, "B_no_rv")   # đặc tả B cũ, làm độ nhạy
    assert "spread_bps (z)" not in terms(t4, "B_no_spread") and "rv_live (z)" in terms(t4, "B_no_spread")
    assert "B_vol" not in set(t4.model)                                    # không có nến -> không có B_vol
    assert "B_rv" not in set(t4.model)
    assert t4.loc[t4.model == "B", "notes"].str.contains("chưa có vol_share").all()


def test_b_sample_drops_whole_sessions_with_reason(tmp_path, capsys):
    make_inputs(tmp_path)
    t4 = run_main(tmp_path)
    log = capsys.readouterr().out
    assert f"loại phiên {NO_DEPTH}" in log and f"loại phiên {SHORT_COVER}" in log
    b = t4[t4.model == "B"]
    assert b["n_obs"].eq(36).all() and b["n_groups"].eq(6).all()        # 8 phiên - 2 phiên thiếu depth = 6 phiên x 6 venue
    assert b["notes"].str.contains("6/8 phiên").all() and b["notes"].str.contains(NO_DEPTH).all()


def test_rv_live_is_measured_from_live_mid_and_varies_by_venue(tmp_path):
    make_inputs(tmp_path)
    q = pd.read_parquet(tmp_path / "live_quotes_long.parquet")
    lm = lr.live_minutes(q, 5, [])
    rv = lr.session_rv(lm)
    assert rv["rv_live"].notna().all() and rv["rv_live"].std() > 0
    means = rv.groupby("venue").rv_live.mean().reindex(VENUES)           # dữ liệu giả: venue sau có biến động mid lớn hơn
    assert np.corrcoef(np.arange(len(VENUES)), means.to_numpy())[0, 1] > 0.9


# ------------------------------------------------------------------ B_vol
def test_session_vol_share_is_minute_share_averaged_over_session():
    t = pd.date_range("2026-10-17 13:00", periods=5, freq="min", tz="UTC")
    candles = pd.concat([pd.DataFrame({"ts_open_utc": t, "venue": "a", "volume_base": 1.0}),
                         pd.DataFrame({"ts_open_utc": t, "venue": "b", "volume_base": 3.0})])
    lm = pd.DataFrame({"session_id": "s", "venue": "a", "t": t})
    out = lr.session_vol_share(candles, lm).set_index("venue")["vol_share"]
    assert out["a"] == pytest.approx(0.25) and out["b"] == pytest.approx(0.75) and out.sum() == pytest.approx(1.0)


def test_session_vol_share_needs_enough_minutes_and_does_not_fill():
    t = pd.date_range("2026-10-17 13:00", periods=10, freq="min", tz="UTC")
    full = pd.DataFrame({"ts_open_utc": t, "venue": "a", "volume_base": 1.0})
    gappy = pd.DataFrame({"ts_open_utc": t[:6], "venue": "b", "volume_base": 1.0})     # chỉ 6/10 phút < 80%
    lm = pd.DataFrame({"session_id": "s", "venue": "a", "t": t})
    out = lr.session_vol_share(pd.concat([full, gappy]), lm).set_index("venue")["vol_share"]
    assert np.isnan(out["b"]) and out["a"] > 0


def test_b_vol_added_with_candles_and_other_models_unchanged(tmp_path):
    make_inputs(tmp_path, with_candles=True)
    plain = run_main(tmp_path, out="plain.csv")
    t4 = run_main(tmp_path, "--live-candles", str(tmp_path / "candles.parquet"), out="with_vol.csv")
    assert terms(t4, "B_vol") == ["const", "vol_share (z)", "rv_live (z)", "spread_bps (z)", "log_depth (z)", "is_perp"]
    assert t4.loc[t4.model == "B_vol", "n_obs"].eq(36).all()
    keep = ["model", "term", "coef", "se_hac", "p_value"]
    pd.testing.assert_frame_equal(plain[plain.model != "B_vol"][keep].reset_index(drop=True),
                                  t4[t4.model != "B_vol"][keep].reset_index(drop=True))     # thêm nến không làm đổi A, B, độ nhạy


# ------------------------------------------------------------------ kiểm nguồn nến, suy luận theo cụm
def test_synthetic_candles_are_rejected_in_real_zone_and_old_file_kept(tmp_path):
    make_inputs(tmp_path, with_candles=True)
    c = pd.read_parquet(tmp_path / "candles.parquet").assign(data_kind="synthetic")
    c.to_parquet(tmp_path / "candles.parquet", index=False)
    out = tmp_path / "T4.csv"
    out.write_text("old\n")
    with pytest.raises(SystemExit, match="live_candles"):
        lr.main(["--rolling-is", str(tmp_path / "rolling_is.parquet"), "--features", str(tmp_path / "features_1m.parquet"),
                 "--live", str(tmp_path / "live_quotes_long.parquet"), "--live-is", str(tmp_path / "live_is.parquet"),
                 "--live-candles", str(tmp_path / "candles.parquet"), "--out", str(out)])
    assert out.read_text() == "old\n"


def test_cluster_se_matches_statsmodels_and_group_mode_does_not():
    sm = pytest.importorskip("statsmodels.api")
    rng = np.random.default_rng(5)
    g = np.repeat(np.arange(8), 6)
    X = np.column_stack([np.ones(48), rng.normal(size=48), rng.normal(size=48)])
    y = 0.2 + rng.normal(scale=0.2, size=48) + np.repeat(rng.normal(scale=0.15, size=8), 6)
    ref = sm.OLS(y, X).fit(cov_type="cluster", cov_kwds={"groups": g})
    np.testing.assert_allclose(lr.ols_hac(y, X, g.astype(str), 0, mode="cluster")["se"], ref.bse, rtol=1e-8)
    assert not np.allclose(lr.ols_hac(y, X, g.astype(str), 0, mode="group")["se"], ref.bse, rtol=1e-3)   # HC1 theo dòng, khác theo cụm


def test_sessions_too_short_for_rv_are_dropped_with_reason_and_nothing_written(tmp_path, capsys):
    make_inputs(tmp_path, ticks=61)                                   # 6 phút mid -> 5 lợi suất < 6: không tính được rv_live
    out = tmp_path / "T4.csv"
    out.write_text("old\n")
    with pytest.raises(SystemExit, match="mô hình B chính"):
        lr.main(["--rolling-is", str(tmp_path / "rolling_is.parquet"), "--features", str(tmp_path / "features_1m.parquet"),
                 "--live", str(tmp_path / "live_quotes_long.parquet"), "--live-is", str(tmp_path / "live_is.parquet"),
                 "--out", str(out)])
    assert "thiếu rv_live" in capsys.readouterr().out
    assert out.read_text() == "old\n"                                   # không ghi T4 chỉ còn B_no_rv