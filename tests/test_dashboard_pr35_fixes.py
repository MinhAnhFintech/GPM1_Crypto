"""PR #35: synchronized live quotes and source-consistent presentation."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("app_pr35_fixes", ROOT / "dashboard/app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_live_mid_dispersion_has_no_future_stale_or_cross_market_prices():
    start = pd.Timestamp("2026-10-20 10:00:00", tz="UTC")
    frame = pd.DataFrame([
        (start, "s1", "a_spot", 100.),
        (start + pd.Timedelta(seconds=2), "s1", "b_spot", 110.),
        (start, "s1", "a_perp", 10000.),
        (start, "s1", "b_perp", 10000.),
        (start + pd.Timedelta(seconds=10), "s1", "a_spot", 200.),
    ], columns=["ts_recv_utc", "session_id", "venue", "mid"])
    frame["ts_recv_utc"] = frame.ts_recv_utc.astype("datetime64[ns, UTC]")
    venues = ("a_spot", "b_spot", "a_perp", "b_perp")
    before = frame.copy(deep=True)
    out = app.live_mid_dispersion(frame, venues).set_index(["market_type", "ts_available"])
    assert np.isnan(out.loc[("spot", start), "dispersion_bps"])  # b quote has not arrived
    assert out.loc[("spot", start + pd.Timedelta(seconds=5)), "dispersion_bps"] == pytest.approx(10 / 105 * 1e4)
    assert out.loc[("perp", start), "dispersion_bps"] == 0  # no comparison with spot
    assert np.isnan(out.loc[("spot", start + pd.Timedelta(seconds=10)), "dispersion_bps"])  # b is stale
    pd.testing.assert_frame_equal(frame, before)


def test_live_mid_dispersion_requires_all_research_venues_and_same_session():
    t = pd.Timestamp("2026-10-20 10:00:00", tz="UTC")
    frame = pd.DataFrame({"ts_recv_utc": [t, t], "venue": ["a_spot", "b_spot"],
                          "session_id": ["s1", "s2"], "mid": [100., 110.]})
    frame["ts_recv_utc"] = frame.ts_recv_utc.astype("datetime64[ns, UTC]")
    out = app.live_mid_dispersion(frame, ("a_spot", "b_spot"))
    assert out.dispersion_bps.isna().all() and out.n_available.eq(1).all()
    frame["session_id"] = "s1"
    out = app.live_mid_dispersion(frame, ("a_spot", "b_spot", "c_spot"))
    assert out.dispersion_bps.isna().all() and out.n_expected.eq(3).all()


def gap_page_source(live_available=True):
    return f'''
import pandas as pd
from types import SimpleNamespace
from dashboard import app
def events(gross, net):
    return pd.DataFrame(dict(event_id=["e"], ts_start=[pd.Timestamp("2026-10-20", tz="UTC")],
        ts_end=[pd.Timestamp("2026-10-20 00:01", tz="UTC")], duration_s=[60.], market_type=["spot"],
        buy_exchange=["binance"], sell_exchange=["okx"], gross_bps=[gross], net_bps=[net], regime=[None]))
c = SimpleNamespace(d7=events(100., -80.), d7_live=events(9., 3.) if {live_available!r} else None,
    d7_msg=None, kind="historical", res=dict(t6b=None, t5=None, msg="No T5"),
    mode="Sáng", t=app.palette("Sáng", "#3A6FD8"), manifest=None)
app.page_gap(c)
'''


def test_live_selector_also_switches_net_and_arbitrage_flags():
    at = AppTest.from_string(gap_page_source(), default_timeout=30).run()
    assert not at.exception
    at.segmented_control(key="gapsrc").set_value("Live (lưới 5 giây)").run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Trước phí (trung vị)"] == "9,00 bps"
    assert metrics["Sau phí (trung vị)"] == "3,00 bps"
    assert metrics["Spot: sự kiện còn lãi sau chi phí"] == "1"
    assert any("không trừ spread lần nữa" in c.value for c in at.caption)
    at.segmented_control(key="gapsrc").set_value("Lịch sử (lưới 60 giây)").run()
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Sau phí (trung vị)"] == "−80,00 bps"
    assert metrics["Spot: sự kiện còn lãi sau chi phí"] == "0"


def test_gap_without_live_retains_historical_source():
    at = AppTest.from_string(gap_page_source(False), default_timeout=30).run()
    assert not at.exception
    assert {m.label: m.value for m in at.metric}["Sau phí (trung vị)"] == "−80,00 bps"
