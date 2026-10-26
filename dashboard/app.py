"""C2.07 / C2.08 (TV2) - Dashboard: bảy khối nghiên cứu chia thành các thẻ.

Chạy từ gốc repo:
    python -m streamlit run dashboard/app.py

Đầu vào (đường dẫn sửa được ở thanh bên, mục "Nâng cao: nguồn dữ liệu"):
    D3  data/processed/panel_1m_wide.parquet         bảng rộng 1 phút (index ts_available, UTC)
    D6  outputs/tables/rolling_is.parquet            rolling IS/CS/ILS (tùy chọn, TV5)
    D7  outputs/tables/gap_events_historical.parquet sự kiện gap (tùy chọn, TV6)
    D2  data/processed/live_quotes_long.parquet      báo giá live (tùy chọn, TV3)
    T5, T5b, sổ lệnh backtest trong outputs/tables  kết quả backtest (tùy chọn, TV6; chỉ cho dữ liệu thật)
Chọn rõ file fixtures/synthetic hoặc historical/live; không tự chuyển nguồn khi thiếu file.

Quy ước: giá USDT; gap theo bps; thời gian UTC; mỗi venue = {exchange}_{market_type}.
Chỉ hiển thị cái đã có: ô nào chưa có nguồn thì ghi "chờ", KHÔNG tự tính hay bịa số.
Mọi con số trên trang chủ đều đọc từ các file trên, không gõ tay.
Chỉ ghép cặp cùng market_type. funding_rate không nằm trong net_gap_bps.
Phần giao diện (màu, sáng/tối, hiệu ứng, tên cột tiếng Việt) chỉ đổi cách hiển thị, không đổi số liệu.
Mô phỏng nghiên cứu, không khuyến nghị giao dịch.
"""
from html import escape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
import base64
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import streamlit as st

from dashboard import charts, fmt
from dashboard.charts import SERIES, GOOD, BAD, MARKET_NAME, EXCHANGE_NAME, venue_label
from src.utils.io_utils import read_parquet
from src.utils.schema import data_kind, venues_from_panel, validate_frame, VENUE_PATTERN

PANEL_DEFAULT = ROOT / "data/processed/panel_1m_wide.parquet"
D6_DEFAULT = ROOT / "outputs/tables/rolling_is.parquet"
D7_DEFAULT = ROOT / "outputs/tables/gap_events_historical.parquet"
RESULTS_DEFAULT = ROOT / "outputs/tables"
MANIFEST = ROOT / "data/processed/split_manifest.json"

D6_COLS = ["window_start", "window_end", "available_at", "venue", "is_lower", "is_upper", "is_mid",
           "component_share", "ils", "leader", "status", "reason"]
D6_TIME = ["window_start", "window_end", "available_at"]
D7_COLS = ["event_id", "ts_start", "ts_end", "duration_s", "market_type", "buy_exchange", "sell_exchange",
           "gross_bps", "net_bps", "regime"]
D7_TIME = ["ts_start", "ts_end"]
MAX_POINTS = 5000      # số điểm tối đa vẽ mỗi đường (giữ cực đại mỗi nhóm để không mất đỉnh)
TOP_EVENTS = 10
DOWNLOAD_DAYS = {"1 ngày gần nhất": 1, "7 ngày gần nhất": 7, "30 ngày gần nhất": 30}

PRESETS = {"Xanh dương": "#3A6FD8", "Bạc hà": "#1E9E8A", "Xanh lá": "#4F9A45", "Vàng bơ": "#C9961A",
           "Cam Bitcoin": "#F7931A", "Đào": "#E08246", "Hồng phấn": "#D85C86", "Tím oải hương": "#7C60CF"}
CUSTOM = "Tùy chỉnh"
# tints = tỷ lệ pha màu nền / thẻ / viền với màu chủ đạo (tông pastel như bảng màu Chrome)
MODES = {
    "Sáng": dict(text="#1B2A41", muted="#5B6B82", base="#FFFFFF", tints=(0.93, 0.985, 0.80), invert="none"),
    "Tối": dict(text="#EEF1F8", muted="#9FAAC0", base="#0B0F19", tints=(0.90, 0.84, 0.70),
                invert="invert(0.92) hue-rotate(180deg)"),
}
# Màu cố định cho hai nhóm thị trường: xem dashboard/charts.py (spot xanh nét liền, perp cam nét đứt).
DASH = {"spot": [], "perp": [6, 4]}        # nét cho đồ thị HTML 1 phút có thu phóng
KIND_NAME = {"historical": "lịch sử", "live": "live", "synthetic": "giả lập"}
STATUS_TEXT = {"ok": "Ước lượng được"}
REASON_TEXT = {"overlapping_bounds": "Các khoảng IS chồng nhau, chưa phân biệt được sàn dẫn giá"}
REASON_SHORT = {"overlapping_bounds": "IS chồng nhau"}
LEADLAG_FILE, REGRESSION_FILE, T3C_GLOB = "T2_leadlag_xcorr.csv", "T4_leadership_determinants.csv", "T3c*.csv"


# --------------------------------------------------------------------------- hàm thuần (có test)
def dispersion_bps(panel: pd.DataFrame, venues: list, market: str):
    """(max - min) / mean của close__{venue} trong cùng market_type, đơn vị bps.
    Chỉ tính ở phút mà MỌI venue của nhóm đều có giá; còn lại để NaN (không lấp). None nếu nhóm < 2 venue."""
    cols = [f"close__{v}" for v in venues if v.endswith("_" + market)]
    if len(cols) < 2:
        return None
    prices = panel[cols]
    spread = (prices.max(axis=1) - prices.min(axis=1)) / prices.mean(axis=1) * 1e4
    return spread.where(prices.notna().all(axis=1)).rename(f"dispersion_bps_{market}")


def thin(series: pd.Series, max_points: int = MAX_POINTS) -> pd.Series:
    """Giữ đỉnh tại đúng timestamp gốc và các khoảng missing (chỉ để hiển thị)."""
    if len(series) <= max_points:
        return series
    step = int(np.ceil(len(series) / max_points))
    positions = []
    for start in range(0, len(series), step):
        chunk = series.iloc[start:start + step]
        if chunk.notna().any():
            positions.append(start + int(np.nanargmax(chunk.to_numpy(dtype=float))))
    # Missing runs must remain visible even when this adds gap markers to the point budget.
    missing = series.isna().to_numpy()
    positions.extend(np.flatnonzero(missing & ~np.r_[False, missing[:-1]]).tolist())
    return series.iloc[sorted(set(positions))]


def current_window(d6: pd.DataFrame, asof: pd.Timestamp):
    """Cửa sổ rolling mới nhất đã SẴN SÀNG tại thời điểm asof (available_at <= asof). None nếu chưa có."""
    ready = d6[d6["available_at"] <= asof]
    if ready.empty:
        return None
    # Several historical windows may have been published together after spec selection.
    ready = ready[ready["window_end"] == ready["window_end"].max()] if "window_end" in ready else ready
    ready = ready[ready["window_start"] == ready["window_start"].max()] if "window_start" in ready else ready
    return ready[ready["available_at"] == ready["available_at"].max()]


def leader_of(window: pd.DataFrame):
    """(leader, lý do). Leader để trống là kết quả hợp lệ: 'chưa phân biệt được'."""
    if not window["status"].str.lower().eq("ok").all():
        return None, "cửa sổ có kết quả mô hình chưa đạt; xem status/reason"
    leaders = window["leader"].dropna().astype(str).str.strip()
    leaders = leaders[leaders != ""].unique()
    if len(leaders) == 1:
        candidate = window[window.venue.eq(leaders[0])]
        others = window[~window.venue.eq(leaders[0])]
        if len(candidate) == 1 and not others.empty and candidate.is_lower.iloc[0] > others.is_upper.max():
            return leaders[0], None
        return None, "overlapping_bounds: chưa phân biệt được"
    if len(leaders) > 1:
        return None, "nhiều leader trong cùng một cửa sổ (kiểm tra rolling_is)"
    reasons = window["reason"].dropna().astype(str).str.strip()
    reasons = reasons[reasons != ""].unique()
    return None, (", ".join(reasons) if len(reasons) else "chưa phân biệt được")


def gap_summary(d7: pd.DataFrame, column: str) -> pd.DataFrame:
    """Tóm tắt theo market_type cho cột gap (gross_bps hoặc net_bps): số sự kiện, trung vị, số dòng NaN."""
    return d7.groupby("market_type").agg(
        su_kien=("event_id", "size"),
        trung_vi_bps=(column, "median"),
        thieu=(column, lambda s: int(s.isna().sum())),
    ).reset_index()


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV UTF-8 có BOM để Excel đọc đúng; giữ nguyên index (ts_available) và NaN là ô trống."""
    return df.to_csv().encode("utf-8-sig")


def vn(x, digits: int = 0) -> str:
    """Số kiểu Việt Nam (xem dashboard/fmt.py): 22.805; −8,10."""
    return fmt.num(x, digits)


MARKET_SHORT = {"spot": "Spot", "perp": "Perp"}


def venue_name(venue: str) -> str:
    """binance_spot -> 'Binance · Spot', binance_perp -> 'Binance · Perp'. Tên lạ giữ nguyên."""
    exchange, _, market = str(venue).rpartition("_")
    if not exchange:
        return str(venue)
    return f"{EXCHANGE_NAME.get(exchange, exchange)} · {MARKET_SHORT.get(market, market)}"


def duration_ccdf(d7: pd.DataFrame) -> pd.DataFrame:
    """Tỷ lệ sự kiện kéo dài ÍT NHẤT d giây, theo market_type (dạng F4). Cột: market_type, duration_s, share."""
    frames = []
    for market, g in d7.dropna(subset=["duration_s"]).groupby("market_type"):
        dur = np.sort(g["duration_s"].to_numpy(dtype=float))
        values = np.unique(dur)
        share = 1 - np.searchsorted(dur, values, side="left") / len(dur)
        frames.append(pd.DataFrame({"market_type": market, "duration_s": values, "share": share}))
    if not frames:
        return pd.DataFrame(columns=["market_type", "duration_s", "share"])
    return pd.concat(frames, ignore_index=True)


WATERFALL_STEPS = [("mean_mid_gap_bps", "Chênh lệch lúc ra quyết định"), ("mean_fee_bps", "− Phí taker hai chân"),
                   ("mean_spread_bps", "− Nửa chênh lệch mua–bán"), ("mean_slippage_bps", "− Trượt giá"),
                   ("mean_latency_bps", "− Giá biến động trong lúc chờ khớp")]


def cost_waterfall(row: pd.Series) -> pd.DataFrame:
    """Thác chi phí trung bình mỗi lệnh (bps) từ một dòng T5: gap ban đầu trừ dần từng chi phí ra net.
    Cột: step, start, end, value, kind (gap/cost/net). Thanh net lấy đúng mean_net_bps của T5."""
    rows, level = [], 0.0
    for i, (col, label) in enumerate(WATERFALL_STEPS):
        value = float(row[col]) if pd.notna(row.get(col)) else 0.0
        start, level = level, (level + value if i == 0 else level - value)
        rows.append(dict(step=label, start=start, end=level, value=value if i == 0 else -value,
                         kind="gap" if i == 0 else "cost"))
    net = float(row["mean_net_bps"])
    rows.append(dict(step="= Lãi/lỗ ròng mỗi lệnh", start=0.0, end=net, value=net, kind="net"))
    return pd.DataFrame(rows)


def cumulative_pnl(ledger: pd.DataFrame) -> pd.DataFrame:
    """Lãi/lỗ cộng dồn (USDT) theo thời điểm khớp, từng market_type, chỉ lệnh đã khớp (dạng F5)."""
    filled = ledger[ledger["status"].eq("filled")].sort_values(["ts_fill", "signal_id"])
    out = filled[["ts_fill", "market_type", "pnl_usdt"]].copy()
    out["cum_pnl_usdt"] = out.groupby("market_type")["pnl_usdt"].cumsum()
    return out.reset_index(drop=True)


def fee_scenarios(t5b: pd.DataFrame) -> dict:
    """Đếm kịch bản T5b CÓ phí (hệ số phí > 0): tổng số, số kịch bản có ít nhất một lệnh lãi, tổng tín hiệu."""
    fee = t5b[t5b["fee_multiplier"] > 0]
    return dict(n=len(fee), with_profit=int((fee["n_net_positive"] > 0).sum()),
                signals=int(fee["n_signals"].sum()), positive=int(fee["n_net_positive"].sum()))


def split_label(d7: pd.DataFrame, manifest: dict | None):
    """Sự kiện gap thuộc tập nào của split_manifest (theo ts_end lớn nhất). None nếu không xác định được."""
    if manifest is None or d7.empty:
        return None
    end = d7["ts_end"].max()
    if end <= pd.Timestamp(manifest["dev_end"]):
        return "tập phát triển"
    if end <= pd.Timestamp(manifest["val_end"]):
        return "tập phát triển và xác thực"
    return None


REGIME_LABELS = {("weekend", "weekend"): "Cuối tuần so với ngày thường",
                 ("volatility", "high_vol"): "Biến động cao so với bình thường",
                 ("event", "event"): "Ngày có sự kiện so với ngày không có",
                 ("trend", "bear"): "Giá giảm (bear) so với giá tăng (bull)"}


def regime_rates(t6b: pd.DataFrame) -> pd.DataFrame:
    """T6b trên tập phát triển: chỉ các mức khác mức tham chiếu. Cột: label, market_type, ratio (so với mức tham chiếu)."""
    base = t6b[(t6b["source"] == "historical_dev") & (t6b["level"] != t6b["reference_level"])]
    rows = [dict(label=REGIME_LABELS[(r.dimension, r.level)], market_type=r.market_type,
                 ratio=r.rate_ratio_vs_reference)
            for r in base.itertuples() if (r.dimension, r.level) in REGIME_LABELS]
    return pd.DataFrame(rows, columns=["label", "market_type", "ratio"])


def leader_counts(d6: pd.DataFrame):
    """({thị trường: số cửa sổ có sàn dẫn rõ ràng}, tổng số cửa sổ). Cửa sổ không có leader hoặc nhiều leader
    không tính cho thị trường nào (nằm trong 'chưa phân biệt được')."""
    keys = ["window_start", "window_end"]
    total = d6.groupby(keys).ngroups
    lead = d6.assign(leader=d6["leader"].astype("string").str.strip()).dropna(subset=["leader"])
    lead = lead[lead["leader"] != ""]
    one = lead.groupby(keys)["leader"].agg(lambda s: s.iloc[0] if s.nunique() == 1 else None).dropna()
    return one.value_counts().to_dict(), total


TERM_LABELS = {"const": "Hằng số", "vol_share_w (z)": "Thị phần khối lượng (chuẩn hóa)",
               "rv_60m (z)": "Biến động thực hiện 60 phút (chuẩn hóa)", "is_perp": "Là Perpetual futures (perp)",
               "rv_live (z)": "Biến động thực hiện live (chuẩn hóa)", "depth_share (z)": "Thị phần độ sâu (chuẩn hóa)",
               "spread_bps (z)": "Chênh lệch mua–bán (chuẩn hóa)", "log_depth (z)": "Log độ sâu (chuẩn hóa)"}


def regression_table(t4: pd.DataFrame, model: str) -> pd.DataFrame:
    """Hệ số hồi quy (T4) của một mô hình, đã gọn: Biến, Hệ số, Khoảng tin cậy 95%, p."""
    g = t4[t4["model"] == model]
    return pd.DataFrame({
        "Biến": [TERM_LABELS.get(t, t) for t in g["term"]],
        "Hệ số": [fmt.num(v, 4) for v in g["coef"]],
        "Khoảng tin cậy 95%": [f"{fmt.num(a, 4)} → {fmt.num(b, 4)}" for a, b in zip(g["ci95_low"], g["ci95_high"])],
        "Ý nghĩa thống kê": [("p < 0,001" if p < 1e-3 else "p = " + fmt.num(p, 3)) +
                             (" (có)" if p < 0.05 else " (chưa)") for p in g["p_value"]]})


# --------------------------------------------------------------------------- giao diện (chỉ hiển thị)
ASSETS = Path(__file__).resolve().parent / "assets"
FONTS = ("https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800"
         "&family=Inter:wght@400;600;700&family=Lexend:wght@400;600;700&family=Manrope:wght@400;600;800&display=swap")
CURSORS = ["Đồng Bitcoin", "Đồng Tether", "Ethereum", "Nến giá", "Mặc định"]
TRANSITIONS = {"Đẩy sang": "push", "Mờ dần": "fade", "Lật sách": "flip", "Thu phóng": "zoom",
               "Quét ngang": "wipe", "Mở cửa": "doors", "Không chuyển": None}
FONT_STACKS = {"Be Vietnam Pro": "'Be Vietnam Pro',sans-serif", "Inter": "Inter,sans-serif",
               "Lexend": "Lexend,sans-serif", "Manrope": "Manrope,sans-serif",
               "Times New Roman": "'Times New Roman',Times,serif", "Georgia": "Georgia,serif",
               "Arial": "Arial,Helvetica,sans-serif", "Verdana": "Verdana,sans-serif", "Courier New": "'Courier New',monospace"}
SIZES_PT = [10, 11, 12, 13, 14, 16, 18, 20]     # cỡ chữ như Word; 12 pt = 16 px (mặc định)
RANGES = {"1 ngày": 1, "7 ngày": 7, "30 ngày": 30, "Toàn bộ": None}
RQS = [
    "Sàn nào đóng góp nhiều nhất vào giá chung (giá hiệu quả) của Bitcoin?",
    "Vai trò dẫn giá có phụ thuộc khối lượng, chênh lệch mua–bán và độ sâu sổ lệnh không?",
    "Spot hay Perpetual futures (perp) dẫn giá?",
    "Chênh lệch giá giữa các sàn tồn tại bao lâu?",
    "Sau phí, chênh lệch mua–bán, trượt giá và độ trễ, còn bao nhiêu tín hiệu arbitrage có lãi?",
]
INTRO = ("Nhóm so sánh giá Bitcoin (BTC/USDT) trên 6 thị trường: Binance, OKX và Bybit, mỗi sàn có Spot "
         "và Perpetual futures (perp: hợp đồng tương lai không có ngày đáo hạn). Câu hỏi thứ nhất: giá chung hình thành ở sàn nào trước (đo bằng Information Share, "
         "Component Share và ILS). Câu hỏi thứ hai: khi giá các sàn lệch nhau, mua sàn rẻ bán sàn đắt có còn lãi "
         "sau phí, chênh lệch mua–bán và độ trễ không. Đây là mô phỏng nghiên cứu, không phải khuyến nghị giao dịch.")


@st.cache_resource(show_spinner=False)
def asset_uri(name: str) -> str:
    path = ASSETS / name
    if not path.exists():
        return ""
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
            ".avif": "image/avif"}.get(path.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def cursor_svg(name: str, size: int = 32) -> str:
    """Hình con trỏ chuột bằng SVG 32px, chọn theo tên trong CURSORS."""
    shapes = {
        "Đồng Bitcoin": "<circle cx='50' cy='50' r='46' fill='#F7931A' stroke='#fff' stroke-width='4'/>"
                        "<path d='M42 17v10M58 17v10M42 73v10M58 73v10' stroke='#fff' stroke-width='5' stroke-linecap='round'/>"
                        "<text x='50' y='66' font-size='50' font-weight='700' text-anchor='middle' fill='#fff' "
                        "font-family='Arial,sans-serif'>B</text>",
        "Đồng Tether": "<circle cx='50' cy='50' r='46' fill='#26A17B' stroke='#fff' stroke-width='4'/>"
                       "<rect x='27' y='26' width='46' height='11' fill='#fff'/><rect x='44' y='37' width='12' height='40' fill='#fff'/>"
                       "<ellipse cx='50' cy='46' rx='27' ry='7' fill='none' stroke='#fff' stroke-width='4'/>",
        "Ethereum": "<circle cx='50' cy='50' r='46' fill='#627EEA' stroke='#fff' stroke-width='4'/>"
                    "<path d='M50 16 27 52 50 42zM50 16 73 52 50 42zM27 57 50 85 50 61zM73 57 50 85 50 61z' fill='#fff'/>",
        "Nến giá": "<circle cx='50' cy='50' r='46' fill='#1B2A41' stroke='#fff' stroke-width='4'/>"
                   "<path d='M36 22v56M64 22v56' stroke='#fff' stroke-width='3'/>"
                   "<rect x='28' y='36' width='16' height='30' fill='#2FBF71'/><rect x='56' y='30' width='16' height='28' fill='#E5484D'/>",
    }
    return (f"<svg xmlns='http://www.w3.org/2000/svg' width='{size}' height='{size}' viewBox='0 0 100 100'>"
            + shapes[name] + "</svg>")


def _shade(hex_color: str, factor: float) -> str:
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{min(255, int(c * factor)):02x}" for c in rgb)


def _mix(a: str, b: str, w: float) -> str:
    """Pha hai màu hex: (1-w)*a + w*b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * (1 - w) + y * w):02x}" for x, y in zip(ca, cb))


