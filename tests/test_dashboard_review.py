"""Regression tests for PR10 handoff validation and displayed information time."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_review_app", ROOT / "dashboard/app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
T = pd.Timestamp("2026-01-01T12:00:00Z").as_unit("ns")


def rolling(kind="synthetic"):
    df = pd.DataFrame({
        "window_start": T - pd.Timedelta(hours=1), "window_end": T, "available_at": T,
        "venue": ["binance_spot", "okx_spot"], "is_lower": [0.4, 0.1],
        "is_upper": [0.9, 0.5], "is_mid": [0.65, 0.3], "component_share": [0.6, 0.4],
        "ils": [0.55, 0.45], "leader": [None, None], "status": "ok", "reason": "overlapping_bounds"})
    for c in app.D6_TIME:
        df[c] = df[c].astype("datetime64[ns, UTC]")
    df.attrs["data_kind"] = kind
    return df


def events(kind="synthetic"):
    df = pd.DataFrame({"event_id": ["event1"], "ts_start": T, "ts_end": T,
                       "duration_s": [60.0], "market_type": ["spot"], "buy_exchange": ["binance"],
                       "sell_exchange": ["okx"], "gross_bps": [8.0], "net_bps": [1.0], "regime": [None]})
    for c in app.D7_TIME:
        df[c] = df[c].astype("datetime64[ns, UTC]")
    df.attrs["data_kind"] = kind
    return df


def load(tmp_path, df, schema="D6", **kwargs):
    p = tmp_path / f"{schema}.parquet"
    df.to_parquet(p, index=False)
    return app._load_table(str(p), p.stat().st_mtime_ns, tuple(getattr(app, schema + "_COLS")),
                           tuple(getattr(app, schema + "_TIME")), **kwargs)


def test_rejects_leader_with_overlapping_bounds(tmp_path):
    df = rolling()
    df["leader"] = "binance_spot"
    out, msg = load(tmp_path, df)
    assert out is None and "overlapping bounds" in msg


@pytest.mark.parametrize("timestamp", ["naive", "invalid"])
def test_bad_time_is_reported_not_assumed_utc_or_crashed(tmp_path, timestamp):
    df = rolling()
    df["available_at"] = (df.available_at.dt.tz_localize(None) if timestamp == "naive" else "invalid")
    out, msg = load(tmp_path, df)
    assert out is None and msg


def test_does_not_mix_real_optional_tables_with_fixtures(tmp_path):
    out, msg = load(tmp_path, rolling("historical"), expected_kind="synthetic")
    assert out is None and "data_kind" in msg


def test_requires_provenance_and_rejects_conflicting_labels(tmp_path):
    df = rolling()
    df.attrs.clear()
    out, msg = load(tmp_path, df)
    assert out is None and "data_kind" in msg
    df.attrs["data_kind"] = "synthetic"
    df["data_kind"] = "historical"
    out, msg = load(tmp_path, df)
    assert out is None and "data_kind" in msg


def test_one_window_even_when_many_windows_share_publication_time():
    a, b = rolling(), rolling()
    a["window_start"] -= pd.Timedelta(minutes=10)
    a["window_end"] -= pd.Timedelta(minutes=10)
    out = app.current_window(pd.concat([a, b], ignore_index=True), T)
    assert len(out) == 2 and out.window_end.eq(T).all()


def test_peak_keeps_its_actual_time():
    s = pd.Series([0., 0., 9., 0., 0., 0.], index=pd.date_range(T, periods=6, freq="1min"))
    out = app.thin(s, max_points=2)
    assert out.idxmax() == s.idxmax()


def test_does_not_fall_back_to_synthetic_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "ROOT", tmp_path)
    contract = tmp_path / "outputs/tables/gap_events.parquet"
    fallback = tmp_path / "outputs/synthetic/gap_events.parquet"
    fallback.parent.mkdir(parents=True)
    fallback.touch()
    assert app._default_path(contract) == str(contract)


def test_duplicate_rolling_rows_rejected(tmp_path):
    out, msg = load(tmp_path, pd.concat([rolling(), rolling()], ignore_index=True))
    assert out is None and "duplicate" in msg


def test_future_gap_events_not_shown_or_counted(tmp_path):
    from src.utils.io_utils import read_parquet
    panel = ROOT / "data/fixtures/multi/panel_1m_wide.parquet"
    t = read_parquet(panel, "D3").index[-1]
    df = events()
    df["ts_start"] = t + pd.Timedelta(minutes=1)
    df["ts_end"] = t + pd.Timedelta(minutes=2)
    path = tmp_path / "future.parquet"
    df.to_parquet(path, index=False)
    at = AppTest.from_file(str(ROOT / "dashboard/app.py"), default_timeout=60).run()
    at.sidebar.text_input[1].set_value(str(tmp_path / "absent.parquet"))
    at.sidebar.text_input[2].set_value(str(path))
    at.run()
    at.radio(key="nav").set_value("Chênh lệch giá & lợi nhuận")   # các ô "còn lãi" nằm ở trang này
    at.run()
    assert not at.exception
    assert not any(m.label.endswith("còn lãi sau chi phí") and m.value == "1" for m in at.metric)


@pytest.mark.parametrize("bad", ["self", "unknown", "negative_duration", "non_numeric"])
def test_bad_events_rejected(tmp_path, bad):
    df = events()
    if bad == "self":
        df["sell_exchange"] = "binance"
    elif bad == "unknown":
        df["market_type"] = "mixed"
    elif bad == "negative_duration":
        df["duration_s"] = -1.
    else:
        df["net_bps"] = "not a number"
    out, msg = load(tmp_path, df, "D7")
    assert out is None and msg


def test_empty_upstream_is_valid_but_does_not_invent_leader(tmp_path):
    for schema, df in (("D6", rolling().iloc[:0]), ("D7", events().iloc[:0])):
        out, msg = load(tmp_path, df, schema, expected_kind="synthetic")
        assert out is not None and out.empty and msg is None


def test_failed_window_cannot_announce_leader():
    df = rolling()
    df["status"] = "failed"
    df["leader"] = "binance_spot"
    leader, reason = app.leader_of(df)
    assert leader is None and reason
