"""run_live_is: ghép phiên/venue/đặc tả và các kiểm tra nguồn, kiểm bằng hàm giả (không cần statsmodels hay dữ liệu thật)."""
import json
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "price_discovery"))
import run_live_is as r  # noqa: E402

VENUES = ["binance_spot", "binance_perp", "okx_spot", "okx_perp"]
SPEC = {"venues": VENUES + ["coinbase_spot"], "k_ar_diff": 3, "coint_rank": 5, "deterministic": "co",
        "det_order": 0, "selected_available_at": "2026-05-14T19:11:00+00:00", "data_kind": "historical"}
SESSIONS = [("20261018_S1", "2026-10-18 07:00"), ("20261019_S1", "2026-10-19 07:00"), ("20261019_S2", "2026-10-19 13:00")]


def quotes(skip_venue=None, kind="live"):
    rows = []
    rng = np.random.default_rng(0)
    for sid, start in SESSIONS:
        ts = pd.date_range(start, periods=2159, freq="5s", tz="UTC")
        for v in VENUES:
            if v == skip_venue:
                continue
            ex, mt = v.split("_")
            rows.append(pd.DataFrame({"session_id": sid, "exchange": ex, "market_type": mt, "ts_recv_utc": ts, "data_kind": kind,
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
    def __init__(self, fail=False):
        self.specs, self.fail = [], fail

    def __call__(self, logs, spec):
        self.specs.append(spec)
        if self.fail:
            raise ValueError("johansen_rank=0: IS/CS need one common trend")
        n = logs.shape[1]
        return pd.DataFrame({"venue": list(logs.columns), "is_lower": 0.1, "is_upper": 0.4, "is_mid": 0.25,
                             "component_share": 1 / n, "status": "ok", "reason": ""})


def pairs(logs, spec):
    return pd.DataFrame({"venue": list(logs.columns), "ils": 0.3, "status": "ok"})


def run(q, shares=None, **kw):
    shares = shares or Shares()
    out = r.build_live_is(q, SPEC, align_fn=fake_align, logs_fn=real_logs, shares_fn=shares, pairs_fn=pairs, say=lambda *_: None, **kw)
    return out, shares


# ===================================================================== logic
def test_each_session_estimated_separately_and_exclude_works():
    out, shares = run(quotes())
    assert set(out.session_id) == {"20261018_S1", "20261019_S1", "20261019_S2"}      # mặc định không loại phiên nào
    assert (out.status == "ok").all() and len(shares.specs) == 3                    # một lần ước lượng mỗi phiên
    out2, _ = run(quotes(), exclude=("20261018_S1",))
    assert set(out2.session_id) == {"20261019_S1", "20261019_S2"}
    assert out.n_obs.between(170, 181).all()


def test_spec_restricted_to_live_venues_one_common_trend_and_locked_lag():
    out, shares = run(quotes())
    for spec in shares.specs:
        assert spec["venues"] == VENUES and spec["coint_rank"] == 3 and spec["k_ar_diff"] == 3
        assert set(spec["pair_ranks"].values()) == {1} and len(spec["pair_ranks"]) == 6
    _, shares2 = run(quotes(), k_ar_diff=1)
    assert all(s["k_ar_diff"] == 1 for s in shares2.specs)


def test_missing_venue_in_session_is_dropped_not_filled():
    out, shares = run(quotes(skip_venue="okx_perp"))
    assert "okx_perp" not in set(out.venue) and all(s["coint_rank"] == 2 for s in shares.specs)


def test_internal_gap_fails_session_with_reason_instead_of_raising():
    q = quotes()
    bad = ((q.session_id == "20261019_S1") & (q.exchange == "okx") & (q.market_type == "spot")
           & q.ts_recv_utc.between("2026-10-19 08:00", "2026-10-19 08:10"))
    out, _ = run(q[~bad])
    s1 = out[out.session_id == "20261019_S1"]
    assert (s1.status == "failed").all() and s1.reason.str.contains("internal gaps").all()
    assert (out[out.session_id == "20261019_S2"].status == "ok").all()


# ===================================================================== kiểm tra nguồn / giữ file cũ
def deps(q, shares=None):
    def write_frame(df, path):                    # thay to_parquet (sandbox không có pyarrow)
        df.to_csv(path, index=False)
    return types.SimpleNamespace(align=fake_align, logs=real_logs, shares=shares or Shares(), pairs=pairs,
                                 read_parquet=lambda path, schema: q.copy(), write_frame=write_frame)


def setup(tmp_path, spec=SPEC):
    spec_path = tmp_path / "T2_vecm_spec.json"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    out = tmp_path / "live_session_is.parquet"
    out.write_text("OLD RESULT", encoding="utf-8")
    argv = ["--quotes", str(tmp_path / "live_quotes_long.parquet"), "--spec", str(spec_path), "--out", str(out)]
    return argv, out


def test_good_run_writes_atomically_without_tmp_leftover(tmp_path):
    argv, out = setup(tmp_path)
    res = r.main(argv, deps(quotes()))
    assert out.read_text(encoding="utf-8") != "OLD RESULT" and len(pd.read_csv(out)) == len(res) == 12
    assert not list(tmp_path.glob("*.tmp"))


def test_synthetic_quotes_are_rejected_and_old_file_kept(tmp_path):
    argv, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="cần 'live'"):
        r.main(argv, deps(quotes(kind="synthetic")))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"


def test_quotes_without_any_kind_label_are_rejected(tmp_path):
    argv, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="cần 'live'"):
        r.main(argv, deps(quotes().drop(columns="data_kind")))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"


def test_synthetic_spec_is_rejected_and_old_file_kept(tmp_path):
    argv, out = setup(tmp_path, spec={**SPEC, "data_kind": "synthetic"})
    with pytest.raises(SystemExit, match="historical"):
        r.main(argv, deps(quotes()))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"


def test_nothing_left_after_exclusion_is_an_error_and_old_file_kept(tmp_path):
    argv, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="Không còn dữ liệu live"):
        r.main(argv + ["--exclude-sessions", "20261018_S1,20261019_S1,20261019_S2"], deps(quotes()))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"


def test_all_sessions_failed_is_an_error_and_old_file_kept(tmp_path):
    argv, out = setup(tmp_path)
    with pytest.raises(SystemExit, match="Không phiên nào ước lượng được"):
        r.main(argv, deps(quotes(), Shares(fail=True)))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"


def test_synthetic_directory_in_any_path_is_rejected(tmp_path):
    d = tmp_path / "synthetic"
    d.mkdir()
    argv, out = setup(d)
    with pytest.raises(SystemExit, match="synthetic"):
        r.main(argv, deps(quotes()))
    assert out.read_text(encoding="utf-8") == "OLD RESULT"