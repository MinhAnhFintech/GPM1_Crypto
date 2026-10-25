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
    assert app.vn(22805) == "22 805" and app.vn(-89.29, 2) == "−89,29"
    assert app.venue_name("binance_perp") == "Binance · Vĩnh cửu" and app.venue_name("odd") == "odd"


def test_every_glossary_column_has_a_vietnamese_label():
    assert set(app.GLOSSARY) <= set(app.COLUMN_SPECS)
    assert all(label != col for col, (label, _) in app.COLUMN_SPECS.items())


def test_pretty_changes_display_only():
    df = pd.DataFrame({"venue": ["okx_spot"], "market_type": ["spot"], "reason": ["overlapping_bounds"], "x": [1.5]})
    out = app.pretty(df)
    assert out.loc[0, "venue"] == "OKX · Giao ngay" and out.loc[0, "x"] == 1.5
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