def palette(mode: str, accent: str) -> dict:
    """Màu nền, thẻ, viền pha từ màu chủ đạo (tông pastel); chữ và độ tương phản theo chế độ sáng/tối."""
    m = MODES[mode]
    bg, surface, border = (_mix(accent, m["base"], w) for w in m["tints"])
    return dict(bg=bg, surface=surface, border=border, text=m["text"], muted=m["muted"], invert=m["invert"])


def _rgba(hex_color: str, a: float) -> str:
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{a})"


NEON = {"Sáng": "#ff7ac8", "Tối": "#ff4fd8"}   # màu phụ cho vệt sáng neon, pha với màu chủ đạo


def theme_css(mode: str, accent: str, effects: bool, cursor: str = "Đồng Bitcoin", transition: str = "Đẩy sang",
              font: str = "Be Vietnam Pro", size_pt: int = 12) -> str:
    """CSS toàn trang: nền có vệt sáng neon theo màu chủ đạo, thẻ kính mờ, điều hướng ở thanh bên, tiêu đề trang
    có ảnh mờ. Con trỏ chuột chỉ đổi khi effects=True. Chỉ đổi cách hiển thị, không đổi số liệu."""
    t = palette(mode, accent)
    s = SERIES[mode]
    dark = mode == "Tối"
    accent2 = _mix(accent, NEON[mode], 0.55)
    glass = _rgba(t["surface"], 0.995)       # khung nội dung gần như đặc: họa tiết nền không lộ xuyên qua biểu đồ
    sideglass = _rgba(t["surface"], 0.58 if dark else 0.74)
    soft = _rgba(t["surface"], 0.40 if dark else 0.55)           # khối chữ không chứa biểu đồ: trong, thấy họa tiết nền
    css = """@import url('__FONTS__');
html{font-size:__PX__px}
html,body,[data-testid="stAppViewContainer"],[data-testid="stSidebar"],button,input,select,textarea,
.stApp p,.stApp label,.stApp li,.stApp h1,.stApp h2,.stApp h3,.stApp h4,.stApp td,.stApp th,
.stApp [data-testid="stMetricValue"],.stApp [data-testid="stMarkdownContainer"] span{font-family:__BODYFONT__!important}
.stApp [data-testid="stMarkdownContainer"] span[role="img"],[data-testid="stIconMaterial"]{font-family:"Material Symbols Rounded"!important}
.stApp [data-testid="stMetricValue"],.stApp [data-testid="stDataFrame"],.card .big{font-variant-numeric:tabular-nums}
.stApp{background:
  radial-gradient(900px 520px at 6% -8%,__GLOW1__,transparent 70%),
  radial-gradient(760px 520px at 104% 12%,__GLOW2__,transparent 70%),
  radial-gradient(900px 600px at 60% 112%,__GLOW3__,transparent 70%),__BG__;
  background-attachment:fixed;color:__TEXT__}
[data-testid="stHeader"]{background:transparent}
[data-testid="stMain"] .block-container{position:relative;z-index:1;padding-top:1.6rem!important;max-width:1320px}
.deco{position:fixed;inset:0;pointer-events:none;z-index:-1;overflow:hidden}
.deco .coin{position:absolute;opacity:__COINOP__;filter:drop-shadow(0 0 18px __ACCENT__)}
.deco .c1{width:300px;right:-70px;top:70px;transform:rotate(-16deg)}
.deco .c2{width:210px;left:250px;bottom:-60px;transform:rotate(12deg)}
.deco .c3{width:120px;right:28%;top:46%;transform:rotate(-8deg);opacity:calc(__COINOP__ * .7)}
.deco .streak{position:absolute;height:2px;border-radius:2px;filter:blur(.6px);opacity:__STREAKOP__;
  background:linear-gradient(90deg,transparent,__ACCENT2__,__ACCENT__,transparent)}
.deco .s1{width:62vw;left:18vw;top:22vh;transform:rotate(-14deg)}
.deco .s2{width:48vw;left:40vw;top:64vh;transform:rotate(-14deg);opacity:calc(__STREAKOP__ * .7)}
.deco .s3{width:30vw;left:22vw;top:84vh;transform:rotate(-14deg);opacity:calc(__STREAKOP__ * .5)}
[data-testid="stSidebar"]{background:__SIDEGLASS__;backdrop-filter:blur(18px) saturate(140%);border-right:1px solid __BORDER__}
[data-testid="stSidebar"] .sb-brand{display:flex;align-items:center;gap:10px;font-weight:800;font-size:1.05rem;margin:0 0 14px;color:__TEXT__}
[data-testid="stSidebar"] .sb-brand small{display:block;font-weight:500;font-size:.72rem;color:__MUTED__}
.st-key-navbox [role="radiogroup"]{gap:4px;width:100%}
.st-key-navbox [role="radiogroup"] label{width:100%;padding:9px 12px;border-radius:12px;margin:0;transition:background .2s,box-shadow .2s}
.st-key-navbox [data-testid="stRadioOption"]>div>div:first-child:not([data-testid]){display:none!important}
.st-key-navbox [data-baseweb="radio"]>div:last-child{padding-left:0!important}
.st-key-navbox span[role="img"]{margin-right:8px;font-size:1.2rem}
.st-key-navbox [role="radiogroup"] label:hover{background:__HOVER__}
.st-key-navbox [role="radiogroup"] label:has(input:checked){background:linear-gradient(100deg,__ACCENT__,__ACCENT2__);
  box-shadow:0 6px 22px __GLOWBTN__}
.st-key-navbox [role="radiogroup"] label:has(input:checked) *{color:#fff!important;font-weight:600}
.stApp h2,.stApp h3,.stApp h4,.stApp label,.stApp p,.stApp li,.stApp [data-testid="stMetricValue"],.stApp [data-testid="stAlert"] *{color:__TEXT__}
.stApp h3{font-weight:700;letter-spacing:-.01em;border-left:4px solid __ACCENT__;padding:2px 0 2px 12px!important;margin-top:1.1rem}
.stApp h4{font-size:1.02rem;font-weight:700;margin:.9rem 0 .2rem}
.stApp [data-testid="stCaptionContainer"],.stApp [data-testid="stMetricLabel"] *{color:__MUTED__}
[data-baseweb="input"],[data-baseweb="select"]>div{background:__SURFACE__!important;border-radius:10px}
[data-baseweb="input"] input{color:__TEXT__!important}
[data-baseweb="select"] *{color:__TEXT__!important;background-color:transparent}
[data-baseweb="select"]>div{background:__SURFACE__!important}
[data-baseweb="popover"] [role="listbox"],[data-baseweb="popover"] li{background:__SURFACE__!important;color:__TEXT__!important}
[data-testid="stExpander"] summary,[data-testid="stExpander"] summary *{background:transparent!important;color:__TEXT__!important}
[data-testid="stExpander"] details,
[data-testid="stVerticalBlock"][data-test-wrap]{background:__GLASS__;border:1px solid __BORDER__!important;border-radius:18px;box-shadow:__SHADOW__}
[data-testid="stMetric"]{background:__SOFT__;backdrop-filter:blur(3px);border:1px solid __BORDER__!important;
  border-left:4px solid __ACCENT__!important;padding:12px 16px;border-radius:14px;box-shadow:__SHADOW__}
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] *,[data-testid="stMetricValue"],[data-testid="stMetricValue"] *{
 white-space:normal!important;overflow:visible!important;text-overflow:clip!important;line-height:1.25}
[data-testid="stMetricValue"]{font-weight:700;font-size:clamp(1.35rem,2.3vw,2rem)!important}
[data-testid="stDataFrame"]{filter:__INVERT__;border:1px solid __BORDER__;border-radius:12px}
.stDownloadButton button{background:linear-gradient(100deg,__ACCENT__,__ACCENT2__);border:0;border-radius:999px}
.stDownloadButton button p{color:#fff!important}
.stDownloadButton button:hover{filter:brightness(.92)}
.phead{position:relative;overflow:hidden;border-radius:22px;padding:26px 30px;margin:0 0 18px;min-height:132px;
  background:__SOFT__;backdrop-filter:blur(3px);border:1px solid __BORDER__;box-shadow:__SHADOW__}
.phead .img{position:absolute;inset:0 0 0 48%;background-size:cover;background-position:center;opacity:__IMGOP__;
  -webkit-mask-image:linear-gradient(to right,transparent,#000 60%);mask-image:linear-gradient(to right,transparent,#000 60%)}
.phead .glow{position:absolute;right:-60px;top:-80px;width:340px;height:340px;border-radius:50%;
  background:radial-gradient(circle,__GLOWBTN__,transparent 65%)}
.phead .body{position:relative;max-width:min(760px,100%)}
.phead .eyebrow{font-size:.74rem;letter-spacing:.12em;text-transform:uppercase;color:__ACCENT__;font-weight:700;margin-bottom:6px}
.phead h1{font-size:clamp(1.6rem,3vw,2.3rem);font-weight:800;line-height:1.15;margin:0 0 8px;padding:0!important;color:__TEXT__}
.phead h1.big{font-size:clamp(2rem,4.4vw,3.3rem)}
.phead .sub{color:__MUTED__;line-height:1.6;max-width:66ch}
@media (max-width:800px){.phead .img{inset:0;opacity:.14}.phead{padding:20px 18px}}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:6px 0 10px}
.card{background:__SOFT__;backdrop-filter:blur(3px);border:1px solid __BORDER__;border-radius:18px;padding:16px 18px 14px;box-shadow:__SHADOW__}
.card .rq{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:__ACCENT__;font-weight:700}
.card .q{font-size:.9rem;color:__MUTED__;margin:4px 0 10px;line-height:1.4}
.card .big{font-size:2.1rem;font-weight:800;line-height:1.05;color:__TEXT__;letter-spacing:-.02em}
.card .big.bad{color:__BAD__}
.card .lbl{font-weight:600;margin-top:4px;line-height:1.35}
.card .note{color:__MUTED__;font-size:.85rem;margin-top:8px;line-height:1.45}
.ex-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin:4px 0 14px}
.ex-card{background:__SOFT__;backdrop-filter:blur(3px);border:1px solid __BORDER__;border-radius:20px;overflow:hidden;box-shadow:__SHADOW__}
.ex-card .cover{position:relative;height:150px;background-size:cover;background-position:center}
.ex-card .cover::after{content:"";position:absolute;inset:0;background:linear-gradient(160deg,var(--brand) 0%,transparent 75%);opacity:.8}
.ex-card .cover .name{position:absolute;left:18px;bottom:14px;z-index:1;font-size:1.7rem;font-weight:800;color:#fff;
  text-shadow:0 2px 14px rgba(0,0,0,.45)}
.ex-card .cover .tag{position:absolute;right:14px;top:14px;z-index:1;background:rgba(0,0,0,.45);color:#fff;
  font-size:.72rem;font-weight:600;padding:3px 10px;border-radius:999px}
.ex-card .stats{display:grid;grid-template-columns:1fr 1fr;gap:10px 14px;padding:14px 18px 16px}
.ex-card .stats div{font-size:.78rem;color:__MUTED__}
.ex-card .stats b{display:block;font-size:1.05rem;color:__TEXT__;font-variant-numeric:tabular-nums}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-size:.78rem;font-weight:700;letter-spacing:.02em;
 border:1px solid __BORDER__;background:__GLASS__;color:__TEXT__;margin:0 6px 6px 0}
.badge.hist{border-color:__SPOT__;color:__SPOT__}.badge.live{border-color:__PERP__;color:__PERP__}
.badge.warn{border-color:__BAD__;color:__BAD__}
.verdict{border-radius:18px;padding:18px 22px;margin:8px 0 14px;border:1px solid __BORDER__;background:__SOFT__;backdrop-filter:blur(3px)}
.verdict .head{font-size:1.45rem;font-weight:800;line-height:1.2}
.verdict.no .head{color:__BAD__}
.verdict p{margin:.4rem 0 0;color:__TEXT__}
.rq{list-style:none;margin:4px 0 12px;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px}
.rq li{background:__SOFT__;border:1px solid __BORDER__;border-radius:12px;padding:9px 14px;line-height:1.4}
.rq b{color:__ACCENT__;margin-right:8px}
.method{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.method .card h5{margin:0 0 6px;font-size:1rem;font-weight:700;color:__TEXT__}
.method .card ul{margin:.2rem 0 0 1rem;padding:0}
.method .card li{margin:.15rem 0;line-height:1.45;color:__TEXT__}
.phead .img.art{inset:0 0 0 44%;background-size:contain;background-repeat:no-repeat;background-position:right center;
  opacity:.95;-webkit-mask-image:none;mask-image:none}
.tbl-wrap{overflow-x:auto;background:__GLASS__;backdrop-filter:blur(14px);border:1px solid __BORDER__;border-radius:16px;
  padding:4px 10px;margin:6px 0 12px;box-shadow:__SHADOW__}
.tbl{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}
.tbl th{text-align:left;font-weight:600;color:__MUTED__;padding:12px 10px;font-size:.78rem;white-space:nowrap}
.tbl th[title]{cursor:help}
.tbl td{padding:10px 10px;color:__TEXT__;border-top:1px solid __ROWLINE__;white-space:nowrap}
.tbl tbody tr:hover td{background:__HOVER__}
.tbl .r{text-align:right}.tbl .bad{color:__BAD__;font-weight:600}.tbl .muted{color:__MUTED__}
[data-testid="stButtonGroup"] button{border-radius:999px!important;border:1px solid __BORDER__!important;
  background:__GLASS__!important;padding:4px 16px!important;margin-right:6px}
[data-testid="stButtonGroup"] button p{color:__TEXT__!important;font-weight:500}
[data-testid="stButtonGroup"] button[aria-checked="true"]{background:linear-gradient(100deg,__ACCENT__,__ACCENT2__)!important;
  border-color:transparent!important;box-shadow:0 6px 18px __GLOWBTN__}
[data-testid="stButtonGroup"] button[aria-checked="true"] p{color:#fff!important;font-weight:600}
.ex-card .cover{background-color:#111}
.ex-card.pick .blurb{padding:14px 18px 18px;color:__TEXT__;line-height:1.5;font-size:.92rem}
.ex-hero{position:relative;border-radius:22px;overflow:hidden;min-height:230px;background-size:cover;background-position:center;
  margin:4px 0 10px;box-shadow:__SHADOW__}
.ex-hero::before{content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.75),rgba(0,0,0,.3) 60%,transparent)}
.ex-hero .body{position:relative;padding:30px 34px;max-width:640px}
.ex-hero .eyebrow{color:#fff;opacity:.8;font-size:.75rem;letter-spacing:.12em;text-transform:uppercase;font-weight:700}
.ex-hero h2{color:#fff!important;font-size:2.6rem;font-weight:800;margin:6px 0 8px;padding:0!important;border:0!important}
.ex-hero p{color:#f1f1f1!important;line-height:1.6;margin:0}
.figpng{border-radius:16px;overflow:hidden;border:1px solid __BORDER__;margin:4px 0 10px}
.figpng img{display:block;width:100%;height:auto;filter:__FIGFILTER__}
.guide{background:__SOFT__;border:1px solid __BORDER__;border-left:4px solid __ACCENT__;border-radius:14px;padding:14px 18px;margin:8px 0}
.guide .g-title{font-weight:700;margin-bottom:6px;color:__TEXT__}
.guide ol{margin:0 0 0 1.1rem;padding:0}.guide li{margin:.25rem 0;line-height:1.55;color:__TEXT__}
.guide .g-take{margin-top:10px;padding:10px 12px;border-radius:10px;background:__HOVER__;color:__TEXT__}
[data-testid="stFullScreenFrame"]:has(.modebar-btn[data-title="Close fullscreen"]){background:__BG__!important;padding:12px 18px;z-index:1000000!important;
  background-image:radial-gradient(900px 520px at 6% -8%,__GLOW1__,transparent 70%),radial-gradient(760px 520px at 104% 12%,__GLOW2__,transparent 70%)!important}
.stApp:has(.modebar-btn[data-title="Close fullscreen"]) [data-testid="stHeader"],.stApp:has(.modebar-btn[data-title="Close fullscreen"]) [data-testid="stHeader"] *,
.stApp:has(.modebar-btn[data-title="Close fullscreen"]) [data-testid="stSidebar"],.stApp:has(.modebar-btn[data-title="Close fullscreen"]) [data-testid="stSidebar"] *{visibility:hidden!important;backdrop-filter:none!important;background:transparent!important;border:0!important}
.phead .img.art.wide{inset:0 0 0 70%}
@keyframes page-in{from{opacity:0}to{opacity:1}}
[data-testid="stMain"] .block-container{animation:page-in .45s ease-out backwards}
@media (prefers-reduced-motion:reduce){[data-testid="stMain"] .block-container{animation:none}}
"""
    for key, value in {"__FONTS__": FONTS, "__BG__": t["bg"], "__SURFACE__": t["surface"], "__TEXT__": t["text"],
                       "__MUTED__": t["muted"], "__BORDER__": _rgba(t["border"], .9), "__INVERT__": t["invert"],
                       "__ACCENT2__": accent2, "__ACCENT__": accent, "__ACCENT_DARK__": _shade(accent, 0.7),
                       "__GLASS__": glass, "__SIDEGLASS__": sideglass, "__SOFT__": soft, "__HOVER__": _rgba(accent, .12), "__ROWLINE__": _rgba(t["border"], .5), "__FIGFILTER__": "invert(.9) hue-rotate(180deg) saturate(1.2)" if dark else "none",
                       "__GLOW1__": _rgba(accent, .34 if dark else .20), "__GLOW2__": _rgba(accent2, .30 if dark else .16),
                       "__GLOW3__": _rgba(accent, .22 if dark else .12), "__GLOWBTN__": _rgba(accent2, .45 if dark else .30),
                       "__SHADOW__": "0 10px 32px rgba(0,0,0,.35)" if dark else "0 10px 30px rgba(30,40,80,.08)",
                       "__COINOP__": ".14" if dark else ".10", "__STREAKOP__": ".38" if dark else ".24",
                       "__IMGOP__": ".42" if dark else ".5",
                       "__SPOT__": s["spot"], "__PERP__": s["perp"], "__BAD__": BAD,
                       "__BODYFONT__": FONT_STACKS.get(font, FONT_STACKS["Be Vietnam Pro"]),
                       "__PX__": f"{size_pt * 4 / 3:.1f}"}.items():
        css = css.replace(key, value)
    if effects:
        if cursor in CURSORS and cursor != "Mặc định":
            css += f'html,body,.stApp,.stApp *{{cursor:url("data:image/svg+xml,{quote(cursor_svg(cursor))}") 16 16, auto !important}}'
        kind = TRANSITIONS.get(transition)
        if kind:
            css += TRANSITION_CSS.replace("__K__", kind)
    return f"<style>{css}</style>"


