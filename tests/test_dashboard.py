"""Test C2.07: hàm thuần của dashboard + chạy thử giao diện trên fixtures (không cần mạng)."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_app", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def make_panel(n=6):
    idx = pd.date_range("2026-01-01 00:01", periods=n, freq="1min", tz="UTC", name="ts_available")
    cols = {}
    for i, v in enumerate(["binance_spot", "okx_spot", "binance_perp", "okx_perp"]):
        cols[f"close__{v}"] = 100.0 + i * 0.01 + np.arange(n) * 0.001
    return pd.DataFrame(cols, index=idx)


def test_dispersion_bps_only_when_all_venues_present():
    panel = make_panel()
    panel.loc[panel.index[2], "close__okx_spot"] = np.nan
    s = app.dispersion_bps(panel, ["binance_spot", "okx_spot", "binance_perp", "okx_perp"], "spot")
    assert s.isna().tolist() == [False, False, True, False, False, False]  # không lấp phút thiếu
    assert (s.dropna() > 0).all()


def test_dispersion_none_when_single_venue_in_group():
    assert app.dispersion_bps(make_panel(), ["binance_spot", "okx_perp"], "spot") is None


def test_thin_keeps_peak_and_limits_points():
    s = pd.Series(np.zeros(10_000), index=pd.date_range("2026-01-01", periods=10_000, freq="1min", tz="UTC"), name="x")
    s.iloc[7_777] = 55.0
    out = app.thin(s, max_points=1_000)
    assert len(out) <= 1_000 and out.max() == 55.0


def test_current_window_ignores_not_yet_available():
    t = pd.Timestamp("2026-01-02", tz="UTC")
    d6 = pd.DataFrame({"available_at": [t - pd.Timedelta(hours=2), t - pd.Timedelta(hours=1), t + pd.Timedelta(hours=1)],
                       "venue": ["a", "a", "a"]})
    w = app.current_window(d6, t)
    assert len(w) == 1 and w["available_at"].iloc[0] == t - pd.Timedelta(hours=1)
    assert app.current_window(d6, t - pd.Timedelta(days=1)) is None


def test_leader_blank_is_valid_result():
    win = pd.DataFrame({"leader": [None, None], "reason": ["overlapping_bounds", "overlapping_bounds"], "status": "ok"})
    assert app.leader_of(win) == (None, "overlapping_bounds")
    assert app.leader_of(pd.DataFrame({"venue": ["okx_perp", "binance_perp"], "is_lower": [0.8, 0.1],
                                      "is_upper": [0.9, 0.2], "leader": ["okx_perp", "okx_perp"],
                                      "reason": [None, None], "status": "ok"})) == ("okx_perp", None)
    leader, reason = app.leader_of(pd.DataFrame({"leader": ["a", "b"], "reason": [None, None], "status": "ok"}))
    assert leader is None and "nhiều leader" in reason


def test_gap_summary_by_market_type():
    d7 = pd.DataFrame({"event_id": [1, 2, 3], "market_type": ["spot", "spot", "perp"],
                       "gross_bps": [4.0, 6.0, np.nan]})
    out = app.gap_summary(d7, "gross_bps").set_index("market_type")
    assert out.loc["spot", "trung_vi_bps"] == 5.0 and out.loc["perp", "thieu"] == 1


FIXTURE_PANEL = ROOT / "data" / "fixtures" / "multi" / "panel_1m_wide.parquet"


@pytest.fixture
def panel_file():
    """Panel D3 giả lập hợp lệ do TV4 sinh (đã qua kiểm tra schema); bỏ qua nếu chưa có."""
    if not FIXTURE_PANEL.exists():
        pytest.skip("chưa có data/fixtures/multi/panel_1m_wide.parquet (chạy: python run_all.py --mode synthetic)")
    return FIXTURE_PANEL


def test_app_runs_without_optional_tables(panel_file, tmp_path):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    at.sidebar.text_input[0].set_value(str(panel_file))
    at.sidebar.text_input[1].set_value(str(tmp_path / "khong_co_d6.parquet"))
    at.sidebar.text_input[2].set_value(str(tmp_path / "khong_co_d7.parquet"))
    at.run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert [s.value for s in at.subheader][0].startswith("1.") and len(at.subheader) == 7
    assert any("Chờ file" in m.value for m in at.markdown)  # ô chưa có nguồn ghi 'chờ', không bịa số


def test_app_missing_panel_shows_error(tmp_path):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    at.sidebar.text_input[0].set_value(str(tmp_path / "khong_co.parquet"))
    at.run()
    assert not at.exception and len(at.error) == 1


def test_app_with_rolling_and_gap_tables(panel_file, tmp_path):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    from src.utils.io_utils import read_parquet
    t = pd.Timestamp(read_parquet(str(panel_file), "D3").index[-1])
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    d6 = pd.DataFrame({
        "window_start": t - pd.Timedelta(hours=2), "window_end": t, "available_at": t,
        "venue": ["binance_spot", "okx_spot"], "is_lower": [0.4, 0.1], "is_upper": [0.9, 0.5], "is_mid": [0.65, 0.3],
        "component_share": [0.6, 0.4], "ils": [0.55, 0.45], "leader": [None, None], "status": ["ok", "ok"],
        "reason": ["overlapping_bounds", "overlapping_bounds"]})
    d7 = pd.DataFrame({
        "event_id": ["one", "two", "three"], "ts_start": t - pd.Timedelta(minutes=2), "ts_end": t, "duration_s": [180.0, 180.0, 180.0],
        "market_type": ["spot", "spot", "spot"], "buy_exchange": "binance", "sell_exchange": "okx",
        "gross_bps": [8.0, 3.0, 12.0], "net_bps": [1.0, -4.0, np.nan], "regime": "normal"})
    for frame, columns in ((d6, app.D6_TIME), (d7, app.D7_TIME)):
        frame.attrs["data_kind"] = "synthetic"
        for col in columns:
            frame[col] = frame[col].astype("datetime64[ns, UTC]")
    p6, p7 = tmp_path / "d6.parquet", tmp_path / "d7.parquet"
    d6.to_parquet(p6, index=False)
    d7.to_parquet(p7, index=False)
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    at.sidebar.text_input[0].set_value(str(panel_file))
    at.sidebar.text_input[1].set_value(str(p6))
    at.sidebar.text_input[2].set_value(str(p7))
    at.run()
    assert not at.exception
    assert not at.error, [e.value for e in at.error]
    assert any("overlapping_bounds" in w.value for w in at.warning)      # leader trống hiển thị đúng lý do
    spot, perp = [m.value for m in at.metric if "còn lãi" in m.label]
    assert (spot, perp) == ("1", "0")                                      # NaN net không tính là cơ hội


def test_theme_css_uses_accent_and_mode():
    css = app.theme_css("Tối", "#1F6FEB", effects=False)
    assert "#1F6FEB" in css and app.palette("Tối", "#1F6FEB")["bg"] in css


def test_cursor_only_when_effects_on():
    assert "cursor:url(" in app.theme_css("Sáng", "#F7931A", effects=True)
    assert "cursor:url(" not in app.theme_css("Sáng", "#F7931A", effects=False)


def test_hero_has_title_and_five_questions():
    html = app.hero_html()
    assert "Crypto Price Discovery" in html and html.count("<li><b>RQ") == 5


def test_to_csv_bytes_has_bom_and_keeps_nan_blank():
    df = pd.DataFrame({"a": [1.0, np.nan]}, index=pd.Index([1, 2], name="k"))
    raw = app.to_csv_bytes(df)
    assert raw.startswith(b"\xef\xbb\xbf") and raw.decode("utf-8-sig").splitlines()[2] == "2,"


def test_range_mask_windows():
    idx = pd.date_range("2026-01-01", periods=3000, freq="1min", tz="UTC")
    assert app.range_mask(idx, "1 ngày").sum() == 1440
    assert app.range_mask(idx, "Toàn bộ").sum() == 3000
    d = pd.Timestamp("2026-01-02").date()
    assert app.range_mask(idx, "Tùy chọn", (d, d)).sum() == 1440


def test_anim_chart_embeds_data_and_colors():
    s = pd.Series([1.0, 2.0], index=pd.date_range("2026-01-01", periods=2, freq="1min", tz="UTC"))
    html = app.anim_chart_html({"spot": s}, ["#3A6FD8"], True, app.palette("Sáng", "#3A6FD8"))
    assert '"spot"' in html and "#3A6FD8" in html and "__DATA__" not in html


def test_font_and_size_in_css():
    css = app.theme_css("Sáng", "#3A6FD8", False, font="Georgia", size_pt=14)
    assert "Georgia" in css and "18.7px" in css


def test_app_runs_in_dark_mode_with_custom_color(panel_file, tmp_path):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "dashboard" / "app.py"), default_timeout=60).run()
    at.sidebar.text_input[0].set_value(str(panel_file))
    at.sidebar.text_input[1].set_value(str(tmp_path / "khong_co_d6.parquet"))
    at.sidebar.text_input[2].set_value(str(tmp_path / "khong_co_d7.parquet"))
    at.sidebar.radio[0].set_value("Tối")
    at.sidebar.selectbox[0].set_value("Tùy chỉnh")
    at.run()
    assert not at.exception and not at.error
    assert len(at.tabs) == 6 and len(at.subheader) == 7
