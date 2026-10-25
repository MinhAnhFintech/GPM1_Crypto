"""run_live_is: logic ghép phiên/venue/đặc tả, kiểm bằng hàm giả (không cần statsmodels hay dữ liệu thật)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "price_discovery"))
import src.price_discovery.run_live_is as r  # noqa: E402

VENUES = ["binance_spot", "binance_perp", "okx_spot", "okx_perp"]
SPEC = {"venues": VENUES + ["coinbase_spot"], "k_ar_diff": 3, "coint_rank": 5, "deterministic": "co",
        "det_order": 0, "selected_available_at": "2026-05-14T19:11:00+00:00"}
SESSIONS = [("20261018_S1", "2026-10-18 07:00"), ("20261019_S1", "2026-10-19 07:00"), ("20261019_S2", "2026-10-19 13:00")]


def quotes(skip_venue=None):
    rows = []
    rng = np.random.default_rng(0)
    for sid, start in SESSIONS:
        ts = pd.date_range(start, periods=2159, freq="5s", tz="UTC")
        for v in VENUES:
            if v == skip_venue:
                continue
            ex, mt = v.split("_")
            rows.append(pd.DataFrame({"session_id": sid, "exchange": ex, "market_type": mt, "ts_recv_utc": ts,
                                      "mid": 80000 * np.exp(np.cumsum(rng.normal(0, 1e-5, len(ts))))}))
    return pd.concat(rows, ignore_index=True)


def fake_align(q, *, grid_freq, max_age):
    cols = {}
    for (ex, mt), g in q.groupby(["exchange", "market_type"]):
        cols[f"mid__{ex}_{mt}"] = g.set_index("ts_recv_utc")["mid"].resample(grid_freq).last()
    wide = pd.DataFrame(cols)
    wide.index.name = "ts_available"
    return wide


def real_logs(panel, venues, frac):
    if panel.isna().any().any():
        raise ValueError("missing_prices: internal gaps cannot be dropped or interpolated")
    out = panel.copy()
    out.columns = venues
    return out


class Shares:
    def __init__(self, fail_session=None):
        self.specs, self.fail_session = [], fail_session

    def __call__(self, logs, spec):
        self.specs.append(spec)
        n = logs.shape[1]
        return pd.DataFrame({"venue": list(logs.columns), "is_lower": 0.1, "is_upper": 0.4, "is_mid": 0.25,
                             "component_share": 1 / n, "status": "ok", "reason": ""})


def pairs(logs, spec):
    return pd.DataFrame({"venue": list(logs.columns), "ils": 0.3, "status": "ok"})


def run(q, shares=None, **kw):
    shares = shares or Shares()
    out = r.build_live_is(q, SPEC, align_fn=fake_align, logs_fn=real_logs, shares_fn=shares, pairs_fn=pairs, say=lambda *_: None, **kw)
    return out, shares


def test_skew_session_excluded_and_each_session_estimated_separately():
    out, shares = run(quotes())
    expected_sessions = {
    "20261018_S1",
    "20261019_S1",
    "20261019_S2",
    }

    assert set(out["session_id"]) == expected_sessions
    assert (out.status == "ok").all()
    assert out.groupby("session_id").venue.nunique().eq(4).all()
    assert len(shares.specs) == 3                               # một lần ước lượng cho mỗi phiên, không ghép
    assert out.n_obs.between(170, 181).all()
    out_keep, _ = run(quotes(), exclude=())
    assert out_keep.session_id.nunique() == 3


def test_spec_is_restricted_to_live_venues_with_one_common_trend_and_keeps_locked_lag():
    out, shares = run(quotes())
    for spec in shares.specs:
        assert spec["venues"] == VENUES                            # coinbase_spot không có trong D2 -> bị bỏ
        assert spec["coint_rank"] == 3 and spec["k_ar_diff"] == 3  # hạng = n - 1, số trễ giữ nguyên bản khóa
        assert set(spec["pair_ranks"].values()) == {1} and len(spec["pair_ranks"]) == 6
    out2, shares2 = run(quotes(), k_ar_diff=1)
    assert all(s["k_ar_diff"] == 1 for s in shares2.specs) and (out2.k_ar_diff == 1).all()


def test_missing_venue_in_session_is_dropped_not_filled():
    out, shares = run(quotes(skip_venue="okx_perp"))
    assert "okx_perp" not in set(out.venue)
    assert all(s["coint_rank"] == 2 for s in shares.specs)


def test_internal_gap_fails_session_with_reason_instead_of_raising():
    q = quotes()
    bad = (q.session_id == "20261019_S1") & (q.exchange == "okx") & (q.market_type == "spot") & \
          (q.ts_recv_utc.between("2026-10-19 08:00", "2026-10-19 08:10"))
    out, _ = run(q[~bad])
    s1 = out[out.session_id == "20261019_S1"]
    assert (s1.status == "failed").all() and s1.reason.str.contains("internal gaps").all()
    assert (out[out.session_id == "20261019_S2"].status == "ok").all()


def test_output_columns_and_data_kind():
    out, _ = run(quotes())
    assert list(out.columns) == r.OUT_COLS and (out.data_kind == "live").all()
    assert out.loc[out.status == "ok", "ils"].eq(0.3).all()