def deco_html(accent: str) -> str:
    """Lớp trang trí cố định phía sau nội dung: ba đồng bitcoin viền mảnh và ba vệt sáng neon. Không nhận chuột."""
    coin = ("<svg class='coin {c}' viewBox='0 0 100 100' xmlns='http://www.w3.org/2000/svg'>"
            f"<circle cx='50' cy='50' r='46' fill='none' stroke='{accent}' stroke-width='2'/>"
            f"<circle cx='50' cy='50' r='38' fill='none' stroke='{accent}' stroke-width='1' stroke-dasharray='2 3'/>"
            f"<text x='50' y='66' font-size='46' font-weight='700' text-anchor='middle' fill='{accent}' "
            "font-family='Arial,sans-serif'>₿</text></svg>")
    return ("<div class='deco'>" + "".join(coin.format(c=c) for c in ("c1", "c2", "c3"))
            + "<div class='streak s1'></div><div class='streak s2'></div><div class='streak s3'></div></div>")


PAGE_IMAGES = {"Tổng quan": "hero_coins.avif", "Các sàn giao dịch": "coin_btc.avif", "So sánh các sàn": "data.avif",
               "Giá & độ phân tán": "dark.avif", "Sàn dẫn giá": "bit.avif", "Chênh lệch giá & lợi nhuận": "cyber.avif",
               "Phương pháp & dữ liệu": "data.avif", "Tải dữ liệu": "data.avif"}


def page_header(page: str, title: str, sub: str, eyebrow: str = "", big: bool = False) -> str:
    """Dải tiêu đề của mỗi trang: chữ bên trái, ảnh minh họa mờ bên phải (chỉ trong dải này, không đè nội dung)."""
    img = (f"<div class='img art{' wide' if big else ''}' "
           f"style=\"background-image:url({art_svg(PAGE_ART.get(page, 'home'))})\"></div>")
    return (f"<div class='phead'>{img}<div class='glow'></div><div class='body'>"
            + (f"<div class='eyebrow'>{escape(eyebrow)}</div>" if eyebrow else "")
            + f"<h1 class='{'big' if big else ''}'>{escape(title)}</h1><div class='sub'>{escape(sub)}</div></div></div>")


def hero_html() -> str:
    """Trang chủ: tên đề tài lớn và đoạn giới thiệu, có ảnh minh họa mờ bên phải."""
    return page_header("Tổng quan", "Giá Bitcoin hình thành ở đâu, và chênh lệch giữa các sàn có còn lãi?", INTRO,
                       "Crypto Price Discovery and Cross-Exchange Arbitrage · BTC/USDT", big=True)


def rq_html() -> str:
    """Năm câu hỏi nghiên cứu dạng danh sách gọn dưới các thẻ kết quả."""
    rqs = "".join(f"<li><b>RQ{i}</b>{escape(q)}</li>" for i, q in enumerate(RQS, 1))
    return f"<ol class='rq'>{rqs}</ol>"


def cards_html(cards: list) -> str:
    """Thẻ kết quả: mỗi thẻ = dict(rq, q, big, lbl, note, bad). Mọi chữ được escape."""
    out = []
    for c in cards:
        out.append(f"<div class='card'><div class='rq'>{escape(c['rq'])}</div><div class='q'>{escape(c['q'])}</div>"
                   f"<div class='big{' bad' if c.get('bad') else ''}'>{escape(c['big'])}</div>"
                   f"<div class='lbl'>{escape(c['lbl'])}</div><div class='note'>{escape(c['note'])}</div></div>")
    return f"<div class='cards'>{''.join(out)}</div>"


def badge(text: str, kind: str = "") -> str:
    return f"<span class='badge {kind}'>{escape(text)}</span>"


TRANSITION_CSS = """@keyframes tr-fade{from{opacity:0}to{opacity:1}}
@keyframes tr-push{from{opacity:.2;transform:translateX(90px)}to{opacity:1;transform:none}}
@keyframes tr-flip{from{opacity:0;transform:perspective(1400px) rotateY(-70deg)}to{opacity:1;transform:perspective(1400px) rotateY(0)}}
@keyframes tr-zoom{from{opacity:0;transform:scale(.86)}to{opacity:1;transform:none}}
@keyframes tr-wipe{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes tr-doors{from{clip-path:inset(0 50% 0 50%)}to{clip-path:inset(0 0 0 0)}}
[role="tabpanel"],[role="tabpanel"].tr-run{transform-origin:left center;animation:tr-__K__ .65s cubic-bezier(.22,.8,.3,1) both}
@media (prefers-reduced-motion:reduce){[role="tabpanel"].tr-run{animation:none}}"""

TRANSITION_JS = """<script>try{(function(){const w=window.parent;if(w.__trInit)return;w.__trInit=true;const d=w.document;
d.addEventListener('click',function(e){const t=e.target.closest('button[data-baseweb="tab"]');if(!t)return;
setTimeout(function(){const id=t.getAttribute('aria-controls');
const p=(id&&d.getElementById(id))||d.querySelector('[role="tabpanel"]:not([hidden])');if(!p)return;
p.classList.remove('tr-run');void p.offsetWidth;p.classList.add('tr-run')},30)},true)})()}catch(e){}</script>"""

def range_mask(index, choice: str, custom=None):
    """Chọn khoảng thời gian cho đồ thị: '1 ngày', '7 ngày', '30 ngày' (tính lùi từ quan sát cuối), 'Toàn bộ',
    hoặc 'Tùy chọn' với custom=(ngày đầu, ngày cuối) gồm cả hai ngày. Chỉ lọc hiển thị, không đổi số liệu."""
    if choice == "Tùy chọn" and custom is not None and len(custom) == 2:
        lo, hi = pd.Timestamp(custom[0]), pd.Timestamp(custom[1]) + pd.Timedelta(days=1)
        if index.tz is not None:
            lo, hi = lo.tz_localize(index.tz), hi.tz_localize(index.tz)
        return (index >= lo) & (index < hi)
    days = RANGES.get(choice)
    if days is None:
        return np.ones(len(index), dtype=bool)
    return index > index[-1] - pd.Timedelta(days=days)


def transition_js() -> str:
    """Mã chạy lại hiệu ứng chuyển tab mỗi lần bấm một thẻ (Streamlit không tự làm việc này)."""
    return TRANSITION_JS


# --------------------------------------------------------------------------- nạp dữ liệu
def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _load_panel(source: str, mtime: float) -> pd.DataFrame:
    return read_parquet(source, "D3")


@st.cache_data(show_spinner=False)
def _load_table(source: str, mtime: float, required: tuple, time_cols: tuple,
                expected_kind: str | None = None, venues: tuple = ()):
    """Validate optional D6/D7 without coercing units, timestamps or provenance."""
    path = Path(source)
    if not path.exists():
        return None, f"Chờ file: {source}"
    try:
        df = pd.read_parquet(path)
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"thiếu cột {missing}")
        kind = data_kind(df) if not df.empty else df.attrs.get("data_kind")
        if kind not in {"synthetic", "historical", "live"}:
            raise ValueError("data_kind: thiếu nguồn dữ liệu")
        if df.attrs.get("data_kind", kind) != kind or (expected_kind is not None and kind != expected_kind):
            raise ValueError(f"data_kind: nguồn {kind!r} không khớp panel {expected_kind!r} hoặc metadata")
        schema = "D6" if "available_at" in required else "D7"
        if not df.empty:
            validate_frame(df, schema)
            if schema == "D6":
                if df.duplicated(["window_start", "window_end", "venue"]).any():
                    raise ValueError("D6: duplicate window/venue")
                if not df.venue.map(lambda v: isinstance(v, str) and bool(VENUE_PATTERN.fullmatch(v))).all():
                    raise ValueError("D6: invalid venue")
                if venues and not df.venue.isin(venues).all():
                    raise ValueError("D6: venue không có trong panel")
            else:
                if df.event_id.duplicated().any():
                    raise ValueError("D7: duplicate event_id")
                if df.buy_exchange.eq(df.sell_exchange).any():
                    raise ValueError("D7: self pair")
                for side in ("buy", "sell"):
                    venue = df[f"{side}_exchange"] + "_" + df.market_type
                    if not venue.map(lambda v: isinstance(v, str) and bool(VENUE_PATTERN.fullmatch(v))).all():
                        raise ValueError("D7: invalid venue/market_type")
                    if venues and not venue.isin(venues).all():
                        raise ValueError("D7: venue không có trong panel")
                    if f"{side}_venue" in df and not df[f"{side}_venue"].eq(venue).all():
                        raise ValueError("D7: venue disagrees with exchange/market_type")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return None, f"Không dùng được {path.name}: {exc}"
    return df, None


LIVE_DEFAULT = ROOT / "data/processed/live_quotes_long.parquet"
LIVE_COLS = ["ts_recv_utc", "ts_grid_utc", "venue", "market_type", "bid", "ask", "mid", "spread_abs", "spread_bps",
             "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_truncated", "data_kind"]
DEPTH_COLS = ["depth_bid_10bps_usdt", "depth_ask_10bps_usdt"]


def live_latest(live: pd.DataFrame) -> pd.DataFrame:
    """Báo giá live mới nhất của từng venue THEO GIỜ NHẬN (ts_recv_utc lớn nhất, giờ thông tin thật sự có sẵn),
    không theo giờ lưới ts_grid_utc. Kèm cờ depth bị cắt của đúng báo giá đó, spread trung vị và số quote của cả mẫu.
    Không lấp, không nội suy: ô thiếu (ví dụ depth) để trống. Đơn vị: giá USDT, spread bps, depth USDT."""
    ordered = live.sort_values(["venue", "ts_recv_utc"])
    last = ordered.groupby("venue", sort=True).tail(1).set_index("venue")
    stats = ordered.groupby("venue")["spread_bps"].agg(spread_bps_median="median", n_quotes="size")
    cols = ["market_type", "ts_recv_utc", "bid", "ask", "spread_bps", "depth_bid_10bps_usdt",
            "depth_ask_10bps_usdt", "depth_truncated"]
    return last[cols].join(stats).reset_index()


@st.cache_data(show_spinner=False)
def _load_live(source: str, mtime: float, venues: tuple = ()):
    """Đọc live_quotes_long (D2). Trả về (DataFrame hoặc None, thông báo). Sai nguồn hoặc sai cột thì báo, không đoán.
    Từ chối: metadata hoặc cột data_kind không phải 'live' (kể cả synthetic), giờ không có múi giờ, giá bid/ask không
    dương, sổ lệnh chéo (bid > ask)."""
    path = Path(source)
    if not path.exists():
        return None, f"Chờ file: {path.name}"
    try:
        df = pd.read_parquet(path)
        missing = [c for c in LIVE_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"thiếu cột {missing}")
        if df.empty:
            raise ValueError("file rỗng")
        meta = df.attrs.get("data_kind")
        if meta is not None and meta != "live":
            raise ValueError(f"metadata data_kind={meta!r}, phải là 'live'")
        if df["data_kind"].isna().any() or set(df["data_kind"].unique()) != {"live"}:
            raise ValueError("data_kind phải là 'live'")
        for col in ("ts_recv_utc", "ts_grid_utc"):
            if not isinstance(df[col].dtype, pd.DatetimeTZDtype):
                raise ValueError(f"{col} phải có múi giờ (UTC), không tự coi giờ trần là UTC")
            if df[col].isna().any():
                raise ValueError(f"{col} có giá trị thiếu")
            df[col] = df[col].dt.tz_convert("UTC")
        for col in ("bid", "ask"):
            if not (df[col] > 0).all():
                raise ValueError(f"{col} phải dương và đủ (có giá âm, bằng 0 hoặc thiếu)")
        if (df["bid"] > df["ask"]).any():
            raise ValueError("sổ lệnh chéo: có báo giá bid > ask")
        if df["depth_truncated"].isna().any():
            raise ValueError("depth_truncated có giá trị thiếu")
        df["depth_truncated"] = df["depth_truncated"].astype(bool)
        if venues and not df["venue"].isin(venues).all():
            raise ValueError("venue không có trong panel")
        if not df["venue"].str.rsplit("_", n=1).str[1].eq(df["market_type"]).all():
            raise ValueError("venue không khớp market_type")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return None, f"Không dùng được {path.name}: {exc}"
    return df[LIVE_COLS], None


T5_COLS = ["backtest", "data_kind", "market_type", "latency", "n_signals", "n_filled", "n_positive_net",
           "mean_net_bps", "total_pnl_usdt"] + [c for c, _ in WATERFALL_STEPS]
T5B_COLS = ["backtest", "sample", "market_type", "fee_multiplier", "latency", "slippage_bps", "qty_btc",
            "n_signals", "n_net_positive"]
LEDGER_COLS = ["signal_id", "ts_fill", "market_type", "pnl_usdt", "status"]
REAL_KINDS = {"historical", "live"}


def kind_problem(df: pd.DataFrame, name: str, allowed=REAL_KINDS, required: bool = False):
    """Thông báo lỗi nếu cột data_kind của một bảng kết quả không phải dữ liệu thật (ví dụ synthetic); None nếu hợp lệ.
    required=True: bảng phải có cột data_kind. Với T5, backtest A phải là historical và B phải là live."""
    if "data_kind" not in df.columns:
        return f"{name} thiếu cột data_kind" if required else None
    kinds = set(df["data_kind"].dropna().astype(str))
    if df["data_kind"].isna().any() or not kinds or not kinds <= set(allowed):
        return f"{name} có data_kind={sorted(kinds) or 'trống'}; chỉ nhận {sorted(allowed)}, không nhận dữ liệu giả lập"
    if "backtest" in df.columns:
        want = df["backtest"].astype(str).str[:1].map({"A": "historical", "B": "live"})
        if (want.notna() & (want != df["data_kind"].astype(str))).any():
            return f"{name}: backtest A phải là historical và backtest B phải là live"
    return None


def _results_mtime(folder: str) -> float:
    return max([_mtime(str(p)) for p in Path(folder).glob("*")] or [0.0])


