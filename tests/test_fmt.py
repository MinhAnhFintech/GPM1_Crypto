"""Test định dạng số kiểu Việt Nam dùng chung cho dashboard."""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("dashboard_fmt", ROOT / "dashboard" / "fmt.py")
fmt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmt)


def test_number_separators_and_minus():
    assert fmt.num(83565.4, 1) == "83.565,4"
    assert fmt.num(501119) == "501.119"
    assert fmt.num(-8.1, 2) == "−8,10"
    assert fmt.pct(0.0433) == "4,33%"


def test_missing_values_are_a_dash_not_nan():
    assert fmt.num(np.nan) == "–" and fmt.bps(None) == "–" and fmt.pct(pd.NA) == "–"


def test_bps_shows_four_digits_for_tiny_values():
    assert fmt.bps(2.2149) == "2,21"
    assert fmt.bps(0.0012) == "0,0012"          # không hiện 0,00 cho giá trị khác 0
    assert fmt.bps(0) == "0,00" and fmt.bps(-8.35) == "−8,35"


def test_big_usdt_in_vietnamese_words():
    assert fmt.usdt_big(25_000_000) == "25 triệu"
    assert fmt.usdt_big(1_400_000) == "1,4 triệu"
    assert fmt.usdt_big(1_500_000_000) == "1,5 tỷ"
    assert fmt.usdt_big(850) == "850"


def test_latency_is_words_and_sorted_by_value():
    assert fmt.latency("1min") == "1 phút" and fmt.latency("5s") == "5 giây" and fmt.latency("30s") == "30 giây"
    order = sorted(["5min", "1min", "2min"], key=fmt.latency_seconds)
    assert order == ["1min", "2min", "5min"]
    assert sorted(["30s", "5s", "10s"], key=fmt.latency_seconds) == ["5s", "10s", "30s"]


def test_ratio_of_profitable_trades():
    assert fmt.ratio(118909, 178502) == "118.909 / 178.502"
