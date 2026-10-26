"""Test C2.08: phần kết quả backtest, thời gian tồn tại gap và tên hiển thị tiếng Việt của dashboard."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_app_results", ROOT / "dashboard" / "app.py")
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_duration_ccdf_starts_at_one_and_decreases():
    d7 = pd.DataFrame({"market_type": ["spot"] * 4 + ["perp"], "duration_s": [60, 60, 120, 480, 60]})
    out = app.duration_ccdf(d7)
    spot = out[out.market_type == "spot"].set_index("duration_s")["share"]
    assert spot.to_dict() == {60.0: 1.0, 120.0: 0.5, 480.0: 0.25}   # tỷ lệ sự kiện kéo dài ÍT NHẤT d giây
    assert out[out.market_type == "perp"]["share"].tolist() == [1.0]


def test_cost_waterfall_ends_at_t5_net():
    row = pd.Series(dict(mean_mid_gap_bps=13.2, mean_fee_bps=10.25, mean_spread_bps=0.01, mean_slippage_bps=0.0,
                         mean_latency_bps=11.54, mean_net_bps=-8.6))
    out = app.cost_waterfall(row)
    assert out["kind"].tolist() == ["gap", "cost", "cost", "cost", "cost", "net"]
    assert out["end"].iloc[-2] == pytest.approx(row.mean_net_bps)      # trừ dần từng chi phí ra đúng net
    assert out["end"].iloc[-1] == row.mean_net_bps and out["start"].iloc[-1] == 0


def test_cumulative_pnl_only_filled_and_per_market():
    ledger = pd.DataFrame({"signal_id": list("abcd"), "status": ["filled", "rejected", "filled", "filled"],
                           "ts_fill": pd.to_datetime(["2026-01-03", "2026-01-01", "2026-01-01", "2026-01-02"], utc=True),
                           "market_type": ["perp", "perp", "perp", "spot"], "pnl_usdt": [-2.0, 99.0, -1.0, -5.0]})
    out = app.cumulative_pnl(ledger)
    assert out[out.market_type == "perp"]["cum_pnl_usdt"].tolist() == [-1.0, -3.0]
    assert out[out.market_type == "spot"]["cum_pnl_usdt"].tolist() == [-5.0]


def test_fee_scenarios_ignore_zero_fee_upper_bound():
    t5b = pd.DataFrame({"fee_multiplier": [1.0, 0.5, 0.0], "n_net_positive": [0, 0, 500], "n_signals": [3, 10, 900]})
    assert app.fee_scenarios(t5b) == dict(n=2, with_profit=0, signals=13, positive=0)


def test_split_label_reads_manifest_dates():
    manifest = {"dev_end": "2026-05-14T19:11:00+00:00", "val_end": "2026-07-23T09:34:00+00:00"}
    d7 = pd.DataFrame({"ts_end": pd.to_datetime(["2026-05-14 19:11"], utc=True)})
    assert app.split_label(d7, manifest) == "tập phát triển"
    assert app.split_label(d7.assign(ts_end=pd.Timestamp("2026-09-01", tz="UTC")), manifest) is None
    assert app.split_label(d7, None) is None


def test_vietnamese_number_format_and_names():
    assert app.vn(22805) == "22.805" and app.vn(-89.29, 2) == "−89,29"
    assert app.venue_name("binance_perp") == "Binance · Perp" and app.venue_name("odd") == "odd"


def test_every_glossary_column_has_a_vietnamese_label():
    assert set(app.GLOSSARY) <= set(app.COLUMN_SPECS)
    assert all(label != col for col, (label, _) in app.COLUMN_SPECS.items())


def test_pretty_changes_display_only():
    df = pd.DataFrame({"venue": ["okx_spot"], "market_type": ["spot"], "reason": ["overlapping_bounds"], "x": [1.5]})
    out = app.pretty(df)
    assert out.loc[0, "venue"] == "OKX · Spot" and out.loc[0, "x"] == 1.5
    assert df.loc[0, "venue"] == "okx_spot"                              # bản gốc (để tải về) không đổi


def test_home_cards_use_only_given_sources():
    idx = pd.date_range("2026-01-01", periods=3, freq="1min", tz="UTC")
    cards = app.home_cards(pd.DataFrame(index=idx), ["a_spot"], None, None, {"t5": None})
    assert [c["rq"] for c in cards] == ["Dữ liệu"]                       # không có nguồn thì không có thẻ


@pytest.mark.skipif(not (ROOT / "outputs/tables/T5_arbitrage_net.csv").exists(), reason="chưa có T5")
def test_load_results_reads_t5_t5b_and_ledger():
    res = app._load_results(str(ROOT / "outputs/tables"), 0.0)
    assert res["msg"] is None and res["t5"] is not None
    assert int(res["t5"]["n_positive_net"].sum()) >= 0


def test_load_results_waits_when_folder_empty(tmp_path):
    res = app._load_results(str(tmp_path), 0.0)
    assert res["t5"] is None and res["msg"].startswith("Chờ kết quả backtest")


def test_price_ranges_only_compare_same_contract_type():
    q = pd.DataFrame({"venue": ["a_spot", "b_spot", "a_perp", "b_perp"], "market_type": ["spot", "spot", "perp", "perp"],
                      "close_usdt": [100.0, 100.1, 99.9, 99.8]})
    out = app.price_ranges(q)
    assert out["spot"] == pytest.approx(0.1 / 100.05 * 1e4) and out["perp"] == pytest.approx(0.1 / 99.85 * 1e4)
    assert out["basis_n"] == 2 and out["basis"] < 0      # perp thấp hơn giao ngay; không trộn vào hai ô đầu


def test_leader_counts_needs_exactly_one_leader_per_window():
    t0, t1 = pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC")
    d6 = pd.DataFrame({"window_start": [t0, t0, t1, t1], "window_end": [t0, t0, t1, t1],
                       "leader": ["a_spot", "a_spot", None, ""]})
    counts, total = app.leader_counts(d6)
    assert counts == {"a_spot": 1} and total == 2


def test_regime_rates_skips_reference_rows_and_live():
    t6b = pd.DataFrame({
        "source": ["historical_dev", "historical_dev", "live_calendar"], "dimension": ["weekend", "weekend", "weekend"],
        "level": ["weekend", "weekday", "weekend"], "market_type": ["spot"] * 3,
        "rate_ratio_vs_reference": [0.73, 1.0, 0.05], "reference_level": ["weekday"] * 3})
    out = app.regime_rates(t6b)
    assert out["ratio"].tolist() == [0.73] and out["label"].iloc[0].startswith("Cuối tuần")


def test_regression_table_flags_significance():
    t4 = pd.DataFrame({"model": ["A", "A"], "term": ["is_perp", "vol_share_w (z)"], "coef": [0.0063, -0.0002],
                       "ci95_low": [0.0044, -0.001], "ci95_high": [0.0081, 0.0006], "p_value": [5e-11, 0.68]})
    out = app.regression_table(t4, "A")
    assert out["Ý nghĩa thống kê"].tolist() == ["p < 0,001 (có)", "p = 0,680 (chưa)"]
    assert out["Hệ số"].tolist() == ["0,0063", "−0,0002"]


def test_charts_build_with_vietnamese_separators():
    from dashboard import charts
    t = app.palette("Sáng", "#3A6FD8")
    ccdf = app.duration_ccdf(pd.DataFrame({"market_type": ["spot", "perp"], "duration_s": [60, 120]}))
    fig = charts.ccdf_fig(ccdf, "Sáng", t, 60)
    assert fig.layout.separators == ",." and len(fig.data) == 2
    assert charts.png_config("F4")["toImageButtonOptions"]["filename"] == "F4_gap_duration"


def test_t3c_chart_has_is_ranges_and_ils_bars():
    from dashboard import charts
    rows = []
    for ex in ("binance", "okx"):
        for split in ("dev", "val"):
            rows.append(dict(split=split, exchange=ex, is_lower_spot=0.0, is_upper_spot=1.0, is_mid_spot=0.5, ils_spot=0.4,
                             is_lower_perp=0.0, is_upper_perp=1.0, is_mid_perp=0.5, ils_perp=0.6,
                             conclusion="chua_phan_biet_duoc"))
    fig = charts.t3c_fig(pd.DataFrame(rows), "Sáng", app.palette("Sáng", "#3A6FD8"))
    assert len(fig.data) == 2 * 2 * 2                         # 2 sàn × (IS, ILS) × (Spot, Perp)
    assert list(fig.data[0].x) == ["Tập phát triển", "Tập xác thực"]


def test_report_figure_html_inlines_png(tmp_path):
    png = tmp_path / "F.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    html = app.figure_html(png, "a <b>")
    assert "data:image/png;base64," in html and "a &lt;b&gt;" in html


def test_t3c_findings_and_guide_come_from_the_table():
    from dashboard import charts
    base = dict(is_lower_spot=0.0, is_upper_spot=1.0, is_lower_perp=0.0, is_upper_perp=1.0)
    t3c = pd.DataFrame([dict(split="dev", exchange="binance", ils_spot=0.4, ils_perp=0.6, **base),
                        dict(split="val", exchange="binance", ils_spot=0.8, ils_perp=0.2, **base),
                        dict(split="dev", exchange="okx", ils_spot=0.4, ils_perp=0.6, **base),
                        dict(split="val", exchange="okx", ils_spot=0.1, ils_perp=0.9, **base)])
    f = charts.t3c_findings(t3c)
    assert f["overlap_all"] and f["flips"] == [("binance", "perp")] and f["steady"] == [("okx", "perp")]
    html = app.f7_guide(t3c)
    assert "Binance" in html and "chưa phân biệt được" in html
    assert "Kết quả" not in app.f7_guide(None)               # không có bảng thì không đưa kết luận
