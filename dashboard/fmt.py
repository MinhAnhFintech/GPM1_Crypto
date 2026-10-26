"""Định dạng số, đơn vị và thời gian dùng chung cho dashboard (chỉ đổi cách hiển thị, không đổi số liệu).

Quy ước: dấu chấm ngăn hàng nghìn, dấu phẩy thập phân, dấu trừ thật U+2212.
"""
import math

import pandas as pd

MINUS = "−"
# Cấu hình số cho Vega/Altair (trục, hover): 83.565,4 và −8,10. Gắn vào biểu đồ bằng usermeta embedOptions.
VEGA_LOCALE = {"decimal": ",", "thousands": ".", "grouping": [3], "currency": ["", ""], "minus": MINUS}


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x)) and not pd.isna(x)


def num(x, digits: int = 0) -> str:
    """83565.4 -> '83.565,4'; số âm dùng dấu trừ thật. Giá trị thiếu -> '–'."""
    if not _ok(x):
        return "–"
    s = f"{x:,.{digits}f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return s.replace("-", MINUS)


def bps(x) -> str:
    """bps: 2 chữ số thập phân; |x| < 0,01 thì 4 chữ số để không hiện 0,00 cho giá trị khác 0."""
    if not _ok(x):
        return "–"
    return num(x, 4 if 0 < abs(x) < 0.01 else 2)


def pct(x, digits: int = 2) -> str:
    """Tỷ lệ 0..1 -> '4,33%'."""
    return "–" if not _ok(x) else num(x * 100, digits) + "%"


def usdt_big(x) -> str:
    """Số tiền lớn bằng chữ Việt: '25 triệu', '1,4 triệu', '1,5 tỷ'. Dưới 1.000 giữ nguyên số."""
    if not _ok(x):
        return "–"
    a = abs(x)
    for limit, word in ((1e9, "tỷ"), (1e6, "triệu"), (1e3, "nghìn")):
        if a >= limit:
            value = x / limit
            digits = 0 if abs(value) >= 100 or float(round(value)) == round(value, 1) else 1
            return f"{num(value, digits)} {word}"
    return num(x, 0)


def latency(value) -> str:
    """'1min' hoặc '5s' hoặc Timedelta -> '1 phút' / '5 giây'."""
    seconds = pd.Timedelta(value).total_seconds()
    if seconds >= 60 and seconds % 60 == 0:
        return f"{num(seconds / 60)} phút"
    return f"{num(seconds)} giây"


def latency_seconds(value) -> float:
    """Khóa sắp xếp độ trễ theo giá trị số (1 phút < 2 phút < 5 phút, không theo chữ)."""
    return pd.Timedelta(value).total_seconds()


def ratio(a, b) -> str:
    """'118.909 / 178.502' cho 'lệnh có lãi / tín hiệu'."""
    return f"{num(a)} / {num(b)}"


def duration_s(x) -> str:
    return "–" if not _ok(x) else f"{num(x)} giây"


def rank(x) -> str:
    """Hạng '#3'; hạng thiếu (ví dụ thiếu spread) hiện '–' thay vì làm trang lỗi."""
    return "–" if not _ok(x) else f"#{int(x)}"