@st.cache_data(show_spinner=False)
def _load_results(folder: str, mtime: float) -> dict:
    """Đọc T5 (bắt buộc cho phần backtest), T5b và sổ lệnh backtest A ứng với độ trễ trong T5 (tùy chọn).
    Trả về dict t5/t5b/ledger_a (DataFrame hoặc None) và msg. Thiếu cột thì báo, không đoán."""
    base = Path(folder)
    out = dict(t5=None, t5b=None, ledger_a=None, msg=None, t6b=None, xcorr=None, t4=None, t3c=None, t1=None, t0=None)
    for key, (name, needed) in {"t6b": ("T6b_gap_by_regime.csv", ["source", "dimension", "level", "market_type",
                                                                  "rate_ratio_vs_reference", "reference_level"]),
                                "xcorr": (LEADLAG_FILE, ["venue_a", "venue_b", "lag", "correlation"]),
                                "t4": (REGRESSION_FILE, ["model", "term", "coef", "ci95_low", "ci95_high",
                                                         "p_value"]),
                                "t1": ("T1_exchange_characteristics.csv", ["venue", "fee_taker_bps",
                                                                           "avg_daily_volume_usdt",
                                                                           "median_spread_bps_live"]),
                                "t0": ("T0_data_quality.csv", ["venue", "missing_pct"])}.items():
        try:
            df = pd.read_csv(base / name)
            if all(c in df.columns for c in needed) and kind_problem(df, key.upper()) is None:
                out[key] = df
        except (OSError, ValueError):
            pass
    found = sorted(base.glob(T3C_GLOB))
    if found:
        try:
            t3c = pd.read_csv(found[0])
            if kind_problem(t3c, "T3c", {"historical"}) is None:
                out["t3c"] = t3c
        except (OSError, ValueError):
            pass
    try:
        t5 = pd.read_csv(base / "T5_arbitrage_net.csv")
        missing = [c for c in T5_COLS if c not in t5.columns]
        if missing:
            raise ValueError(f"T5 thiếu cột {missing}")
        problem = kind_problem(t5, "T5", required=True)
        if problem:
            raise ValueError(problem)
        out["t5"] = t5
    except (OSError, ValueError) as exc:
        out["msg"] = f"Chờ kết quả backtest (T5_arbitrage_net.csv): {exc}"
        return out
    try:
        t5b = pd.read_csv(base / "T5b_cost_sensitivity.csv")
        if not all(c in t5b.columns for c in T5B_COLS):
            raise ValueError("T5b thiếu cột")
        out["t5b"] = t5b
    except (OSError, ValueError):
        pass
    rows_a = t5[t5["backtest"].str.startswith("A_")]
    if not rows_a.empty:
        minutes = int(pd.Timedelta(rows_a["latency"].iloc[0]).total_seconds() // 60)
        try:
            ledger = pd.read_parquet(base / f"backtest_a_dev_L{minutes}_ledger.parquet")
            if all(c in ledger.columns for c in LEDGER_COLS) and kind_problem(ledger, "Sổ lệnh A", {"historical"}) is None:
                out["ledger_a"] = ledger
        except (OSError, ValueError):
            pass
    return out


def _load_manifest(kind: str):
    """split_manifest (chỉ đọc) để ghi rõ sự kiện gap thuộc tập nào; chỉ dùng khi cùng data_kind với panel."""
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if manifest.get("data_kind") == kind else None


GLOSSARY = {
    "venue": "Thị trường = sàn × loại hợp đồng. Ví dụ Binance · Spot là BTC/USDT spot trên Binance.",
    "market_type": "Spot (spot) hoặc Perpetual futures (perp): hợp đồng tương lai không có ngày đáo hạn, giá được neo về giá spot bằng phí funding. Chỉ so sánh các sàn cùng loại với nhau.",
    "close_usdt": "Giá đóng cửa của nến 1 phút ở phút cuối của dữ liệu lịch sử, tính bằng USDT.",
    "missing": "Được đánh dấu khi sàn không có giá ở phút này (ô giá để trống, không điền thay).",
    "ts_grid_utc": "Mốc thời gian (UTC) trên lưới 5 giây mà báo giá này được gán vào.",
    "ts_recv_utc": "Thời điểm (UTC) máy thu nhận được báo giá. Báo giá 'mới nhất' luôn chọn theo mốc này, không theo lưới 5 giây.",
    "depth_truncated": "Sổ lệnh quan sát bị cắt trước khi đủ ±0,1% quanh giá giữa, nên độ sâu chỉ là cận dưới (hiện kèm dấu ≥).",
    "bid": "Giá mua cao nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "ask": "Giá bán thấp nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "spread_bps": "Chênh lệch giữa giá bán và giá mua so với giá giữa, tính bằng bps (1 bps = 0,01%). Càng nhỏ thì vào lệnh càng rẻ.",
    "spread_bps_median": "Trung vị chênh lệch mua–bán trên toàn bộ mẫu live (đại diện cho cả mẫu, không phải báo giá cuối).",
    "n_quotes": "Số báo giá live của sàn này trong mẫu.",
    "depth_bid_10bps_usdt": "Tổng giá trị lệnh mua đang chờ trong khoảng 0,1% dưới giá giữa, tính bằng USDT. Càng lớn thì lệnh lớn càng ít trượt giá. Dấu ≥: sổ lệnh quan sát bị cắt nên đây chỉ là cận dưới.",
    "depth_ask_10bps_usdt": "Tổng giá trị lệnh bán đang chờ trong khoảng 0,1% trên giá giữa, tính bằng USDT. Dấu ≥: sổ lệnh quan sát bị cắt nên đây chỉ là cận dưới.",
    "is_lower": "Cận dưới của Information Share (IS): phần tối thiểu sàn này đóng góp vào việc hình thành giá chung (0 đến 100%). Tính qua mọi thứ tự xếp các sàn.",
    "is_upper": "Cận trên của Information Share. Khoảng giữa hai cận càng rộng thì càng khó nói sàn này đóng góp bao nhiêu.",
    "is_mid": "Điểm giữa của hai cận. Chỉ để xếp thứ tự tham khảo, không phải kết luận.",
    "component_share": "Component Share (CS, Gonzalo–Granger): trọng số có dấu của sàn trong thành phần giá chung. Tổng các sàn trong một cửa sổ bằng 1, nhưng từng sàn có thể âm hoặc lớn hơn 1 nên không đọc như phần trăm; độ lớn chỉ nói mô hình dựa vào sàn đó mạnh hay yếu.",
    "ils": "Information Leadership Share (ILS): kết hợp Information Share và Component Share để tách sàn phản ứng nhanh khỏi sàn ít nhiễu. Ở đây là trung bình theo từng cặp sàn nên cộng các dòng lại không bằng 1.",
    "status": "Ước lượng được nghĩa là mô hình chạy được cho cửa sổ này. Giá trị khác là cửa sổ lỗi; xem cột Kết luận.",
    "reason": "Kết luận của cửa sổ. Các khoảng IS chồng nhau nghĩa là chưa đủ bằng chứng để nói sàn nào dẫn giá.",
    "su_kien": "Số sự kiện chênh lệch giá giữa hai sàn trong nhóm. Một sự kiện gom các quan sát liên tiếp mà chênh lệch vượt ngưỡng.",
    "trung_vi_bps": "Trung vị của chênh lệch trong nhóm, tính bằng bps (một nửa số sự kiện lớn hơn số này).",
    "thieu": "Số sự kiện chưa có giá trị (không tính vào trung vị).",
    "ts_start": "Thời điểm (UTC) sự kiện bắt đầu.",
    "ts_end": "Thời điểm (UTC) quan sát cuối của sự kiện.",
    "duration_s": "Thời gian sự kiện tồn tại, tính bằng giây.",
    "buy_exchange": "Sàn mua trong cặp (sàn rẻ hơn).",
    "sell_exchange": "Sàn bán trong cặp (sàn đắt hơn).",
    "gross_bps": "Chênh lệch giữa giá bán và giá mua lúc sự kiện bắt đầu, trước mọi chi phí, tính bằng bps.",
    "net_bps": "Chênh lệch lúc bắt đầu sau khi trừ phí taker hai chân, nửa chênh lệch mua–bán, trượt giá và chi phí khác (theo cấu hình của TV6). Dương nghĩa là còn lãi nếu khớp NGAY, chưa tính độ trễ.",
}
# Tên hiển thị và kiểu định dạng của từng cột (chỉ đổi cách hiển thị, dữ liệu và tên cột gốc giữ nguyên).
COLUMN_SPECS = {
    "venue": ("Thị trường", "text"), "market_type": ("Loại thị trường", "text"),
    "close_usdt": ("Giá đóng cửa (USDT)", "price"), "missing": ("Dữ liệu", "bool"),
    "ts_grid_utc": ("Thời điểm lưới (UTC)", "time_s"), "ts_recv_utc": ("Nhận lúc (UTC)", "time_s"),
    "depth_truncated": ("Độ sâu bị cắt", "text"), "bid": ("Giá mua (USDT)", "price"),
    "ask": ("Giá bán (USDT)", "price"), "spread_bps": ("Mua–bán (bps)", "bps"),
    "spread_bps_median": ("Mua–bán TV (bps)", "bps"), "n_quotes": ("Số báo giá", "int"),
    "depth_bid_10bps_usdt": ("Độ sâu ±0,1% (mua)", "big"),
    "depth_ask_10bps_usdt": ("Độ sâu ±0,1% (bán)", "big"),
    "is_lower": ("IS cận dưới", "share"), "is_upper": ("IS cận trên", "share"),
    "is_mid": ("IS điểm giữa", "share"), "component_share": ("CS (có dấu)", "num"),
    "ils": ("ILS", "num"), "status": ("Trạng thái", "text"), "reason": ("Kết luận", "text"),
    "su_kien": ("Số sự kiện", "int"), "trung_vi_bps": ("Trung vị (bps)", "bps"), "thieu": ("Thiếu giá trị", "int"),
    "ts_start": ("Bắt đầu (UTC)", "time"), "ts_end": ("Kết thúc (UTC)", "time"),
    "duration_s": ("Kéo dài (giây)", "int"), "buy_exchange": ("Mua ở", "text"), "sell_exchange": ("Bán ở", "text"),
    "gross_bps": ("Trước phí (bps)", "bps"), "net_bps": ("Sau phí (bps)", "bps"),
}
QUOTE_COLS = ["venue", "market_type", "close_usdt", "missing"]
LIVE_SHOWN_COLS = ["venue", "market_type", "ts_recv_utc", "bid", "ask", "spread_bps",
                   "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_truncated", "spread_bps_median", "n_quotes"]
RANK_COLS = ["venue", "is_lower", "is_upper", "is_mid", "component_share", "ils", "status", "reason"]
GAP_SUMMARY_COLS = ["market_type", "su_kien", "trung_vi_bps", "thieu"]
GAP_EVENT_COLS = ["ts_start", "duration_s", "market_type", "buy_exchange", "sell_exchange", "gross_bps", "net_bps"]


def label_of(col: str) -> str:
    return COLUMN_SPECS.get(col, (col, ""))[0]


def col_config(cols):
    """Tên cột tiếng Việt và tooltip giải thích (rê chuột lên tiêu đề cột). Số đã được định dạng thành chữ
    kiểu Việt Nam trong pretty(), nên cột số căn phải bằng TextColumn."""
    cfg = {}
    for c in cols:
        if c not in GLOSSARY:
            continue
        label, kind = COLUMN_SPECS.get(c, (c, "text"))
        common = dict(label=label, help=GLOSSARY[c])
        if kind == "time":
            cfg[c] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm", timezone="UTC", **common)
        elif kind == "time_s":
            cfg[c] = st.column_config.DatetimeColumn(format="DD/MM HH:mm:ss", timezone="UTC", **common)
        elif kind in ("price", "bps", "num", "share", "big", "int"):
            cfg[c] = st.column_config.TextColumn(alignment="right", **common)
        else:
            cfg[c] = st.column_config.TextColumn(**common)
    return cfg


def pretty(df: pd.DataFrame) -> pd.DataFrame:
    """Bản sao để HIỂN THỊ: tên thị trường, trạng thái và số theo kiểu Việt Nam (83.565,4; −8,10; 4,33%).
    Dữ liệu tải về vẫn là bản gốc, không đổi."""
    out = df.copy()
    if "venue" in out:
        out["venue"] = out["venue"].map(venue_name)
    if "market_type" in out:
        out["market_type"] = out["market_type"].map(lambda m: MARKET_NAME.get(m, m))
    for col in ("buy_exchange", "sell_exchange"):
        if col in out:
            out[col] = out[col].map(lambda e: EXCHANGE_NAME.get(e, e))
    if "status" in out:
        out["status"] = out["status"].map(lambda s: STATUS_TEXT.get(str(s).lower(), s))
    if "reason" in out:
        out["reason"] = out["reason"].map(lambda r: REASON_SHORT.get(r, r) if isinstance(r, str) else "")
    if "missing" in out:
        out["missing"] = out["missing"].map(lambda m: "Thiếu" if m is True or m == 1 else ("Đủ" if m is False or m == 0 else "–"))
    formats = {"price": lambda v: fmt.num(v, 1), "bps": fmt.bps, "num": lambda v: fmt.num(v, 3), "share": fmt.pct,
               "big": fmt.usdt_big, "int": lambda v: fmt.num(v)}
    cut = out["depth_truncated"].fillna(False).astype(bool) if "depth_truncated" in out else None
    for col in out.columns:
        kind = COLUMN_SPECS.get(col, ("", ""))[1]
        if col in DEPTH_COLS and cut is not None:     # sổ lệnh bị cắt: độ sâu chỉ là cận dưới
            out[col] = [("–" if pd.isna(v) else ("≥ " if c else "") + fmt.usdt_big(v)) for v, c in zip(out[col], cut)]
        elif kind in formats:
            out[col] = out[col].map(formats[kind])
    if "depth_truncated" in out:
        out["depth_truncated"] = ["Bị cắt" if c else "Đủ" for c in cut]
    return out


def table(df: pd.DataFrame, cols: list):
    """Bảng gọn kiểu thẻ: tên cột tiếng Việt, rê chuột lên tiêu đề để xem giải thích, số căn phải."""
    shown = pretty(df)[cols]
    right = [c for c in cols if COLUMN_SPECS.get(c, ("", ""))[1] in ("price", "bps", "num", "share", "big", "int")]
    st.markdown(html_table(shown, right, {c: GLOSSARY[c] for c in cols if c in GLOSSARY},
                           {c: label_of(c) for c in cols}), unsafe_allow_html=True)


def explain(cols, title="Giải thích các cột"):
    """Khung mở ra liệt kê ý nghĩa từng cột của bảng ngay phía trên."""
    with st.expander(title):
        st.markdown("\n".join(f"- **{label_of(c)}**: {GLOSSARY[c]}" for c in cols if c in GLOSSARY))


TAB_LABELS = ["Tổng quan", "Các sàn giao dịch", "So sánh các sàn", "Giá & độ phân tán", "Sàn dẫn giá",
              "Chênh lệch giá & lợi nhuận", "Phương pháp & dữ liệu", "Tải dữ liệu"]
GUIDE_STEPS = [
    ("Tổng quan", "câu trả lời ngắn cho 5 câu hỏi nghiên cứu, số liệu lấy thẳng từ kết quả."),
    ("Các sàn giao dịch", "giới thiệu Binance, OKX, Bybit và số liệu của từng sàn."),
    ("So sánh các sàn", "xếp hạng khối lượng, chi phí; thị phần và giá so với các sàn cùng loại."),
    ("Giá & độ phân tán", "giá trên từng thị trường và mức các sàn cùng loại lệch giá nhau."),
    ("Sàn dẫn giá", "sàn nào đóng góp vào giá chung, spot hay perp dẫn, và các yếu tố ảnh hưởng."),
    ("Chênh lệch giá & lợi nhuận", "chênh lệch kéo dài bao lâu, và có còn lãi sau phí, độ trễ không."),
    ("Phương pháp & dữ liệu", "nguồn dữ liệu, mô hình, cách tính chi phí và các giới hạn."),
    ("Tải dữ liệu", "lấy bảng đang xem về máy dạng CSV."),
]


def _default_path(contract: Path) -> str:
    """Không tự chuyển provenance khi thiếu file; người dùng chọn nguồn rõ ràng."""
    return str(contract)


def backtest_name(backtest: str) -> str:
    return "Backtest A · lịch sử" if str(backtest).startswith("A_") else "Backtest B · live"


def latency_text(latency: str) -> str:
    return fmt.latency(latency)


def _fmt_day(ts) -> str:
    return f"{pd.Timestamp(ts):%d/%m/%Y}"


def price_ranges(quotes: pd.DataFrame) -> dict:
    """Chênh lệch giá cao nhất − thấp nhất (bps) trong từng loại hợp đồng, và basis perp − spot trung bình các sàn (bps).
    Chỉ so cùng loại hợp đồng; basis là chênh lệch khác loại nên KHÔNG phải cơ hội arbitrage."""
    out = {}
    for market in ("spot", "perp"):
        p = quotes.loc[quotes["market_type"] == market, "close_usdt"].dropna()
        out[market] = (p.max() - p.min()) / p.mean() * 1e4 if len(p) > 1 else np.nan
    wide = quotes.assign(exchange=quotes["venue"].str.rsplit("_", n=1).str[0]).pivot_table(
        index="exchange", columns="market_type", values="close_usdt")
    if {"spot", "perp"} <= set(wide.columns):
        both = wide.dropna(subset=["spot", "perp"])
        out["basis"] = ((both["perp"] - both["spot"]) / both["spot"] * 1e4).mean() if len(both) else np.nan
        out["basis_n"] = len(both)
    else:
        out["basis"], out["basis_n"] = np.nan, 0
    return out


def daily_volume(panel: pd.DataFrame, venues: list) -> pd.DataFrame:
    """Tổng khối lượng (BTC) mỗi ngày của từng thị trường từ cột volume_base__{venue}; ngày không có dữ liệu để trống."""
    cols = {v: f"volume_base__{v}" for v in venues if f"volume_base__{v}" in panel.columns}
    if not cols:
        return pd.DataFrame()
    data = panel[list(cols.values())].rename(columns={c: v for v, c in cols.items()})
    return data.resample("1D").sum(min_count=1)


def premium_bps(panel: pd.DataFrame, venues: list, market: str):
    """Giá đóng cửa của từng thị trường cao/thấp hơn trung bình các thị trường CÙNG LOẠI bao nhiêu bps.
    Chỉ tính ở phút đủ giá của cả nhóm (không lấp). None nếu nhóm < 2 thị trường."""
    group = [v for v in venues if v.endswith("_" + market)]
    if len(group) < 2:
        return None
    prices = panel[[f"close__{v}" for v in group]].set_axis(group, axis=1)
    mean = prices.mean(axis=1)
    out = prices.sub(mean, axis=0).div(mean, axis=0) * 1e4
    out[~prices.notna().all(axis=1)] = np.nan
    return out


def live_spread_median(live, t1):
    """(Series theo venue, nguồn). Ưu tiên D2 (live) đã nạp vì T1 có thể cũ hơn D2; không có D2 thì dùng cột của T1."""
    if live is not None and not live.empty:
        return live.groupby("venue")["spread_bps"].median(), "D2"
    if t1 is not None and "median_spread_bps_live" in t1:
        return t1.set_index("venue")["median_spread_bps_live"], "T1"
    return pd.Series(dtype=float), None


def rank_table(t1: pd.DataFrame, d6, t0, live=None) -> pd.DataFrame:
    """Bảng xếp hạng 6 thị trường: khối lượng (cao tốt), chênh lệch mua–bán live tính từ D2 (thấp tốt), phí taker (thấp tốt),
    IS và ILS trung bình trên các cửa sổ (cao = ảnh hưởng nhiều hơn), hạng ảnh hưởng tổng hợp = hạng của trung bình hai hạng
    IS và ILS, tỷ lệ phút thiếu. Hạng 1 = tốt nhất theo từng tiêu chí; giá trị thiếu cho hạng NaN, không làm bảng lỗi."""
    out = t1[["venue", "avg_daily_volume_usdt", "fee_taker_bps"]].copy()
    spread, source = live_spread_median(live, t1)
    out["spread_median_bps"] = out["venue"].map(spread)
    out["r_vol"] = out["avg_daily_volume_usdt"].rank(ascending=False, method="min")
    out["r_spread"] = out["spread_median_bps"].rank(ascending=True, method="min")
    out["r_fee"] = out["fee_taker_bps"].rank(ascending=True, method="min")
    if d6 is not None and not d6.empty:
        by = d6.groupby("venue")
        out["is_mid_mean"] = out["venue"].map(by["is_mid"].mean())
        out["ils_mean"] = out["venue"].map(by["ils"].mean())
        out["r_is"] = out["is_mid_mean"].rank(ascending=False, method="min")
        out["r_ils"] = out["ils_mean"].rank(ascending=False, method="min")
        out["r_influence"] = ((out["r_is"] + out["r_ils"]) / 2).rank(ascending=True, method="min")
    if t0 is not None and "missing_pct" in t0:
        out["missing_pct"] = out["venue"].map(t0.set_index("venue")["missing_pct"])
    out.attrs["spread_source"] = source
    return out.sort_values("r_vol").reset_index(drop=True)


SE_TEXT = {"dk": "Driscoll–Kraay (vững với tự tương quan theo thời gian và tương quan chéo giữa các thị trường)",
           "group": "HAC trong từng thị trường",
           "cluster": "theo cụm phiên thu thập (session)"}


def se_text(cov: str, n_groups=None) -> str:
    """Mô tả đúng loại sai số chuẩn của một mô hình T4 theo cột `cov`: 'cluster' (mẫu live) chỉ có vài phiên nên
    p-value và khoảng tin cậy dùng phân phối t với G−1 bậc tự do."""
    base = SE_TEXT.get(str(cov), str(cov))
    if str(cov) == "cluster" and n_groups is not None and not pd.isna(n_groups) and int(n_groups) >= 2:
        g = int(n_groups)
        return f"{base}: chỉ có {g} phiên, p-value và khoảng tin cậy dùng phân phối t với {g - 1} bậc tự do nên kết quả kém chắc chắn"
    return base


def regression_se_lines(t4) -> list:
    """Dòng mô tả sai số chuẩn cho mô hình A (lịch sử) và B (live), đọc từ T4; thiếu T4 thì chỉ trỏ tới file."""
    if t4 is None or "cov" not in t4.columns:
        return ["Loại sai số chuẩn ghi trong cột cov của T4_leadership_determinants.csv."]
    lines = []
    for model, label in (("A", "Mẫu lịch sử (mô hình A)"), ("B", "Mẫu live (mô hình B)")):
        sub = t4[t4["model"] == model]
        if not sub.empty:
            groups = sub["n_groups"].iloc[0] if "n_groups" in sub else None
            lines.append(f"{label}: sai số chuẩn {se_text(sub['cov'].iloc[0], groups)}.")
    return lines


def exchange_rows(ex: str, t1, live, d6, d7, t0, quotes) -> pd.DataFrame:
    """Hồ sơ một sàn: mỗi dòng là một chỉ tiêu, hai cột Spot / Perpetual futures. Mọi số đọc từ file."""
    rows = []
    def add(name, spot, perp):
        rows.append({"Chỉ tiêu": name, "Spot": spot, "Perpetual futures": perp})
    def per(fn):
        return [fn(f"{ex}_{m}") for m in ("spot", "perp")]
    t1i = t1.set_index("venue") if t1 is not None else None
    q = quotes.set_index("venue")
    add("Giá đóng cửa phút cuối (USDT)", *per(lambda v: fmt.num(q.loc[v, "close_usdt"], 1) if v in q.index else "–"))
    if t1i is not None:
        get = lambda v, c: t1i.loc[v, c] if v in t1i.index and c in t1i.columns else np.nan
        add("Phí taker một chiều (bps)", *per(lambda v: fmt.num(get(v, "fee_taker_bps"), 1)))
        add("Khối lượng TB mỗi ngày (USDT)", *per(lambda v: fmt.usdt_big(get(v, "avg_daily_volume_usdt"))))
        add("Khối lượng TB mỗi ngày (BTC)", *per(lambda v: fmt.num(get(v, "avg_daily_volume_btc"))))
        spread, _ = live_spread_median(live, t1)
        add("Chênh lệch mua–bán live, trung vị (bps)", *per(lambda v: fmt.bps(spread.get(v, np.nan))))
        add("Funding TB mỗi kỳ (bps)", *per(lambda v: fmt.bps(get(v, "avg_funding_rate_bps")) if v.endswith("_perp") else "không áp dụng"))
        add("Open interest TB (BTC)", *per(lambda v: fmt.num(get(v, "avg_oi_base_btc")) if v.endswith("_perp") else "không áp dụng"))
    if live is not None:
        # Báo giá bị cắt chỉ cho cận dưới của độ sâu: không đưa vào trung vị.
        full = live[~live["depth_truncated"]]
        depth = full.groupby("venue")[DEPTH_COLS].median()
        add("Độ sâu mua ±0,1% live, trung vị (USDT, chỉ báo giá đủ sâu)", *per(lambda v: fmt.usdt_big(depth.loc[v, "depth_bid_10bps_usdt"]) if v in depth.index else "–"))
        add("Độ sâu bán ±0,1% live, trung vị (USDT, chỉ báo giá đủ sâu)", *per(lambda v: fmt.usdt_big(depth.loc[v, "depth_ask_10bps_usdt"]) if v in depth.index else "–"))
        cut = live.groupby("venue")["depth_truncated"].mean()
        add("Tỷ lệ báo giá live có độ sâu bị cắt", *per(lambda v: fmt.pct(cut.get(v, np.nan), 1)))
    if d6 is not None and not d6.empty:
        is_mean = d6.groupby("venue")["is_mid"].mean()
        add("IS điểm giữa trung bình (tham khảo)", *per(lambda v: fmt.pct(is_mean.get(v, np.nan), 1)))
    if d7 is not None and not d7.empty:
        add("Số sự kiện chênh lệch có tham gia", *per(lambda v: fmt.num(int(
            ((d7["market_type"] == v.rsplit("_", 1)[1]) & ((d7["buy_exchange"] == ex) | (d7["sell_exchange"] == ex))).sum()))))
    if t0 is not None and "missing_pct" in t0:
        miss = t0.set_index("venue")["missing_pct"]
        add("Phút thiếu dữ liệu (lịch sử)", *per(lambda v: fmt.pct(miss.get(v, np.nan) / 100, 2)))
    return pd.DataFrame(rows)


EXCHANGES = {
    "binance": dict(name="Binance", brand="#F0B90B", cover="hero_coins.avif", since="2017",
                    intro="Thành lập năm 2017, là một trong những sàn tiền mã hóa lớn nhất thế giới về khối lượng giao "
                          "dịch, có cả thị trường spot và Perpetual futures USDT-M."),
    "okx": dict(name="OKX", brand="#3d3d3d", cover="dark.avif", since="2017",
                intro="Thành lập năm 2017 với tên OKEx, đổi tên thành OKX năm 2022; mạnh về phái sinh, có cả spot "
                      "và Perpetual futures."),
    "bybit": dict(name="Bybit", brand="#F7A600", cover="bit.avif", since="2018",
                  intro="Thành lập năm 2018, ban đầu tập trung vào hợp đồng phái sinh, sau mở rộng sang spot."),
}


def exchange_cards_html(t1) -> str:
    """Ba thẻ sàn có ảnh bìa (ảnh minh họa chung, phủ màu nhận diện của sàn) và số liệu chính từ T1."""
    t1i = t1.set_index("venue") if t1 is not None else None
    out = []
    for ex, info in EXCHANGES.items():
        uri = asset_uri(info["cover"])
        def val(m, col, f):
            v = f"{ex}_{m}"
            return f(t1i.loc[v, col]) if t1i is not None and v in t1i.index else "–"
        stats = [("KL/ngày spot", val("spot", "avg_daily_volume_usdt", lambda x: fmt.usdt_big(x) + " USDT")),
                 ("KL/ngày perp", val("perp", "avg_daily_volume_usdt", lambda x: fmt.usdt_big(x) + " USDT")),
                 ("Phí taker spot", val("spot", "fee_taker_bps", lambda x: fmt.num(x, 1) + " bps")),
                 ("Phí taker perp", val("perp", "fee_taker_bps", lambda x: fmt.num(x, 1) + " bps"))]
        out.append(f"<div class='ex-card' style='--brand:{info['brand']}'><div class='cover' "
                   f"style=\"background-image:url({uri})\"><span class='tag'>Từ {info['since']}</span>"
                   f"<div class='name'>{escape(info['name'])}</div></div><div class='stats'>"
                   + "".join(f"<div>{escape(k)}<b>{escape(v)}</b></div>" for k, v in stats) + "</div></div>")
    return f"<div class='ex-grid'>{''.join(out)}</div>"


def home_cards(panel, venues, d6, d7, res) -> list:
    """Thẻ kết quả trang chủ. Chỉ thêm thẻ khi có file nguồn; mọi số đều tính từ file, không gõ tay."""
    cards = [dict(rq="Dữ liệu", q="Mẫu lịch sử dùng cho mọi kết quả",
                  big=f"{fmt.num(len(panel))} phút", lbl=f"{len(venues)} thị trường, nến 1 phút",
                  note=f"{_fmt_day(panel.index[0])} → {_fmt_day(panel.index[-1])} (UTC)")]
    if d6 is not None and not d6.empty:
        counts, total = leader_counts(d6)
        decided = sum(counts.values())
        cards.append(dict(rq="RQ1 · RQ3", q=RQS[0], big=f"{fmt.num(decided)} / {fmt.num(total)}",
                          lbl="cửa sổ 1 ngày xác định được sàn dẫn giá",
                          note="Các khoảng IS chồng nhau: dữ liệu 1 phút chưa đủ để nói sàn nào, hay spot "
                               "hay perp, dẫn giá."))
    if d7 is not None and not d7.empty:
        single = (d7["n_obs"].eq(1).mean() if "n_obs" in d7 else np.nan)
        note = (f"{fmt.num(single * 100, 1)}% sự kiện chỉ có một quan sát. " if pd.notna(single) else "")
        cards.append(dict(rq="RQ4", q=RQS[3], big=f"{fmt.num(d7['duration_s'].median())} giây",
                          lbl=f"thời gian tồn tại trung vị của {fmt.num(len(d7))} sự kiện chênh lệch",
                          note=note + f"90% sự kiện kết thúc trong {fmt.num(d7['duration_s'].quantile(.9))} giây."))
    t5 = res.get("t5")
    if t5 is not None:
        sig, pos = int(t5["n_filled"].sum()), int(t5["n_positive_net"].sum())
        note = "; ".join(f"{backtest_name(b)}: {fmt.num(int(g['n_filled'].sum()))} lệnh, "
                         f"{fmt.num(g['total_pnl_usdt'].sum(), 2)} USDT" for b, g in t5.groupby("backtest", sort=True)) + "."
        t5b = res.get("t5b")
        if t5b is not None:
            fs = fee_scenarios(t5b)
            note += (f" Kiểm tra độ nhạy: {fmt.num(fs['with_profit'])} / {fmt.num(fs['n'])} kịch bản còn tính phí "
                     "có lệnh lãi; chỉ khi bỏ hẳn phí mới có lãi.")
        cards.append(dict(rq="RQ5", q=RQS[4], big=f"{fmt.num(pos)} / {fmt.num(sig)}", bad=pos == 0,
                          lbl="lệnh backtest có lãi sau phí và độ trễ", note=note))
    return cards


def perp_waterfall_row(t5: pd.DataFrame):
    """Dòng T5 của Perpetual futures trong backtest A (lịch sử), dùng cho biểu đồ nhỏ ở trang chủ. None nếu thiếu."""
    rows = t5[t5["backtest"].astype(str).str.startswith("A_") & t5["market_type"].eq("perp") & (t5["n_filled"] > 0)]
    return None if rows.empty else rows.iloc[0]


def plot(fig, code: str, key: str):
    # theme=None: giữ màu chữ/lưới do charts.layout đặt, không để Streamlit ghi đè (chế độ tối).
    st.plotly_chart(fig, width="stretch", theme=None, config=charts.png_config(code), key=key)


def method_html(cfg: dict, fees: dict, vecm: dict, manifest, t4=None) -> str:
    """Trang phương pháp: các thẻ mô tả dữ liệu, mô hình, chi phí, kiểm định. Thông số đọc từ config và file kết quả."""
    g = cfg.get("gap_events", {}) or {}
    ba = cfg.get("backtest_a", {}) or {}
    bb = cfg.get("backtest_b", {}) or {}
    roll = cfg.get("rolling", {}) or {}
    se_lines = regression_se_lines(t4)
    sample = cfg.get("sample", {}) or {}
    split = cfg.get("split_ratios", [])
    fee_txt = "; ".join(f"{EXCHANGE_NAME.get(ex, ex)} {fmt.num(v.get('spot', {}).get('taker_bps', np.nan), 1)} / "
                        f"{fmt.num(v.get('perp', {}).get('taker_bps', np.nan), 1)} bps"
                        for ex, v in fees.items() if isinstance(v, dict) and "spot" in v)
    def card(title, items):
        return f"<div class='card'><h5>{escape(title)}</h5><ul>" + "".join(f"<li>{escape(i)}</li>" for i in items) + "</ul></div>"
    split_txt = (f"Chia theo thời gian {' / '.join(fmt.num(x * 100) + '%' for x in split)}: tập phát triển, tập xác thực, "
                 "tập kiểm tra cuối (chỉ mở một lần).") if split else "Chia mẫu theo thời gian."
    if manifest:
        split_txt += (f" Phát triển {_fmt_day(manifest['dev_start'])} → {_fmt_day(manifest['dev_end'])}; xác thực đến "
                      f"{_fmt_day(manifest['val_end'])}; kiểm tra cuối đến {_fmt_day(manifest['test_end'])}.")
    cards = [
        card("1. Dữ liệu", [
            f"BTC/USDT trên {len(cfg.get('venues', []))} thị trường: Binance, OKX, Bybit × spot và Perpetual futures.",
            f"Lịch sử: nến {cfg.get('timeframe', '1min').replace('min', ' phút')}, từ {sample.get('start', '?')[:10]} đến trước "
            f"{sample.get('end', '?')[:10]} (UTC). Binance spot tải từ data.binance.vision; các thị trường khác qua API "
            "công khai (thư viện CCXT).",
            "Live: ảnh chụp sổ lệnh (giá mua, giá bán, độ sâu) mỗi 5 giây, 17–23/10/2026; là một mẫu riêng, không trộn với "
            "dữ liệu lịch sử.",
            f"Đồng bộ thời gian theo thời điểm dữ liệu sẵn có (ts_available); giá cũ hơn {cfg.get('max_age', '5min').replace('min', ' phút')} "
            "coi là thiếu, không nội suy.",
            split_txt]),
        card("2. Sàn nào dẫn giá (RQ1–RQ3)", [
            f"Mô hình VECM: hạng đồng liên kết {vecm.get('coint_rank', '?')}, số trễ {vecm.get('k_ar_diff', '?')} "
            f"(chọn theo {str(vecm.get('criterion', '?')).upper()}).",
            "Information Share (Hasbrouck): đóng góp của từng sàn vào phương sai giá hiệu quả; tính cận dưới–cận trên qua "
            "mọi thứ tự xếp các sàn.",
            "Component Share (Gonzalo–Granger) và ILS (kết hợp IS và CS) để đối chiếu.",
            f"Cửa sổ trượt {fmt.num(roll.get('window_obs', 0))} quan sát (1 ngày), bước {fmt.num(roll.get('step_obs', 0))} "
            "(1 giờ). Một sàn chỉ được gọi là dẫn giá khi cận dưới của nó lớn hơn cận trên của mọi sàn khác.",
            f"Lead–lag: tương quan lợi suất ở độ trễ −{cfg.get('max_lag', 5)}…+{cfg.get('max_lag', 5)} phút."]),
        card("3. Yếu tố quyết định (RQ2)", [
            "Hồi quy IS điểm giữa theo thị phần khối lượng, biến động thực hiện, chênh lệch mua–bán, độ sâu và biến "
            "là Perpetual futures.",
            *se_lines,
            "Đọc theo hướng và ý nghĩa thống kê, không đọc như quan hệ nhân quả."]),
        card("4. Chênh lệch giá và chi phí (RQ4–RQ5)", [
            f"Sự kiện chênh lệch: giá bán ở sàn đắt cao hơn giá mua ở sàn rẻ từ {fmt.num(g.get('gap_threshold_bps', np.nan), 1)} "
            "bps trở lên, chỉ ghép các sàn cùng loại hợp đồng.",
            f"Phí taker một chiều (spot / perp, bậc thấp nhất): {fee_txt}.",
            "Sau phí = trước phí − phí hai chân − nửa chênh lệch mua–bán − trượt giá − chi phí khác. Funding không tính.",
            f"Backtest A: giá đóng cửa 1 phút, khớp sau {ba.get('latency_min', '?')} phút, {fmt.num(ba.get('qty_btc', 0), 1)} BTC "
            "mỗi lệnh, vốn đặt sẵn ở mọi sàn.",
            f"Backtest B: giá mua/bán live, khớp sau {bb.get('latency_s', '?')} giây, yêu cầu đủ khối lượng ở mức giá tốt nhất.",
            "T5b: thay phí ×1 / ×0,5 / ×0, độ trễ và quy mô; backtest A chạy một lần trên tập kiểm tra cuối với cấu hình "
            "đã đóng băng."]),
        card("5. Giới hạn", [
            "Nến 1 phút quá thô để tách sàn dẫn giá khi các sàn phản ứng trong vài giây; mẫu live chỉ 6 ngày.",
            "Backtest A dùng giá đóng cửa làm đại diện, không có sổ lệnh lịch sử.",
            "Phí taker bậc thấp nhất; không tính chiết khấu VIP, phí rút/chuyển tiền và funding.",
            "Mô phỏng nghiên cứu, không phải khuyến nghị giao dịch."]),
    ]
    return f"<div class='method'>{''.join(cards)}</div>"


@st.cache_data(show_spinner=False)
def _load_project_config(mtime: float) -> tuple:
    """config.yaml, fees.yaml và đặc tả VECM (T2_vecm_spec.json); thiếu file thì trả dict rỗng."""
    import yaml
    def read_yaml(p):
        try:
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (OSError, ValueError, yaml.YAMLError):
            return {}
    try:
        vecm = json.loads((RESULTS_DEFAULT / "T2_vecm_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        vecm = {}
    return read_yaml(ROOT / "config/config.yaml"), read_yaml(ROOT / "config/fees.yaml"), vecm


# --------------------------------------------------------------------------- hình minh họa tự vẽ (SVG), bảng, nút chọn
ART = {"c1": "#3A6FD8", "c2": "#8a5cf0", "c3": "#2aa7d8"}     # đặt lại theo màu chủ đạo trong main()


def _svg_uri(svg: str) -> str:
    return "data:image/svg+xml," + quote(svg)


def art_svg(kind: str) -> str:
    """Hình minh họa cho dải tiêu đề mỗi trang, vẽ bằng SVG theo màu giao diện (không dùng ảnh ngoài)."""
    c1, c2, c3 = ART["c1"], ART["c2"], ART["c3"]
    defs = (f"<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{c1}'/>"
            f"<stop offset='1' stop-color='{c2}'/></linearGradient><filter id='f' x='-20%' y='-20%' width='140%' "
            "height='140%'><feGaussianBlur stdDeviation='5' result='b'/><feMerge><feMergeNode in='b'/>"
            "<feMergeNode in='SourceGraphic'/></feMerge></filter></defs>")
    def coin(x, y, r, op=1):
        return (f"<g opacity='{op}'><circle cx='{x}' cy='{y}' r='{r}' fill='{c1}' fill-opacity='.12' stroke='url(#g)' "
                f"stroke-width='{max(2, r / 14):.1f}' filter='url(#f)'/><circle cx='{x}' cy='{y}' r='{r * .8:.0f}' fill='none' "
                f"stroke='{c2}' stroke-opacity='.6' stroke-dasharray='3 5'/><text x='{x}' y='{y + r * .34:.0f}' "
                f"font-size='{r:.0f}' font-weight='800' text-anchor='middle' fill='url(#g)' font-family='Arial'>₿</text></g>")
    body = ""
    if kind == "home":
        body = (coin(470, 150, 82) + coin(575, 82, 44, .85) + coin(360, 235, 40, .8)
                + f"<polyline points='250,270 320,230 380,250 450,190 520,205 600,120' fill='none' stroke='{c3}' "
                  "stroke-width='4' stroke-linecap='round' stroke-linejoin='round' filter='url(#f)'/>")
    elif kind == "exchanges":
        for i, x in enumerate((330, 450, 570)):
            col = (c1, c3, c2)[i]
            body += (f"<g filter='url(#f)'><polygon points='{x - 52},120 {x},84 {x + 52},120' fill='{col}' fill-opacity='.25' "
                     f"stroke='{col}' stroke-width='3'/><rect x='{x - 46}' y='124' width='92' height='10' rx='3' fill='{col}'/>"
                     + "".join(f"<rect x='{x - 40 + k * 22}' y='140' width='12' height='92' rx='3' fill='{col}' "
                               "fill-opacity='.75'/>" for k in range(4))
                     + f"<rect x='{x - 52}' y='236' width='104' height='12' rx='3' fill='{col}'/></g>")
        body += coin(450, 50, 26)
    elif kind == "compare":
        for x, h, col, n in ((380, 120, c3, "2"), (470, 170, c1, "1"), (560, 90, c2, "3")):
            body += (f"<rect x='{x - 38}' y='{270 - h}' width='76' height='{h}' rx='8' fill='{col}' fill-opacity='.8' "
                     f"filter='url(#f)'/><text x='{x}' y='{262 - h}' font-size='30' font-weight='800' text-anchor='middle' "
                     f"fill='{col}' font-family='Arial'>#{n}</text>")
        body += coin(600, 70, 30, .9)
    elif kind == "price":
        candles = [(300, 150, 200, 1), (330, 170, 215, 0), (360, 140, 190, 1), (390, 120, 170, 1), (420, 135, 185, 0),
                   (450, 100, 150, 1), (480, 110, 165, 0), (510, 80, 130, 1), (540, 70, 118, 1), (570, 85, 140, 0)]
        for x, top, bot, up in candles:
            col = c1 if up else c2
            body += (f"<line x1='{x}' x2='{x}' y1='{top - 18}' y2='{bot + 18}' stroke='{col}' stroke-width='2'/>"
                     f"<rect x='{x - 9}' y='{top}' width='18' height='{bot - top}' rx='3' fill='{col}' "
                     f"fill-opacity='{.85 if up else .55}'/>")
        body += (f"<polyline points='280,210 330,195 380,170 430,160 480,135 530,110 600,95' fill='none' stroke='{c3}' "
                 "stroke-width='3' filter='url(#f)'/>")
    elif kind == "lead":
        nodes = [(470, 160), (380, 90), (580, 80), (600, 220), (380, 240), (470, 270), (300, 170)]
        for x, y in nodes[1:]:
            body += f"<line x1='470' y1='160' x2='{x}' y2='{y}' stroke='url(#g)' stroke-width='2.5' stroke-opacity='.8'/>"
        for i, (x, y) in enumerate(nodes):
            r = 30 if i == 0 else 16
            body += (f"<circle cx='{x}' cy='{y}' r='{r}' fill='{(c1, c3, c2)[i % 3]}' fill-opacity='{.9 if i == 0 else .7}' "
                     "filter='url(#f)'/>")
        body += "<text x='470' y='171' font-size='30' font-weight='800' text-anchor='middle' fill='#fff' font-family='Arial'>?</text>"
    elif kind == "gap":
        body = (f"<path d='M270,200 C340,190 380,120 450,120 S560,170 620,150' fill='none' stroke='{c1}' stroke-width='4' "
                "filter='url(#f)'/>"
                f"<path d='M270,210 C340,205 380,175 450,180 S560,180 620,158' fill='none' stroke='{c2}' stroke-width='4' "
                "stroke-dasharray='10 7' filter='url(#f)'/>"
                f"<path d='M380,150 C410,125 430,120 450,120 L450,180 C420,178 400,172 380,170 Z' fill='{c3}' fill-opacity='.25'/>"
                f"<line x1='450' y1='128' x2='450' y2='172' stroke='{c3}' stroke-width='3' marker-end='url(#a)' "
                "marker-start='url(#a)'/>"
                f"<defs><marker id='a' viewBox='0 0 10 10' refX='5' refY='5' markerWidth='5' markerHeight='5' "
                f"orient='auto-start-reverse'><path d='M0,0 L10,5 L0,10 z' fill='{c3}'/></marker></defs>"
                + coin(330, 90, 30, .85) + coin(580, 250, 30, .85))
    elif kind == "method":
        for i, (x, label) in enumerate(((320, "DATA"), (460, "VECM"), (600, "T5"))):
            col = (c3, c1, c2)[i]
            body += (f"<rect x='{x - 55}' y='120' width='110' height='80' rx='16' fill='{col}' fill-opacity='.18' "
                     f"stroke='{col}' stroke-width='3' filter='url(#f)'/><text x='{x}' y='170' font-size='22' "
                     f"font-weight='800' text-anchor='middle' fill='{col}' font-family='Arial'>{label}</text>")
            if i < 2:
                body += (f"<line x1='{x + 60}' y1='160' x2='{x + 80}' y2='160' stroke='{col}' stroke-width='4' "
                         "stroke-linecap='round'/>")
        body += coin(460, 60, 26, .9)
    else:  # download
        for r_ in range(4):
            for k in range(3):
                body += (f"<rect x='{360 + k * 70}' y='{90 + r_ * 34}' width='62' height='26' rx='5' fill='{c1}' "
                         f"fill-opacity='{.18 + .12 * (r_ == 0)}' stroke='{c1}' stroke-opacity='.5'/>")
        body += (f"<line x1='600' y1='100' x2='600' y2='200' stroke='{c2}' stroke-width='8' stroke-linecap='round' "
                 f"filter='url(#f)'/><polyline points='570,175 600,210 630,175' fill='none' stroke='{c2}' stroke-width='8' "
                 "stroke-linecap='round' stroke-linejoin='round'/>"
                 f"<rect x='560' y='225' width='80' height='10' rx='5' fill='{c2}'/>")
    return _svg_uri(f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 320'>{defs}{body}</svg>")


PAGE_ART = {"Tổng quan": "home", "Các sàn giao dịch": "exchanges", "So sánh các sàn": "compare",
            "Giá & độ phân tán": "price", "Sàn dẫn giá": "lead", "Chênh lệch giá & lợi nhuận": "gap",
            "Phương pháp & dữ liệu": "method", "Tải dữ liệu": "download"}


def cover_svg(ex: str) -> str:
    """Ảnh bìa tự vẽ cho từng sàn: nền theo màu nhận diện của sàn, đồng bitcoin, nến giá và lưới."""
    bg1, bg2, acc = {"binance": ("#1e2026", "#3a3214", "#F0B90B"), "okx": ("#050505", "#2b2b2b", "#f5f5f5"),
                     "bybit": ("#15161c", "#3b2a10", "#F7A600")}[ex]
    candles = "".join(
        f"<line x1='{x}' x2='{x}' y1='{t - 10}' y2='{b + 10}' stroke='{acc}' stroke-opacity='.6' stroke-width='2'/>"
        f"<rect x='{x - 7}' y='{t}' width='14' height='{b - t}' rx='2' fill='{acc}' fill-opacity='{o}'/>"
        for x, t, b, o in ((250, 120, 155, .8), (276, 110, 140, .45), (302, 90, 130, .8), (328, 100, 125, .45),
                           (354, 70, 110, .8), (380, 60, 100, .8), (406, 70, 90, .45), (432, 42, 85, .8)))
    grid = "".join(f"<line x1='0' x2='640' y1='{y}' y2='{y}' stroke='{acc}' stroke-opacity='.07'/>" for y in range(30, 260, 40))
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 640 260' preserveAspectRatio='xMidYMid slice'>"
           f"<defs><linearGradient id='bg' x1='0' y1='0' x2='1' y2='1'><stop offset='0' stop-color='{bg1}'/>"
           f"<stop offset='1' stop-color='{bg2}'/></linearGradient><radialGradient id='glow'><stop offset='0' "
           f"stop-color='{acc}' stop-opacity='.45'/><stop offset='1' stop-color='{acc}' stop-opacity='0'/></radialGradient>"
           f"</defs><rect width='640' height='260' fill='url(#bg)'/>{grid}<circle cx='500' cy='110' r='150' fill='url(#glow)'/>"
           f"<circle cx='500' cy='110' r='78' fill='none' stroke='{acc}' stroke-width='6'/>"
           f"<circle cx='500' cy='110' r='62' fill='none' stroke='{acc}' stroke-opacity='.5' stroke-dasharray='4 6'/>"
           f"<text x='500' y='138' font-size='78' font-weight='800' text-anchor='middle' fill='{acc}' "
           f"font-family='Arial'>₿</text>{candles}</svg>")
    return _svg_uri(svg)


def html_table(df: pd.DataFrame, right=(), helps: dict | None = None, labels: dict | None = None) -> str:
    """Bảng HTML gọn (không kẻ ô, tiêu đề mờ, số căn phải). Ô 'Thiếu' tô đỏ, 'Đủ' tô xám."""
    helps, labels = helps or {}, labels or {}
    head = "".join(f"<th class='{'r' if c in right else ''}'" + (f" title='{escape(helps[c], quote=True)}'" if c in helps else "")
                   + f">{escape(str(labels.get(c, c)))}</th>" for c in df.columns)
    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in df.columns:
            v = r[c]
            if isinstance(v, pd.Timestamp):
                v = f"{v:%d/%m/%Y %H:%M:%S}" if v.second else f"{v:%d/%m/%Y %H:%M}"
            v = "–" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)
            cls = ("r " if c in right else "") + ("bad" if v == "Thiếu" else "muted" if v == "Đủ" else "")
            cells.append(f"<td class='{cls.strip()}'>{escape(v)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<div class='tbl-wrap'><table class='tbl'><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"


def show_df(df: pd.DataFrame, right=None):
    """Hiển thị một bảng đã định dạng sẵn (chữ). Cột căn phải: mặc định mọi cột trừ cột đầu."""
    right = list(df.columns[1:]) if right is None else right
    st.markdown(html_table(df.reset_index(drop=True), right), unsafe_allow_html=True)


def choose(label: str, options: list, key: str, format_func=None, default=None):
    """Nút chọn dạng viên thuốc (segmented control), luôn có một lựa chọn."""
    default = options[0] if default is None else default
    value = st.segmented_control(label, options, default=default, key=key, required=True,
                                 format_func=format_func or str)
    return default if value is None else value


FIG_DIR = ROOT / "outputs/figures"
REPORT_FIGS = {"F7": ("F7_spot_perp_is_ils.png", "Spot và perp trong cùng sàn: IS và ILS")}   # chỉ dùng khi chưa có T3c


F7_HOW = """<div class='guide'><div class='g-title'>Cách đọc hình</div><ol>
<li><b>Hàng trên – khoảng IS.</b> Mỗi thanh là khoảng Information Share của Spot hoặc Perp, từ cận dưới đến cận trên.
Thanh càng dài thì càng không chắc mỗi bên đóng góp bao nhiêu; hai thanh chồng nhau nghĩa là chưa đủ bằng chứng để
nói bên nào đóng góp nhiều hơn.</li>
<li><b>Hàng dưới – ILS.</b> Chia 1 giữa Spot và Perp của cùng sàn. Cột nào vượt đường chấm 0,5 thì bên đó
<i>có vẻ</i> dẫn theo ILS. Dòng chữ trên mỗi ô cho biết ILS của Spot đổi từ tập phát triển sang tập xác thực ra sao.</li>
"""


def f7_guide(t3c) -> str:
    """Khung hướng dẫn đọc F7. Phần 'Kết quả' được rút từ bảng T3c (không gõ tay); không có bảng thì chỉ giải thích cách đọc."""
    if t3c is None:
        return F7_HOW + "</ol></div>"
    f = charts.t3c_findings(t3c)
    name = lambda e: EXCHANGE_NAME.get(e, e)
    side = lambda x: "Spot" if x == "spot" else "Perp"
    parts = []
    parts.append("khoảng IS của Spot và Perp chồng nhau ở mọi sàn và cả hai tập" if f["overlap_all"]
                 else "khoảng IS của Spot và Perp không chồng nhau ở một số sàn")
    if f["flips"]:
        parts.append("ILS đổi phía giữa tập phát triển và tập xác thực ở " + ", ".join(name(e) for e, _ in f["flips"]))
    if f["steady"]:
        parts.append(", ".join(f"{name(e)} nghiêng về {side(x)} ở cả hai tập" for e, x in f["steady"]))
    take = ("với dữ liệu 1 phút, <b>chưa phân biệt được</b> Spot hay Perpetual futures dẫn giá."
            if f["overlap_all"] else "khoảng IS không chồng nhau ở một số sàn, xem bảng để biết sàn nào.")
    text = "; ".join(parts)
    return (F7_HOW + f"<li><b>Kết quả.</b> {escape(text[:1].upper() + text[1:])}.</li></ol>"
            f"<div class='g-take'>Kết luận cho RQ3: {take}</div></div>")


@st.cache_resource(show_spinner=False)
def figure_uri(path: str, mtime: float) -> str:
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()


def figure_html(path: Path, alt: str) -> str:
    """Ảnh hình tĩnh của nhóm trong khung bo góc; ở chế độ tối được đảo màu cho hợp nền tối (CSS .figpng)."""
    return (f"<div class='figpng'><img src='{figure_uri(str(path), _mtime(str(path)))}' alt='{escape(alt, quote=True)}'/>"
            "</div>")


# --------------------------------------------------------------------------- các trang
def page_home(c):
    st.markdown(hero_html(), unsafe_allow_html=True)
    st.markdown(cards_html(home_cards(c.panel, c.venues, c.d6, c.d7, c.res)), unsafe_allow_html=True)
    t5 = c.res.get("t5")
    wf = perp_waterfall_row(t5) if t5 is not None else None
    left, right = st.columns([3, 2], gap="large") if wf is not None else (st.container(), None)
    with left:
        st.markdown("#### Năm câu hỏi nghiên cứu")
        st.markdown(rq_html(), unsafe_allow_html=True)
    if right is not None:
        with right.container(border=True):
            st.markdown("**Vì sao không còn lãi?**")
            plot(charts.waterfall_fig(cost_waterfall(wf), c.t, height=290, compact=True), "T5", "home_wf")
            st.caption("Perp, backtest A: chênh lệch lúc ra quyết định trừ phí và giá biến động trong lúc chờ khớp "
                       "ra lỗ ròng (bps mỗi lệnh).")
    rq2 = ("RQ2 nằm ở trang Sàn dẫn giá." if c.res.get("t4") is not None
           else "RQ2 có trong báo cáo, chưa có trên dashboard.")
    st.caption(f"Nguồn: dữ liệu {KIND_NAME.get(c.kind, c.kind)} · {len(c.venues)} thị trường · quan sát cuối "
               f"{c.asof:%d/%m/%Y %H:%M} UTC. {rq2} Giá USDT, chênh lệch tính bằng bps (1 bps = 0,01%), thời gian UTC.")


def page_exchanges(c):
    st.markdown(page_header("Các sàn giao dịch", "Ba sàn trong nghiên cứu",
                            "Chọn một sàn để xem giới thiệu, số liệu Spot và Perpetual futures, và giá của sàn đó.",
                            "Binance · OKX · Bybit"), unsafe_allow_html=True)
    t1, t0 = c.res.get("t1"), c.res.get("t0")
    ex = st.segmented_control("Chọn sàn", list(EXCHANGES), format_func=lambda e: EXCHANGES[e]["name"], key="ex")
    if ex is None:
        cards = "".join(
            f"<div class='ex-card pick'><div class='cover' style=\"background-image:url({cover_svg(e)})\">"
            f"<span class='tag'>Từ {info['since']}</span><div class='name'>{escape(info['name'])}</div></div>"
            f"<div class='blurb'>{escape(info['intro'])}</div></div>" for e, info in EXCHANGES.items())
        st.markdown(f"<div class='ex-grid'>{cards}</div>", unsafe_allow_html=True)
        st.caption("Bấm tên sàn ở nút phía trên để mở trang của sàn đó. Ảnh bìa là hình minh họa tự vẽ theo màu "
                   "nhận diện của sàn, không phải logo.")
        return
    info = EXCHANGES[ex]
    st.markdown(f"<div class='ex-hero' style=\"background-image:url({cover_svg(ex)})\"><div class='body'>"
                f"<div class='eyebrow'>Thành lập {info['since']} · Spot và Perpetual futures</div>"
                f"<h2>{escape(info['name'])}</h2><p>{escape(info['intro'])}</p></div></div>", unsafe_allow_html=True)
    st.caption("Phần giới thiệu là thông tin chung để tham khảo; mọi con số bên dưới đọc từ dữ liệu của nhóm.")
    if t1 is not None:
        t1i = t1.set_index("venue")
        get = lambda m, col: t1i.loc[f"{ex}_{m}", col] if f"{ex}_{m}" in t1i.index else np.nan
        k = st.columns(4)
        k[0].metric("KL TB/ngày · Spot (USDT)", fmt.usdt_big(get("spot", "avg_daily_volume_usdt")))
        k[1].metric("KL TB/ngày · Perpetual (USDT)", fmt.usdt_big(get("perp", "avg_daily_volume_usdt")))
        k[2].metric("Phí taker Spot / Perp (bps)", f"{fmt.num(get('spot', 'fee_taker_bps'), 1)} / "
                                                     f"{fmt.num(get('perp', 'fee_taker_bps'), 1)}")
        k[3].metric("Funding TB mỗi kỳ (perp)", f"{fmt.bps(get('perp', 'avg_funding_rate_bps'))} bps")
    else:
        st.info("Chưa có dữ liệu: cần file T1_exchange_characteristics.csv trong outputs/tables.")
    st.markdown("#### Hồ sơ số liệu")
    show_df(exchange_rows(ex, t1, c.live, c.d6, c.d7, t0, c.quotes))
    st.caption("Khối lượng: tập phát triển và xác thực (T1). Chênh lệch mua–bán và độ sâu: mẫu live 5 giây. "
               "IS điểm giữa trung bình chỉ để tham khảo: các khoảng IS chồng nhau nên không xếp hạng dẫn giá được.")
    cols = [f"close__{ex}_{m}" for m in ("spot", "perp") if f"close__{ex}_{m}" in c.panel.columns]
    if cols:
        with st.container(border=True):
            st.markdown(f"#### Giá {info['name']} 30 ngày cuối của mẫu (theo giờ)")
            recent = c.panel.loc[c.panel.index > c.panel.index[-1] - pd.Timedelta(days=30), cols]
            hourly = recent.resample("1h").last().set_axis([x.rsplit("_", 1)[1] for x in cols], axis=1)
            plot(charts.exchange_price_fig(hourly, c.mode, c.t), "PX", "ex_price")
            st.caption("Giá đóng cửa của phút cuối mỗi giờ. Perp thường thấp hơn Spot vài bps (basis), đó là chênh "
                       "lệch khác loại hợp đồng, không phải cơ hội arbitrage.")


def page_compare(c):
    st.markdown(page_header("So sánh các sàn", "So sánh 6 thị trường",
                            "Khối lượng, thị phần, chi phí giao dịch và giá của từng thị trường so với trung bình các "
                            "thị trường cùng loại.", "Giá · khối lượng · xếp hạng"), unsafe_allow_html=True)
    t1, t0 = c.res.get("t1"), c.res.get("t0")
    if t1 is None:
        st.info("Chưa có dữ liệu: cần file T1_exchange_characteristics.csv trong outputs/tables.")
    else:
        rt = rank_table(t1, c.d6, t0, c.live)
        shown = pd.DataFrame({
            "Thị trường": rt["venue"].map(venue_label),
            "KL/ngày (USDT)": rt["avg_daily_volume_usdt"].map(fmt.usdt_big),
            "Hạng KL": rt["r_vol"].map(fmt.rank),
            "Mua–bán (bps)": rt["spread_median_bps"].map(lambda v: fmt.num(v, 4)),
            "Hạng mua–bán": rt["r_spread"].map(fmt.rank),
            "Phí (bps)": rt["fee_taker_bps"].map(lambda v: fmt.num(v, 1)),
            "Hạng phí": rt["r_fee"].map(fmt.rank)})
        if "r_influence" in rt:
            shown["Hạng IS"] = rt["r_is"].map(fmt.rank)
            shown["Hạng ILS"] = rt["r_ils"].map(fmt.rank)
            shown["Hạng ảnh hưởng"] = rt["r_influence"].map(fmt.rank)
        st.subheader("Xếp hạng")
        show_df(shown)
        src = rt.attrs.get("spread_source")
        note = ""
        if src == "D2" and c.live is not None:
            note = (f" Chênh lệch mua–bán là trung vị tính trực tiếp từ báo giá live (D2) đến "
                    f"{c.live['ts_recv_utc'].max():%d/%m/%Y %H:%M} UTC")
            t1_end = pd.to_datetime(t1["live_last_utc"], utc=True, errors="coerce").max() if "live_last_utc" in t1 else pd.NaT
            if pd.notna(t1_end) and t1_end < c.live["ts_recv_utc"].max():
                note += f"; bảng T1 chỉ đến {t1_end:%d/%m/%Y}, nên hạng có thể khác bảng T1"
            note += ". Chênh lệch giữa các thị trường rất nhỏ (dưới 0,001 bps) nên hạng mua–bán gần như hòa."
        elif src == "T1":
            note = " Chênh lệch mua–bán lấy từ bảng T1 vì chưa có báo giá live (D2)."
        st.caption("Hạng 1 = tốt nhất theo từng tiêu chí: khối lượng cao nhất; chênh lệch mua–bán và phí thấp nhất; "
                   "IS và ILS cao nhất." + note)
        if "r_influence" in rt:
            st.caption("Hạng ảnh hưởng (tham khảo) = hạng của trung bình hai hạng IS và ILS, tính trên mọi cửa sổ rolling của "
                       "D6: đo mức thị trường đóng góp vào giá chung, tức ảnh hưởng lên các sàn khác. Chỉ để tham khảo: "
                       "các khoảng IS chồng nhau nên chưa thể kết luận thị trường nào dẫn giá.")
        left, right = st.columns(2, gap="large")
        with left.container(border=True):
            st.markdown("#### Khối lượng giao dịch")
            plot(charts.volume_bar_fig(t1, c.mode, c.t), "VOL", "vol_bar")
            st.caption("Perp có khối lượng gấp nhiều lần spot trên cả ba sàn.")
        with right.container(border=True):
            st.markdown("#### Thị phần khối lượng theo ngày")
            daily = daily_volume(c.panel, c.venues)
            if daily.empty:
                st.write("Bảng giá không có cột khối lượng (volume_base__…).")
            else:
                plot(charts.volume_share_fig(daily, c.mode, c.t), "SHARE", "vol_share")
                st.caption("Từ khối lượng BTC của nến 1 phút, cộng theo ngày; cả mẫu lịch sử.")
    st.subheader("Giá từng thị trường so với trung bình cùng loại")
    choice = choose("Khoảng thời gian", ["7 ngày", "30 ngày", "Toàn bộ"], key="prem_range")
    mask = range_mask(c.panel.index, choice)
    premiums = {}
    for m in ("spot", "perp"):
        p = premium_bps(c.panel.loc[mask], c.venues, m)
        if p is not None:
            premiums[m] = p.resample("1h").median()
    if not premiums:
        st.write("Cần ít nhất hai thị trường cùng loại.")
    else:
        with st.container(border=True):
            plot(charts.premium_fig(premiums, c.mode, c.t), "PREM", "premium")
            st.caption("Trung vị theo giờ của (giá thị trường − trung bình các thị trường cùng loại) / trung bình, "
                       "tính bằng bps. Đường trên 0 là sàn đó thường đắt hơn các sàn còn lại.")


def page_price(c):
    st.markdown(page_header("Giá & độ phân tán", "Giá và độ phân tán giá",
                            "Giá BTC trên từng thị trường và mức các sàn cùng loại lệch giá nhau theo thời gian.",
                            "Dữ liệu lịch sử 1 phút và live 5 giây"), unsafe_allow_html=True)
    kind, asof, panel, quotes, live = c.kind, c.asof, c.panel, c.quotes, c.live
    st.subheader("Giá BTC trên từng thị trường")
    with st.container(border=True):
        st.markdown("#### Giá đồng bộ của 6 thị trường (F1)")
        span = choose("Khoảng thời gian", ["30 ngày", "90 ngày", "Toàn bộ"], key="f1_range")
        days = {"30 ngày": 30, "90 ngày": 90}.get(span)
        cols_px = [f"close__{v}" for v in c.venues]
        px = panel[cols_px] if days is None else panel.loc[panel.index > panel.index[-1] - pd.Timedelta(days=days), cols_px]
        plot(charts.prices_fig(px.resample("1h").last().set_axis(c.venues, axis=1), c.mode, c.t), "F1", "f1")
        st.caption("Giá đóng cửa của phút cuối mỗi giờ. Sáu đường gần như trùng nhau: chênh lệch giữa các sàn chỉ vài "
                   "bps, nhỏ hơn rất nhiều so với biến động giá.")
    with st.container(border=True):
        st.markdown(badge(f"Dữ liệu {KIND_NAME.get(kind, kind)} · nến 1 phút", "hist")
                    + badge(f"Phút cuối: {asof:%d/%m/%Y %H:%M} UTC"), unsafe_allow_html=True)
        pr = price_ranges(quotes)
        c1, c2, c3 = st.columns(3)
        c1.metric("Spot: cao nhất − thấp nhất", f"{fmt.bps(pr['spot'])} bps",
                  help="Chỉ so các sàn spot với nhau, ở phút cuối.")
        c2.metric("Perp: cao nhất − thấp nhất", f"{fmt.bps(pr['perp'])} bps",
                  help="Chỉ so các Perpetual futures với nhau, ở phút cuối.")
        c3.metric(f"Basis perp − spot (TB {pr['basis_n']} sàn)", f"{charts.signed(pr['basis'])} bps",
                  help="Chênh lệch giữa hai loại hợp đồng khác nhau: KHÔNG phải cơ hội arbitrage.")
        st.caption("Hai ô đầu so cùng loại hợp đồng. Ô thứ ba là basis perp − spot, không phải cơ hội arbitrage.")
        table(quotes, QUOTE_COLS)
        explain(QUOTE_COLS)
    with st.container(border=True):
        st.markdown("#### Báo giá live gần nhất")
        if live is None:
            st.write(c.live_msg)
        else:
            st.markdown(badge("Dữ liệu live · bid/ask mỗi 5 giây", "live")
                        + badge(f"{live['ts_grid_utc'].min():%d/%m/%Y %H:%M} → "
                                f"{live['ts_grid_utc'].max():%d/%m/%Y %H:%M} UTC")
                        + badge(f"{fmt.num(len(live))} báo giá"), unsafe_allow_html=True)
            latest = live_latest(live)
            shown = ["venue", "ts_recv_utc", "bid", "ask", "spread_bps", "depth_bid_10bps_usdt", "depth_ask_10bps_usdt"]
            table(latest, shown)
            med = ", ".join(f"{venue_name(v)} {fmt.bps(m)}" for v, m in zip(latest["venue"], latest["spread_bps_median"]))
            st.caption(f"Báo giá cuối của mỗi thị trường theo giờ nhận, đến {latest['ts_recv_utc'].max():%d/%m/%Y %H:%M:%S} UTC. "
                       "Dấu ≥ ở độ sâu: sổ lệnh quan sát bị cắt nên chỉ là cận dưới. "
                       f"Chênh lệch mua–bán trung vị cả mẫu (bps): {med}. Mẫu live là giai đoạn KHÁC dữ liệu lịch sử "
                       "ở trên, nên giá hai bảng khác nhau là bình thường.")
            explain(shown + ["depth_truncated", "spread_bps_median"])

    st.subheader("Độ phân tán giá: các sàn cùng loại lệch nhau bao nhiêu?")
    st.caption("Mỗi phút: (giá cao nhất − giá thấp nhất) / giá trung bình giữa các sàn cùng loại, tính bằng bps "
               "(1 bps = 0,01%). Số càng lớn thì các sàn càng lệch giá nhau.")
    choice = choose("Khoảng thời gian", list(RANGES) + ["Tùy chọn"], key="range", default="7 ngày")
    custom = None
    if choice == "Tùy chọn":
        first, last_day = panel.index[0].date(), panel.index[-1].date()
        custom = st.date_input("Từ ngày đến ngày", (first, last_day), min_value=first, max_value=last_day, key="drange")
    mask = range_mask(panel.index, choice, custom)
    raw = {m: ser[mask] for m, ser in c.dispersions.items() if ser is not None}
    raw = {m: r for m, r in raw.items() if r.notna().any()}
    if not raw:
        st.write("Không có dữ liệu độ phân tán giá trong khoảng này (cần ít nhất hai sàn cùng loại).")
        return
    cols = st.columns(2 * len(raw))
    for i, (market, r) in enumerate(raw.items()):
        valid = r.dropna()
        cols[2 * i].metric(f"{market} · trung vị", f"{fmt.bps(valid.median())} bps",
                           help=f"{MARKET_NAME[market]}: trung vị độ phân tán giá trong khoảng đang chọn.")
        cols[2 * i + 1].metric(f"{market} · phút cuối", f"{fmt.bps(valid.iloc[-1])} bps",
                               help=f"{MARKET_NAME[market]}: phút cuối có đủ giá, {valid.index[-1]:%d/%m/%Y %H:%M} UTC.")
    with st.container(border=True):
        minute_view = st.toggle("Xem dữ liệu 1 phút", key="raw1m") if choice in ("1 ngày", "7 ngày") else False
        if minute_view:
            plot(charts.dispersion_fig({m: thin(r) for m, r in raw.items()}, c.mode, c.t, raw=True), "F2", "disp")
            st.caption("Dữ liệu 1 phút; chỗ thiếu giá để ngắt đường, không nối thay. Kéo để phóng to, bấm đúp để đặt lại.")
        else:
            plot(charts.dispersion_fig({m: charts.hourly_band(r) for m, r in raw.items()}, c.mode, c.t), "F2", "disp")
            st.caption("Đường đậm là trung vị theo giờ; dải mờ là khoảng từ phân vị 10% đến 90% trong giờ đó. "
                       "Hai khung dùng chung trục tung.")
    with st.expander("Cách tính độ phân tán giá"):
        st.markdown("Tại mỗi phút, lấy giá cao nhất trừ giá thấp nhất giữa các sàn **cùng loại** (spot với "
                    "spot, perp với perp), chia cho giá trung bình rồi nhân 10.000 để ra bps. "
                    "Phút nào thiếu giá của một sàn trong nhóm thì để trống, không điền thay.")


def page_lead(c):
    st.markdown(page_header("Sàn dẫn giá", "Sàn nào dẫn dắt giá Bitcoin?",
                            "Information Share (IS), Component Share (CS) và ILS trên cửa sổ trượt 1 ngày; tương quan "
                            "lợi suất giữa spot và perp; các yếu tố ảnh hưởng đến vai trò dẫn giá.",
                            "RQ1 · RQ2 · RQ3"), unsafe_allow_html=True)
    d6, asof, mode, t, res = c.d6, c.asof, c.mode, c.t, c.res
    st.subheader("RQ1 · Sàn nào đóng góp nhiều nhất vào giá hiệu quả?")
    if d6 is None or d6.empty:
        st.write(c.d6_msg or "Không có cửa sổ rolling nào trong dữ liệu này.")
    else:
        counts, total = leader_counts(d6)
        decided = sum(counts.values())
        with st.container(border=True):
            st.markdown("#### " + ("Không cửa sổ nào xác định được sàn dẫn giá" if decided == 0 else
                                   f"{fmt.num(decided)} / {fmt.num(total)} cửa sổ xác định được sàn dẫn giá"))
            plot(charts.leader_share_fig(counts, total, mode, t), "RQ1", "leader_share")
            st.caption("Dẫn đầu xét theo quy tắc khoảng IS: cận dưới của sàn đó phải lớn hơn cận trên của mọi sàn "
                       "khác (không chồng nhau); không xét theo ILS > 0,5.")
        dates = sorted(d6["window_end"].dt.date.unique())
        day = (st.slider("Xem cửa sổ kết thúc ngày", min_value=dates[0], max_value=dates[-1], value=dates[-1],
                         format="DD/MM/YYYY", key="wdate") if len(dates) > 1 else dates[0])
        cutoff = min(pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1), asof)
        window = current_window(d6[d6["available_at"] <= cutoff], cutoff)
        if window is None:
            st.write("Chưa có cửa sổ rolling nào sẵn sàng tại thời điểm này.")
        else:
            leader, reason = leader_of(window)
            if leader:
                st.success(f"Sàn dẫn giá: {venue_name(leader)}")
            else:
                code = reason.split(":")[0].strip()
                st.warning(REASON_TEXT.get(code, reason) + f" ({code}).")
            c1, c2, c3 = st.columns(3)
            c1.metric("Số cửa sổ rolling (1 ngày, bước 1 giờ)", fmt.num(total))
            c2.metric("Cửa sổ xác định được sàn dẫn", fmt.num(decided))
            c3.metric("Cửa sổ đang xem kết thúc", f"{window['window_end'].iloc[0]:%d/%m/%Y}",
                      help=f"{window['window_end'].iloc[0]:%d/%m/%Y %H:%M} UTC; chỉ dùng kết quả đã sẵn sàng đến "
                           f"{cutoff:%d/%m/%Y %H:%M} UTC.")
            stale = asof - d6["window_end"].max()
            if stale > pd.Timedelta(days=1):
                st.info(f"Kết quả sàn dẫn giá có đến {d6['window_end'].max():%d/%m/%Y}, tức {stale.days} ngày "
                        "trước phút cuối của dữ liệu: chỉ tính trên tập phát triển và xác thực, tập kiểm tra cuối "
                        "không dùng ở bước này.")
        with st.container(border=True):
            st.markdown("#### IS điểm giữa của 6 thị trường theo thời gian")
            pick = st.selectbox("Vẽ dải cận dưới–cận trên cho", sorted(d6["venue"].unique()), format_func=venue_label,
                                key="isvenue")
            plot(charts.is_time_fig(d6, pick, mode, t), "IS", "is_time")
            st.caption("Mỗi đường là IS điểm giữa của một thị trường, trung bình theo ngày; màu theo sàn, nét liền là "
                       "spot, nét đứt là perp. Dải mờ là khoảng từ cận dưới đến cận trên của thị trường đang chọn: "
                       "rộng nghĩa là IS chưa xác định chính xác.")
        if window is not None:
            with st.container(border=True):
                st.markdown(f"#### Mức đóng góp trong cửa sổ {window['window_start'].iloc[0]:%d/%m/%Y %H:%M} → "
                            f"{window['window_end'].iloc[0]:%d/%m/%Y %H:%M} UTC")
                plot(charts.bounds_fig(window, mode, t), "F3", "bounds")
                with st.expander("Xem khoảng IS của từng thị trường theo thời gian (kiểu hình F3 trong báo cáo)"):
                    plot(charts.is_panels_fig(d6, mode, t, (c.manifest or {}).get("dev_end")), "F3b", "is_panels")
                    st.caption("Mỗi ô là một thị trường (hàng trên Spot, hàng dưới Perp). Dải màu là khoảng IS từ cận dưới đến cận trên, "
                               "đường là IS điểm giữa, đều trung bình theo ngày; đường chấm dọc là cuối tập phát triển (thời điểm "
                               f"khóa đặc tả). Độ rộng khoảng IS trung vị là {fmt.num((d6['is_upper'] - d6['is_lower']).median(), 2)} "
                   "(thang 0 đến 1): IS chưa xác định chính xác ở hầu hết thời điểm.")
                table(window.sort_values("is_mid", ascending=False), RANK_COLS)
                st.caption("Component Share (CS) có thể âm hoặc lớn hơn 1: đó là trọng số có dấu (tổng các sàn bằng 1), "
                           "không phải phần trăm. ILS ở đây là trung bình theo từng cặp sàn nên cộng lại không bằng 1.")
                explain(RANK_COLS)

    st.subheader("RQ3 · Spot hay perp dẫn giá?")
    xcorr, t3c = res.get("xcorr"), res.get("t3c")
    f7 = FIG_DIR / REPORT_FIGS["F7"][0]
    native = t3c is not None and all(col in t3c.columns for col in charts.T3C_REQUIRED)
    if native or f7.exists():
        with st.container(border=True):
            st.markdown("#### Trong cùng một sàn, Spot hay Perpetual futures đóng góp vào giá nhiều hơn?")
            if native:
                plot(charts.t3c_fig(t3c, mode, t), "F7", "t3c")
            else:
                st.markdown(figure_html(f7, "Hình F7 của nhóm: Spot và Perp trong cùng sàn, IS và ILS"),
                            unsafe_allow_html=True)
            st.markdown(f7_guide(t3c if native else None), unsafe_allow_html=True)
            st.caption("Nguồn: " + ("bảng T3c_spot_perp.csv (mô hình VECM hai biến cho từng sàn, tập phát triển và tập xác thực)."
                                     if native else f"outputs/figures/{f7.name} (hình tĩnh của nhóm). Khi có file "
                                     "T3c_spot_perp.csv trong outputs/tables, hình này được vẽ lại theo màu dashboard."))
    else:
        st.info(f"Chưa có dữ liệu: cần file {T3C_GLOB} (bảng T3c: IS, CS, ILS của 3 cặp Spot – Perp cùng sàn) "
                "trong outputs/tables.")
    if xcorr is None:
        st.info(f"Chưa có dữ liệu: cần file {LEADLAG_FILE} (tương quan lợi suất theo độ trễ) trong outputs/tables.")
    else:
        with st.container(border=True):
            plot(charts.leadlag_fig(xcorr, mode, t), "F6", "leadlag")
            with st.expander("Xem đủ 9 cặp thị trường (kiểu hình F6 trong báo cáo)"):
                plot(charts.leadlag_panels_fig(xcorr, mode, t), "F6b", "leadlag_panels")
                st.caption("Cột là tương quan lợi suất 1 phút ở độ trễ −5…+5 phút. Độ trễ dương: thị trường đứng trước đi trước "
                           "thị trường đứng sau. Độ trễ 0 không vẽ; ρ(0) ghi trong từng ô. Mọi cặp có ρ(0) gần 1 và các cột "
                           "còn lại gần 0, không nghiêng hẳn về phía nào.")
            same = xcorr["venue_a"].str.rsplit("_", n=1).str[0] == xcorr["venue_b"].str.rsplit("_", n=1).str[0]
            zero = xcorr[(xcorr["lag"] == 0) & xcorr["venue_a"].str.endswith("_perp")
                         & xcorr["venue_b"].str.endswith("_spot") & same]
            parts = ", ".join(f"{EXCHANGE_NAME.get(a.rsplit('_', 1)[0], a)} {fmt.num(v, 3)}"
                              for a, v in zip(zero["venue_a"], zero["correlation"]))
            pairs = xcorr[same & xcorr["venue_a"].str.endswith("_perp") & xcorr["venue_b"].str.endswith("_spot")]
            other = pairs.loc[pairs["lag"] != 0, "correlation"].abs().max()
            st.markdown(
                "<div class='guide'><div class='g-title'>Cách đọc hình</div><ol>"
                "<li><b>Trục ngang</b> là độ trễ tính bằng phút. Độ trễ dương: lợi suất Perp phút trước đi cùng lợi suất "
                "Spot phút sau, tức Perp <i>đi trước</i>. Độ trễ âm: Spot đi trước.</li>"
                "<li><b>Trục dọc</b> là tương quan lợi suất. Bên nào đi trước rõ ràng thì sẽ có một cột cao nổi bật "
                "về một phía.</li>"
                f"<li><b>Kết quả.</b> Ở độ trễ 0, tương quan là {escape(parts)}: hai thị trường chạy gần như cùng lúc "
                f"(độ trễ 0 không vẽ để khỏi lấn át các điểm còn lại). Ở các độ trễ khác, tương quan chỉ tối đa "
                f"{fmt.num(other, 3)} về độ lớn, gần như bằng 0 và không nghiêng hẳn về phía nào.</li></ol>"
                "<div class='g-take'>Ở khung 1 phút, Spot và Perp phản ứng <b>cùng lúc</b>; không thấy bên nào đi trước "
                "bên kia.</div></div>", unsafe_allow_html=True)

    st.subheader("RQ2 · Vai trò dẫn giá có phụ thuộc khối lượng, chênh lệch mua–bán, độ sâu không?")
    t4 = res.get("t4")
    if t4 is None:
        st.info(f"Chưa có dữ liệu: cần file {REGRESSION_FILE} (hồi quy của IS theo các yếu tố) trong outputs/tables.")
        return
    for model, title in (("A", "Mẫu lịch sử (1 phút)"), ("B", "Mẫu live (5 giây)")):
        if (t4["model"] == model).any():
            with st.container(border=True):
                st.markdown(f"**{title}**")
                show_df(regression_table(t4, model))
                sub = t4[t4["model"] == model]
                st.caption("Sai số chuẩn: " + se_text(sub["cov"].iloc[0], sub["n_groups"].iloc[0] if "n_groups" in sub else None)
                           + "." if "cov" in sub else "")
                sig = [TERM_LABELS.get(x, x) for x, p in zip(sub["term"], sub["p_value"]) if p < 0.05 and x != "const"]
                st.caption("Biến có ý nghĩa thống kê (p < 0,05): " + (", ".join(sig) if sig else "không có") + ".")
    st.caption("Biến phụ thuộc là IS điểm giữa. Biến chuẩn hóa (z) đã trừ trung bình, chia độ lệch chuẩn. "
               "Hệ số đọc theo hướng và ý nghĩa thống kê, không đọc như nhân quả.")


def page_gap(c):
    st.markdown(page_header("Chênh lệch giá & lợi nhuận", "Chênh lệch giữa các sàn có còn lãi?",
                            "Sự kiện chênh lệch giá giữa hai sàn cùng loại: tồn tại bao lâu, còn bao nhiêu sau phí, và "
                            "kết quả backtest khi lệnh khớp sau độ trễ.", "RQ4 · RQ5"), unsafe_allow_html=True)
    d7, d7_msg, kind, res, mode, t = c.d7, c.d7_msg, c.kind, c.res, c.mode, c.t
    scope = split_label(d7, c.manifest) if d7 is not None else None
    if d7 is not None and not d7.empty:
        st.markdown(badge(f"Dữ liệu {KIND_NAME.get(kind, kind)} · {fmt.num(len(d7))} sự kiện", "hist")
                    + badge(f"{d7['ts_start'].min():%d/%m/%Y} → {d7['ts_end'].max():%d/%m/%Y}")
                    + (badge(f"chỉ {scope}", "warn") if scope else ""), unsafe_allow_html=True)
        if scope:
            st.caption(f"Sự kiện chênh lệch được tính trên {scope} để không dùng trước dữ liệu kiểm tra cuối. "
                       "Một sự kiện = các quan sát liên tiếp mà giá bán ở sàn đắt cao hơn giá mua ở sàn rẻ từ "
                       "ngưỡng trở lên; chỉ ghép các sàn cùng loại.")

    st.subheader("RQ4 · Chênh lệch giá giữa các sàn tồn tại bao lâu?")
    sources = {"Lịch sử (lưới 60 giây)": (d7, 60)}
    if c.d7_live is not None and not c.d7_live.empty:
        sources["Live (lưới 5 giây)"] = (c.d7_live, 5)
    src_name = choose("Nguồn sự kiện", list(sources), key="gapsrc") if len(sources) > 1 else None
    view, step_s = sources[src_name] if src_name else sources["Lịch sử (lưới 60 giây)"]
    if view is None:
        st.write(d7_msg)
    elif view.empty:
        st.write("Không có sự kiện chênh lệch nào trong dữ liệu này.")
    else:
        cols = st.columns(4)
        cols[0].metric("Số sự kiện", fmt.num(len(view)))
        cols[1].metric("Kéo dài trung vị", fmt.duration_s(view["duration_s"].median()))
        cols[2].metric("Kéo dài lâu nhất", fmt.duration_s(view["duration_s"].max()))
        cols[3].metric("Chênh lệch trước phí (trung vị)", f"{fmt.bps(view['gross_bps'].median())} bps")
        with st.container(border=True):
            plot(charts.ccdf_fig(duration_ccdf(view), mode, t, step_s), "F4", "ccdf")
            st.caption("Đọc: tại mỗi mức thời gian, đường cho biết bao nhiêu phần trăm sự kiện kéo dài ít nhất chừng đó. "
                       "Đường rơi nhanh nghĩa là chênh lệch đóng lại nhanh. Lịch sử và live không vẽ chung vì lưới thời "
                       "gian khác nhau.")
        table(gap_summary(view, "gross_bps"), GAP_SUMMARY_COLS)
        st.markdown(f"#### {TOP_EVENTS} sự kiện có chênh lệch trước phí lớn nhất")
        table(view.sort_values("gross_bps", ascending=False).head(TOP_EVENTS), GAP_EVENT_COLS)
        explain(GAP_SUMMARY_COLS + GAP_EVENT_COLS)
    rr = regime_rates(res["t6b"]) if res.get("t6b") is not None else None
    with st.container(border=True):
        st.markdown("#### Chênh lệch xuất hiện nhiều hơn ở chế độ nào?")
        if rr is None or rr.empty:
            st.info("Chưa có dữ liệu: cần file T6b_gap_by_regime.csv trong outputs/tables.")
        else:
            plot(charts.t6b_fig(rr, mode, t), "T6b", "t6b")
            st.caption("So sánh mô tả, chưa kiểm định ý nghĩa thống kê. Tập phát triển; mỗi thanh là số sự kiện mỗi "
                       "1.000 phút ở chế độ đó chia cho mức tham chiếu (vạch tại 1,0).")

    st.subheader("RQ5 · Chênh lệch giá sau phí")
    if d7 is None:
        st.write(d7_msg)
    elif d7.empty:
        st.write("Không có sự kiện chênh lệch nào trong dữ liệu này.")
    else:
        cost = (d7["gross_bps"] - d7["net_bps"]).median()
        cols = st.columns(3)
        cols[0].metric("Trước phí (trung vị)", f"{fmt.bps(d7['gross_bps'].median())} bps")
        cols[1].metric("Chi phí hai chân (trung vị)", f"{fmt.bps(cost)} bps")
        cols[2].metric("Sau phí (trung vị)", f"{fmt.bps(d7['net_bps'].median())} bps")
        table(gap_summary(d7, "net_bps"), GAP_SUMMARY_COLS)
        st.caption("Sau phí = trước phí − phí taker hai chân − nửa chênh lệch mua–bán − trượt giá − chi phí khác "
                   "(theo cấu hình của TV6). Funding rate không nằm trong số này.")
        explain(["net_bps", "trung_vi_bps"])

    st.subheader("RQ5 · Còn lãi sau phí và độ trễ khớp lệnh không?")
    if d7 is None:
        st.write(d7_msg)
    elif d7.empty:
        st.write("Không có sự kiện chênh lệch nào trong dữ liệu này.")
    else:
        st.markdown("#### Bước 1: nếu khớp lệnh ngay lúc sự kiện bắt đầu")
        positive = d7[d7["net_bps"] > 0]
        columns = st.columns(2)
        for column, market in zip(columns, ("spot", "perp")):
            group = d7[d7["market_type"] == market]
            column.metric(f"{MARKET_NAME[market]}: sự kiện còn lãi sau chi phí",
                          fmt.num(int((positive["market_type"] == market).sum())))
            if len(group):
                note = f"trên {fmt.num(len(group))} sự kiện"
                if "any_net_positive" in group:
                    note += f"; {fmt.num(int(group['any_net_positive'].sum()))} sự kiện có ít nhất một quan sát dương sau phí"
                column.caption(note)
        if positive.empty:
            st.write("Không có sự kiện nào còn lãi sau chi phí trong dữ liệu này.")
        st.caption("Sự kiện thiếu giá trị sau phí không được tính. Bước này chưa tính độ trễ: lệnh thật chỉ khớp "
                   "sau khi ra quyết định.")

    st.markdown("#### Bước 2: khi lệnh khớp sau độ trễ (backtest)")
    t5 = res.get("t5")
    if t5 is None:
        st.write(res.get("msg"))
        return
    rows = t5[t5["n_filled"] > 0]
    pos = int(t5["n_positive_net"].sum())
    # Mỗi backtest một nguồn (A lịch sử, B live): báo riêng, không cộng gộp hai loại dữ liệu.
    parts = [f"{backtest_name(b)}: {fmt.ratio(int(g['n_positive_net'].sum()), int(g['n_filled'].sum()))} lệnh "
             f"có lãi, tổng {fmt.num(g['total_pnl_usdt'].sum(), 2)} USDT" for b, g in t5.groupby("backtest", sort=True)]
    head = "Không có lệnh nào có lãi" if pos == 0 else f"{fmt.num(pos)} lệnh có lãi"
    st.markdown(f"<div class='verdict {'no' if pos == 0 else ''}'><div class='head'>{head} sau phí và độ trễ"
                f"</div><p>{escape('. '.join(parts))}. Chênh lệch giữa các sàn có thật nhưng nhỏ hơn chi phí "
                "giao dịch và thường đóng lại trước khi lệnh kịp khớp.</p></div>", unsafe_allow_html=True)
    cols = st.columns(max(1, len(rows)))
    for col, (_, r) in zip(cols, rows.iterrows()):
        col.metric(f"{backtest_name(r['backtest'])} · {r['market_type']} · trễ {fmt.latency(r['latency'])}",
                   f"{fmt.ratio(int(r['n_positive_net']), int(r['n_filled']))} lệnh có lãi",
                   # dấu trừ ASCII để Streamlit tô đỏ mũi tên khi lỗ
                   f"{r['total_pnl_usdt']:.2f} USDT".replace(".", ","), delta_color="normal",
                   help=f"{backtest_name(r['backtest'])}, {MARKET_NAME[r['market_type']]}: lệnh khớp sau "
                        f"{fmt.latency(r['latency'])}; số dưới là tổng lãi/lỗ.")
    if not rows.empty:
        with st.container(border=True):
            st.markdown("#### Vì sao không còn lãi? Chi phí trung bình mỗi lệnh")
            names = {f"{backtest_name(r['backtest'])} · {MARKET_NAME[r['market_type']]}": i for i, r in rows.iterrows()}
            pick = choose("Nhóm lệnh", list(names), key="wf")
            plot(charts.waterfall_fig(cost_waterfall(rows.loc[names[pick]]), t), "T5", "wf_main")
            st.caption("Xanh: chênh lệch giá giữa hai sàn lúc ra quyết định. Đỏ: từng khoản chi phí, trong đó "
                       "'giá biến động trong lúc chờ khớp' là phần chênh lệch mất đi trong lúc chờ khớp. Thanh cuối là "
                       "lãi/lỗ ròng trung bình mỗi lệnh (đúng số trong T5).")
    ledger = res.get("ledger_a")
    if ledger is not None and ledger["status"].eq("filled").any():
        with st.container(border=True):
            st.markdown("#### Lãi/lỗ cộng dồn của backtest A (dữ liệu lịch sử)")
            pnl = cumulative_pnl(ledger)
            start = pnl.groupby("market_type", as_index=False)["ts_fill"].min().assign(pnl_usdt=0.0, cum_pnl_usdt=0.0)
            plot(charts.pnl_fig(pd.concat([start, pnl], ignore_index=True), mode, t), "F5", "pnl")
            st.caption("Mỗi điểm là một lệnh đã khớp (0,1 BTC). Đường đi xuống từng bậc: lệnh nào cũng lỗ.")
    t5b = res.get("t5b")
    if t5b is not None:
        fs = fee_scenarios(t5b)
        st.markdown("#### Kiểm tra độ nhạy chi phí (T5b)")
        st.markdown(f"Đổi phí (×1, ×0,5, ×0), độ trễ và quy mô lệnh: **{fmt.num(fs['with_profit'])} / "
                    f"{fmt.num(fs['n'])}** kịch bản còn tính phí có lệnh lãi ({fmt.ratio(fs['positive'], fs['signals'])} "
                    "lệnh). Phí ×0 là cận trên lý thuyết (bỏ hẳn phí), không phải chi phí thật.")
        grid = t5b[(t5b["slippage_bps"] == 0) & (t5b["qty_btc"] == 0.1)].copy()
        if not grid.empty:
            grid["Backtest"] = grid["backtest"].map(lambda b: "A · lịch sử (tập kiểm tra cuối)"
                                                    if b.startswith("A_") else "B · live")
            grid["Loại thị trường"] = grid["market_type"].map(MARKET_NAME)
            grid["_s"] = grid["latency"].map(fmt.latency_seconds)
            grid["Độ trễ khớp lệnh"] = grid["latency"].map(fmt.latency)
            grid["fee"] = grid["fee_multiplier"].map(lambda f: f"Phí ×{fmt.num(f, 1).rstrip('0').rstrip(',')}")
            grid["cell"] = [fmt.ratio(int(p), int(n)) for p, n in zip(grid["n_net_positive"], grid["n_signals"])]
            wide = grid.pivot_table(index=["Backtest", "Loại thị trường", "_s", "Độ trễ khớp lệnh"],
                                    columns="fee", values="cell", aggfunc="first")
            wide = wide[[x for x in ("Phí ×1", "Phí ×0,5", "Phí ×0") if x in wide.columns]]
            wide = wide.reset_index().sort_values(["Backtest", "Loại thị trường", "_s"]).drop(columns="_s")
            show_df(wide, right=[x for x in wide.columns if x.startswith('Phí')])
            st.caption("Mỗi ô: số lệnh có lãi / số tín hiệu (trượt giá 0 bps, 0,1 BTC). Backtest A ở bảng này "
                       "chạy một lần trên tập kiểm tra cuối với cấu hình đã đóng băng.")
    st.caption("Đây là mô phỏng nghiên cứu, không phải khuyến nghị giao dịch.")


def page_method(c):
    st.markdown(page_header("Phương pháp & dữ liệu", "Dữ liệu, mô hình và cách làm",
                            "Tóm tắt nguồn dữ liệu, cách đồng bộ, các mô hình đo dẫn giá, cách tính chi phí và backtest, "
                            "cùng các giới hạn. Thông số đọc từ config/config.yaml, config/fees.yaml và file kết quả.",
                            "Thuyết minh"), unsafe_allow_html=True)
    cfg, fees, vecm = _load_project_config(_mtime(str(ROOT / "config/config.yaml")))
    st.markdown(method_html(cfg, fees, vecm, c.manifest, c.res.get("t4")), unsafe_allow_html=True)
    st.subheader("Các bảng và hình dùng trên dashboard")
    files = pd.DataFrame([
        ("Bảng giá 1 phút (D3)", "data/processed/panel_1m_wide.parquet", "Giá & độ phân tán, So sánh"),
        ("Báo giá live (D2)", "data/processed/live_quotes_long.parquet", "Giá & độ phân tán, Các sàn"),
        ("Rolling IS/CS/ILS (D6)", "outputs/tables/rolling_is.parquet", "Sàn dẫn giá"),
        ("Sự kiện chênh lệch (D7)", "outputs/tables/gap_events_historical.parquet, gap_events.parquet",
         "Chênh lệch giá & lợi nhuận"),
        ("Đặc điểm các sàn (T1)", "outputs/tables/T1_exchange_characteristics.csv", "Các sàn, So sánh"),
        ("Lead–lag (T2)", "outputs/tables/T2_leadlag_xcorr.csv", "Sàn dẫn giá (RQ3)"),
        ("Yếu tố quyết định (T4)", "outputs/tables/T4_leadership_determinants.csv", "Sàn dẫn giá (RQ2)"),
        ("Arbitrage sau chi phí (T5, T5b)", "outputs/tables/T5_arbitrage_net.csv, T5b_cost_sensitivity.csv",
         "Chênh lệch giá & lợi nhuận (RQ5)"),
        ("Chênh lệch theo chế độ (T6b)", "outputs/tables/T6b_gap_by_regime.csv", "Chênh lệch giá & lợi nhuận (RQ4)"),
    ], columns=["Bảng", "File", "Dùng ở trang"])
    show_df(files, right=[])
    st.caption("Dashboard chỉ đọc và hiển thị kết quả có sẵn, không ước lượng lại mô hình. File nào thiếu thì trang "
               "tương ứng ghi 'Chưa có dữ liệu'.")


def page_download(c):
    st.markdown(page_header("Tải dữ liệu", "Tải bảng đang xem về máy",
                            "CSV giữ tên cột gốc của dự án để dùng lại trong code.", "CSV"), unsafe_allow_html=True)
    panel, d6, d7, res = c.panel, c.d6, c.d7, c.res
    label = st.selectbox("Khoảng thời gian của bảng giá và độ phân tán giá", list(DOWNLOAD_DAYS), key="days")
    cut = panel.index[-1] - pd.Timedelta(days=DOWNLOAD_DAYS[label])
    recent = panel.loc[panel.index > cut]
    disp = [s.loc[s.index > cut] for s in c.dispersions.values() if s is not None]
    left, right = st.columns(2)
    with left.container(border=True):
        st.markdown("**Giá và độ phân tán giá**")
        st.download_button("Giá phút cuối của 6 thị trường", to_csv_bytes(c.quotes.set_index("venue")),
                           "bao_gia_cuoi.csv", "text/csv", key="dl_quotes")
        st.download_button(f"Bảng giá 1 phút, {label}", to_csv_bytes(recent), "panel_1m_wide_gan_nhat.csv",
                           "text/csv", key="dl_panel")
        if disp:
            st.download_button(f"Độ phân tán giá, {label}", to_csv_bytes(pd.concat(disp, axis=1)),
                               "dispersion_bps.csv", "text/csv", key="dl_disp")
    with right.container(border=True):
        st.markdown("**Kết quả nghiên cứu**")
        if d6 is not None:
            st.download_button("Kết quả sàn dẫn giá (rolling IS)", to_csv_bytes(d6.set_index("window_start")),
                               "rolling_is.csv", "text/csv", key="dl_d6")
        if d7 is not None:
            st.download_button("Sự kiện chênh lệch giá", to_csv_bytes(d7.set_index("event_id")),
                               "gap_events.csv", "text/csv", key="dl_d7")
        if res.get("t5") is not None:
            st.download_button("Kết quả backtest (T5)", to_csv_bytes(res["t5"].set_index("backtest")),
                               "T5_arbitrage_net.csv", "text/csv", key="dl_t5")
        if d6 is None or d7 is None:
            st.caption("Nút tải kết quả sàn dẫn giá và sự kiện chênh lệch chỉ hiện khi file tương ứng đã có.")
    st.caption("Muốn toàn bộ bảng giá thì lấy file Parquet trên Drive; CSV ở đây chỉ để xem nhanh.")


PAGE_FUNCS = {"Tổng quan": page_home, "Các sàn giao dịch": page_exchanges, "So sánh các sàn": page_compare,
              "Giá & độ phân tán": page_price, "Sàn dẫn giá": page_lead, "Chênh lệch giá & lợi nhuận": page_gap,
              "Phương pháp & dữ liệu": page_method, "Tải dữ liệu": page_download}
PAGE_ICONS = {"Tổng quan": "space_dashboard", "Các sàn giao dịch": "account_balance", "So sánh các sàn": "leaderboard",
              "Giá & độ phân tán": "candlestick_chart", "Sàn dẫn giá": "hub", "Chênh lệch giá & lợi nhuận": "swap_horiz",
              "Phương pháp & dữ liệu": "menu_book", "Tải dữ liệu": "download"}


# --------------------------------------------------------------------------- khung chính
def main():
    st.set_page_config(page_title="Giá BTC và arbitrage giữa các sàn", page_icon="₿", layout="wide")

    st.sidebar.markdown(f"<div class='sb-brand'>{cursor_svg('Đồng Bitcoin', 30)}<span>Khám phá giá BTC"
                        "<small>Binance · OKX · Bybit</small></span></div>", unsafe_allow_html=True)
    with st.sidebar.container(key="navbox"):
        page = st.radio("Điều hướng", TAB_LABELS, key="nav", label_visibility="collapsed",
                        format_func=lambda p: f":material/{PAGE_ICONS[p]}: {p}")
    guide = dict(GUIDE_STEPS)[page]
    st.sidebar.caption(guide[:1].upper() + guide[1:])

    with st.sidebar.expander("Giao diện", expanded=False):
        mode = st.radio("Chế độ", list(MODES), horizontal=True, key="mode")
        preset = st.selectbox("Bảng màu", list(PRESETS) + [CUSTOM], key="preset")
        accent = (st.color_picker("Màu chủ đạo", "#F7931A", key="accent") if preset == CUSTOM
                  else PRESETS[preset])
        font = st.selectbox("Kiểu chữ", list(FONT_STACKS), key="font")
        size_pt = st.selectbox("Cỡ chữ (pt)", SIZES_PT, index=SIZES_PT.index(12), key="size")
        effects = st.toggle("Con trỏ chuột trang trí", value=True, key="effects")
        cursor = st.selectbox("Con trỏ chuột", CURSORS, key="cursor", disabled=not effects)

    # Đường dẫn lấy từ hằng số của dự án (ROOT). Chỉ hiện ô sửa đường dẫn khi mở với ?advanced=1
    # (hoặc biến môi trường DASHBOARD_ADVANCED=1) để người xem không thấy đường dẫn trên máy.
    source, d6_path, d7_path = str(PANEL_DEFAULT), str(D6_DEFAULT), str(D7_DEFAULT)
    live_path, results_dir = str(LIVE_DEFAULT), str(RESULTS_DEFAULT)
    if os.environ.get("DASHBOARD_ADVANCED") == "1" or st.query_params.get("advanced") == "1":
        # Các ô đường dẫn phải là text_input đầu tiên ở thanh bên, theo thứ tự D3, D6, D7 (test dựa vào thứ tự này).
        with st.sidebar.expander("Nâng cao: nguồn dữ liệu"):
            source = st.text_input("Bảng giá 1 phút (D3, Parquet)", source)
            d6_path = st.text_input("Kết quả sàn dẫn giá (D6, tùy chọn)", d6_path)
            d7_path = st.text_input("Sự kiện chênh lệch (D7, tùy chọn)", d7_path)
            live_path = st.text_input("Báo giá live (D2, tùy chọn)", live_path)
            results_dir = st.text_input("Thư mục kết quả backtest (T5, T5b)", results_dir)
    st.sidebar.caption("Mô phỏng nghiên cứu, không khuyến nghị giao dịch.")
    built = pd.Timestamp(_mtime(__file__), unit="s", tz="UTC").tz_convert("Asia/Ho_Chi_Minh")
    st.sidebar.caption(f"Phiên bản giao diện: {built:%d/%m/%Y %H:%M}")

    pal = charts.apply_palette(mode, accent)
    ART.update(c1=pal["spot"], c2=pal["perp"], c3=pal["cool"])
    st.markdown(theme_css(mode, accent, effects, cursor, "Không chuyển", font, size_pt), unsafe_allow_html=True)
    st.markdown(deco_html(accent), unsafe_allow_html=True)

    try:
        panel = _load_panel(source, _mtime(source))
    except (OSError, ValueError) as exc:
        st.error(f"Không đọc được bảng giá: {exc}")
        return
    if panel.empty:
        st.warning("Bảng giá rỗng: không có dòng nào để hiển thị.")
        return

    kind = data_kind(panel)
    venues = venues_from_panel(panel)
    asof = pd.Timestamp(panel.index[-1])
    asof = asof.tz_localize("UTC") if asof.tzinfo is None else asof.tz_convert("UTC")

    d6, d6_msg = _load_table(d6_path, _mtime(d6_path), tuple(D6_COLS), tuple(D6_TIME), kind, tuple(venues))
    d7, d7_msg = _load_table(d7_path, _mtime(d7_path), tuple(D7_COLS), tuple(D7_TIME), kind, tuple(venues))
    live, live_msg = _load_live(live_path, _mtime(live_path), tuple(venues))
    if d6 is not None:
        d6 = d6[(d6.window_start >= panel.index[0]) & (d6.available_at <= asof)].copy()
    if d7 is not None:
        # A whole event's duration/summary is not available until ts_end.
        d7 = d7[(d7.ts_start >= panel.index[0]) & (d7.ts_end <= asof)].copy()
    # Sự kiện chênh lệch của mẫu live nằm ở file riêng, không trộn với dữ liệu lịch sử.
    d7_live_path = str(Path(d7_path).with_name("gap_events.parquet"))
    d7_live, _ = (_load_table(d7_live_path, _mtime(d7_live_path), tuple(D7_COLS), tuple(D7_TIME), "live", tuple(venues))
                  if kind == "historical" else (None, None))
    # Kết quả nghiên cứu là của dữ liệu thật; không ghép với bảng giá giả lập.
    empty = dict(t5=None, t5b=None, ledger_a=None, t6b=None, xcorr=None, t4=None, t3c=None, t1=None, t0=None)
    if kind == "historical":
        res = _load_results(results_dir, _results_mtime(results_dir))
    else:
        res = dict(empty, msg=f"Kết quả backtest chỉ có cho dữ liệu thật; bảng giá đang là dữ liệu {KIND_NAME.get(kind, kind)}.")
    last = panel.iloc[-1]
    quotes = pd.DataFrame([
        {"venue": v, "market_type": v.rsplit("_", 1)[1], "close_usdt": last[f"close__{v}"],
         "missing": last[f"missing__{v}"] if f"missing__{v}" in panel.columns else None}
        for v in venues])
    ctx = SimpleNamespace(
        panel=panel, kind=kind, venues=venues, asof=asof, res_dir=results_dir, mode=mode, t=palette(mode, accent), d6=d6, d6_msg=d6_msg,
        d7=d7, d7_msg=d7_msg, d7_live=d7_live, live=live, live_msg=live_msg, res=res, quotes=quotes,
        manifest=_load_manifest(kind), dispersions={m: dispersion_bps(panel, venues, m) for m in ("spot", "perp")})
    PAGE_FUNCS[page](ctx)


if __name__ == "__main__":
    main()
