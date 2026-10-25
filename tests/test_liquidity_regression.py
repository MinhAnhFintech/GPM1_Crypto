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