"""C3.07: OLS-HAC/Driscoll-Kraay tự viết khớp công thức, và chạy end-to-end trên dữ liệu giả (không cần dữ liệu thật)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

try:
    import pyarrow  # noqa: F401
    EXT = "parquet"
except ImportError:                       # máy không có pyarrow: dùng CSV (script đọc cả hai)
    EXT = "csv"

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "analysis"))
import liquidity_regression as lr  # noqa: E402

VENUES = ["binance_spot", "binance_perp", "okx_spot", "okx_perp"]
SESSIONS = [("20261018_S1", "2026-10-18", 7), ("20261019_S1", "2026-10-19", 7), ("20261019_S2", "2026-10-19", 13),
            ("20261020_S1", "2026-10-20", 7), ("20261020_S2", "2026-10-20", 13), ("20261021_S1", "2026-10-21", 7),
            ("20261021_S2", "2026-10-21", 13)]       # 18/10 S1 = phiên lệch đồng hồ, bị loại khỏi B


# ===================================================================== phần toán
def test_ols_hac_lag0_equals_hc1_and_recovers_beta():
    rng = np.random.default_rng(0)
    n = 4000
    x = rng.normal(size=n)
    y = 1.0 + 0.5 * x + rng.normal(size=n)
    X = np.column_stack([np.ones(n), x])
    r = lr.ols_hac(y, X, np.zeros(n), lags=0)
    assert abs(r["beta"][1] - 0.5) < 0.05
    xtx_inv = np.linalg.inv(X.T @ X)
    u = y - X @ r["beta"]
    hc1 = xtx_inv @ (X.T * (u ** 2)) @ X @ xtx_inv * n / (n - 2)
    assert np.allclose(r["se"], np.sqrt(np.diag(hc1)))


def ar1(n, rho, rng):
    z = np.zeros(n)
    for i in range(1, n):
        z[i] = rho * z[i - 1] + rng.normal()
    return z


def test_hac_se_larger_than_naive_when_errors_autocorrelated():
    rng = np.random.default_rng(1)
    n = 3000
    x, e = ar1(n, 0.9, rng), ar1(n, 0.9, rng)
    X = np.column_stack([np.ones(n), x])
    y = 0.2 * x + e
    g = np.zeros(n)
    assert lr.ols_hac(y, X, g, lags=30)["se"][1] > 1.5 * lr.ols_hac(y, X, g, lags=0)["se"][1]


def test_driscoll_kraay_equals_group_hac_for_a_single_series_and_is_larger_with_cross_correlation():
    rng = np.random.default_rng(3)
    n = 1500
    x, e = ar1(n, 0.5, rng), ar1(n, 0.5, rng)
    y = 0.3 * x + e
    X = np.column_stack([np.ones(n), x])
    t = np.arange(n)
    one = lr.ols_hac(y, X, np.zeros(n), lags=10)
    dk = lr.ols_hac(y, X, np.zeros(n), lags=10, mode="dk", times=t)
    assert np.allclose(one["se"], dk["se"])
    # hai "venue" nhận CÙNG cú sốc ở mỗi thời điểm: DK phải nhận ra sự phụ thuộc, HAC theo nhóm thì không
    X2 = np.vstack([X, X])
    y2 = np.concatenate([y, y + rng.normal(scale=0.01, size=n)])
    grp = np.array(["a"] * n + ["b"] * n)
    t2 = np.concatenate([t, t])
    se_group = lr.ols_hac(y2, X2, grp, lags=10)["se"][1]
    se_dk = lr.ols_hac(y2, X2, grp, lags=10, mode="dk", times=t2)["se"][1]
    assert se_dk > 1.2 * se_group


# ===================================================================== dữ liệu giả
def save(df, tmp_path, name):
    p = tmp_path / f"{name}.{EXT}"
    df.to_parquet(p, index=False) if EXT == "parquet" else df.to_csv(p, index=False)
    return p


def load(tmp_path, name):
    p = tmp_path / f"{name}.{EXT}"
    return pd.read_parquet(p) if EXT == "parquet" else pd.read_csv(p)


def make_inputs(tmp_path, win_min=1439, step_min=60):
    """Giống rolling_is thật: cửa sổ ~1 ngày, bước 1 giờ, window_end làm mốc, available_at dồn về cuối mẫu phát triển."""
    rng = np.random.default_rng(2)
    mins = pd.date_range("2026-01-01", "2026-02-20 23:59", freq="1min", tz="UTC")
    feat = []
    for i, v in enumerate(VENUES):
        slow = 0.1 * np.sin(np.arange(len(mins)) / (1440 * (2 + i)) + i)
        feat.append(pd.DataFrame({"ts_utc": mins, "venue": v, "vol_share": 0.1 + 0.1 * i + slow + rng.normal(0, 0.01, len(mins)),
                                  "rv_60m": 3e-4 + 1e-4 * np.sin(np.arange(len(mins)) / 3000 + i) + rng.normal(0, 1e-5, len(mins))}))
    feat = pd.concat(feat)
    save(feat, tmp_path, "features")

    ends = pd.date_range("2026-01-03", "2026-02-20 23:00", freq=f"{step_min}min", tz="UTC")
    dev_end = pd.Timestamp("2026-01-20", tz="UTC")
    rows = []
    for v in VENUES:
        vs = feat[feat.venue == v].set_index("ts_utc")["vol_share"].rolling(f"{win_min}min").mean()
        for t in ends:
            val = 0.2 + 0.5 * (vs[t] - 0.25) + rng.normal(0, 0.01)
            rows.append({"window_start": t - pd.Timedelta(minutes=win_min), "window_end": t, "available_at": max(t, dev_end),
                         "venue": v, "is_lower": val - 0.1, "is_upper": val + 0.1, "is_mid": val, "component_share": 1e4 * rng.normal(),
                         "ils": 0.25 + 0.3 * (vs[t] - 0.25) + rng.normal(0, 0.01), "status": "ok", "reason": "overlapping_bounds"})
    rows.append({"window_start": ends[0], "window_end": ends[0], "available_at": ends[0], "venue": "okx_perp",
                 "is_lower": np.nan, "is_upper": np.nan, "is_mid": np.nan, "component_share": np.nan, "ils": np.nan,
                 "status": "error", "reason": "singular"})
    save(pd.DataFrame(rows), tmp_path, "rolling_is")


def make_live(tmp_path):
    rng = np.random.default_rng(4)
    q = []
    for sess, day, h in SESSIONS:
        grid = pd.date_range(f"{day} {h:02d}:00:05", periods=2159, freq="5s", tz="UTC")
        for v in VENUES:
            ex, mt = v.split("_")
            sp = 0.0012 if v == "binance_spot" else 0.0122
            q.append(pd.DataFrame({"ts_grid_utc": grid, "exchange": ex, "market_type": mt, "session_id": sess, "data_kind": "live",
                                   "bid": 80000.0 - sp * 4, "ask": 80000.0 + sp * 4,
                                   "depth_bid_5bps_usdt": rng.uniform(1e6, 3e6, len(grid)) * (1 + VENUES.index(v)),
                                   "depth_ask_5bps_usdt": rng.uniform(1e6, 3e6, len(grid)) * (1 + VENUES.index(v)),
                                   "depth_cover_bps": 15.0}))
    save(pd.concat(q), tmp_path, "live")
    rows = [{"session_id": s, "venue": v, "is_lower": 0.1 * i, "is_upper": 0.1 * i + 0.2, "ils": 0.2}
            for s, _, _ in SESSIONS for i, v in enumerate(VENUES)]
    save(pd.DataFrame(rows), tmp_path, "live_is")


def run_cli(tmp_path, *extra):
    out = tmp_path / "T4.csv"
    code = lr.main(["--rolling-is", str(tmp_path / f"rolling_is.{EXT}"), "--features", str(tmp_path / f"features.{EXT}"),
                    "--live", str(tmp_path / f"live.{EXT}"), "--out", str(out), *extra])
    return code, out


# ===================================================================== end-to-end
def test_model_a_aligns_on_window_end_not_available_at_and_recovers_vol_share_effect(tmp_path):
    make_inputs(tmp_path)
    code, out = run_cli(tmp_path)
    assert code == 0
    t4 = pd.read_csv(out)
    a = t4[t4.model == "A"].set_index("term")
    assert a.loc["vol_share_w (z)", "coef"] > 0 and a.loc["vol_share_w (z)", "p_normal"] < 0.01
    assert (t4.hac_lags == 24).all()                                    # cửa sổ 1439 / bước 60 -> 24
    assert set(t4.model) >= {"A", "A_fe", "A_ils", *[f"A_{v}" for v in VENUES]}
    assert a["n_obs"].iloc[0] == 4 * len(pd.date_range("2026-01-03", "2026-02-20 23:00", freq="60min")) # dòng status=error bị bỏ
    assert (a["cov"] == "dk").all()
    assert "component_share" not in set(t4.term)                        # CS không ổn định, không dùng làm biến


def test_fe_model_has_venue_dummies_and_no_is_perp(tmp_path):
    make_inputs(tmp_path)
    t4 = pd.read_csv(run_cli(tmp_path)[1])
    fe = t4[t4.model == "A_fe"]
    assert "is_perp" not in set(fe.term) and sum(t.startswith("fe_venue") for t in fe.term) == 3


def test_model_b_skipped_with_clear_message_when_no_live_is(tmp_path, capsys):
    make_inputs(tmp_path)
    make_live(tmp_path)
    t4 = pd.read_csv(run_cli(tmp_path)[1])
    assert not t4.model.str.startswith("B").any()
    assert "Mô hình B: bỏ qua" in capsys.readouterr().out


def test_model_b_with_live_is_drops_skew_session(tmp_path):
    make_inputs(tmp_path)
    make_live(tmp_path)
    save(pd.read_csv(tmp_path / "live_is.csv") if EXT == "csv" else load(tmp_path, "live_is"), tmp_path, "live_is")
    t4 = pd.read_csv(run_cli(tmp_path, "--live-is", str(tmp_path / f"live_is.{EXT}"))[1])
    b = t4[t4.model == "B"]
    assert b["n_obs"].iloc[0] == 24 and b["n_groups"].iloc[0] == 6         # 6 phiên (đã loại 18/10 S1) × 4 venue
    assert {"spread_bps (z)", "log_depth (z)", "is_perp"} <= set(b.term)
    assert (t4[t4.model == "B_no_spread"].term != "spread_bps (z)").all()
    t4k = pd.read_csv(run_cli(tmp_path, "--live-is", str(tmp_path / f"live_is.{EXT}"), "--keep-skew")[1])
    assert t4k[t4k.model == "B"]["n_obs"].iloc[0] == 28


def test_unmatched_venue_names_fail_loudly(tmp_path):
    make_inputs(tmp_path)
    f = load(tmp_path, "features")
    f["venue"] = f["venue"].str.split("_").str[0]                 # 'binance' thay vì 'binance_spot'
    save(f, tmp_path, "features")
    with pytest.raises(SystemExit, match="không khớp"):
        run_cli(tmp_path)


def test_wide_features_are_accepted(tmp_path):
    make_inputs(tmp_path)
    f = load(tmp_path, "features")
    wide = f.pivot(index="ts_utc", columns="venue", values="vol_share").add_prefix("vol_share__").reset_index()
    wide["rv_60m"] = f.groupby("ts_utc")["rv_60m"].mean().to_numpy()
    save(wide, tmp_path, "features")
    code, out = run_cli(tmp_path)
    assert code == 0 and "A" in set(pd.read_csv(out).model)


def test_crosscheck_runs_without_statsmodels_or_matches_it(tmp_path, capsys):
    make_inputs(tmp_path)
    run_cli(tmp_path, "--crosscheck")
    out = capsys.readouterr().out
    assert "crosscheck" in out
    if "chưa cài statsmodels" not in out:
        assert "SE HAC lệch tương đối tối đa" in out