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
from urllib.parse import quote
import base64
import json
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

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
# Màu cố định cho hai nhóm thị trường (đã kiểm độ tương phản), không đổi theo màu chủ đạo.
# Ngoài màu còn phân biệt bằng nét: giao ngay nét liền, vĩnh cửu nét đứt.
SERIES = {"Sáng": {"spot": "#2a78d6", "perp": "#eb6834"}, "Tối": {"spot": "#3987e5", "perp": "#d95926"}}
DASH = {"spot": [], "perp": [6, 4]}
GOOD, BAD = "#1f9e6e", "#d6453d"

MARKET_NAME = {"spot": "Giao ngay (spot)", "perp": "Tương lai vĩnh cửu (perp)"}
EXCHANGE_NAME = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
KIND_NAME = {"historical": "lịch sử", "live": "live", "synthetic": "giả lập"}
STATUS_TEXT = {"ok": "Ước lượng được"}
REASON_TEXT = {"overlapping_bounds": "Chưa phân biệt được (các khoảng đóng góp chồng nhau)"}


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
    """Số kiểu báo cáo: khoảng trắng (không ngắt dòng) ngăn hàng nghìn, dấu phẩy thập phân, dấu trừ thật (−)."""
    s = f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


MARKET_SHORT = {"spot": "Giao ngay", "perp": "Vĩnh cửu"}


def venue_name(venue: str) -> str:
    """binance_spot -> 'Binance · Giao ngay', binance_perp -> 'Binance · Vĩnh cửu'. Tên lạ giữ nguyên."""
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


# --------------------------------------------------------------------------- giao diện (chỉ hiển thị)
ASSETS = Path(__file__).resolve().parent / "assets"
FONTS = ("https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700"
         "&family=Playfair+Display:wght@700;800&display=swap")
CURSORS = ["Đồng Bitcoin", "Đồng Tether", "Ethereum", "Nến giá", "Mặc định"]
TRANSITIONS = {"Đẩy sang": "push", "Mờ dần": "fade", "Lật sách": "flip", "Thu phóng": "zoom",
               "Quét ngang": "wipe", "Mở cửa": "doors", "Không chuyển": None}
FONT_STACKS = {"DM Sans": "'DM Sans',sans-serif", "Times New Roman": "'Times New Roman',Times,serif",
               "Georgia": "Georgia,serif", "Cambria": "Cambria,Georgia,serif", "Arial": "Arial,Helvetica,sans-serif",
               "Calibri": "Calibri,Carlito,Arial,sans-serif", "Verdana": "Verdana,sans-serif",
               "Tahoma": "Tahoma,sans-serif", "Courier New": "'Courier New',monospace"}
SIZES_PT = [10, 11, 12, 13, 14, 16, 18, 20]     # cỡ chữ như Word; 12 pt = 16 px (mặc định)
RANGES = {"1 ngày": 1, "7 ngày": 7, "30 ngày": 30, "Toàn bộ": None}
RQS = [
    "Sàn nào đóng góp nhiều nhất vào giá chung (giá hiệu quả) của Bitcoin?",
    "Vai trò dẫn giá có phụ thuộc khối lượng, chênh lệch mua–bán và độ sâu sổ lệnh không?",
    "Thị trường giao ngay (spot) hay hợp đồng tương lai vĩnh cửu (perp) dẫn giá?",
    "Chênh lệch giá giữa các sàn tồn tại bao lâu?",
    "Sau phí, chênh lệch mua–bán, trượt giá và độ trễ, còn bao nhiêu tín hiệu arbitrage có lãi?",
]
INTRO = ("Nhóm so sánh giá Bitcoin (BTC/USDT) trên 6 thị trường: Binance, OKX và Bybit, mỗi sàn có giao ngay "
         "và hợp đồng tương lai vĩnh cửu (perpetual futures: hợp đồng tương lai không có ngày đáo hạn). Câu hỏi thứ nhất: giá chung hình thành ở sàn nào trước (đo bằng Information Share, "
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


def theme_css(mode: str, accent: str, effects: bool, cursor: str = "Đồng Bitcoin", transition: str = "Đẩy sang",
              font: str = "DM Sans", size_pt: int = 12) -> str:
    """CSS: font, màu pastel, thẻ dạng viên thuốc, hero, thẻ số liệu; hiệu ứng (con trỏ, chuyển tab) chỉ khi effects=True."""
    t = palette(mode, accent)
    s = SERIES[mode]
    css = """@import url('__FONTS__');
html{font-size:__PX__px}
html,body,[data-testid="stAppViewContainer"],[data-testid="stSidebar"],button,input,select,
.stApp p,.stApp label,.stApp li,.stApp [data-testid="stMetricValue"]{font-family:__BODYFONT__!important}
.brand{display:flex;align-items:center;gap:10px;margin:0 0 6px;font-weight:600;font-size:.95rem;color:__MUTED__}
.brand b{color:__TEXT__}
.block-container{padding-top:1.4rem!important;max-width:1280px}
.stApp h1,.stApp h2,.stApp h3,.hero h1{font-family:'Playfair Display',Georgia,serif!important;letter-spacing:-.01em}
.stApp{background:__BG__;color:__TEXT__}
[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"]{background:__SURFACE__;border-right:1px solid __BORDER__}
.stApp h2,.stApp h3,.stApp h4,.stApp label,.stApp p,.stApp li,.stApp [data-testid="stMetricValue"],.stApp [data-testid="stAlert"] *{color:__TEXT__}
.stApp h3{border-left:5px solid __ACCENT__;padding:2px 0 2px 12px!important;margin-top:1.2rem}
.stApp h4{font-size:1.05rem;margin:1rem 0 .2rem}
.stApp [data-testid="stCaptionContainer"],.stApp [data-testid="stMetricLabel"] *{color:__MUTED__}
[data-baseweb="input"],[data-baseweb="select"]>div{background:__SURFACE__!important;border-radius:10px}
[data-baseweb="input"] input{color:__TEXT__!important}
[data-testid="stMetric"]{background:__SURFACE__;border:1px solid __BORDER__;border-left:4px solid __ACCENT__;border-radius:12px;padding:10px 14px}
[data-testid="stMetricValue"]{font-weight:700}
[data-testid="stMetricLabel"],[data-testid="stMetricLabel"] *,[data-testid="stMetricValue"],[data-testid="stMetricValue"] *{
 white-space:normal!important;overflow:visible!important;text-overflow:clip!important;line-height:1.25}
[data-testid="stMetricValue"]{font-size:clamp(1.4rem,2.4vw,2.1rem)!important}
[data-testid="stDataFrame"]{filter:__INVERT__;border:1px solid __BORDER__;border-radius:10px}
[data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]){border-radius:16px}
[data-testid="stExpander"] details{background:__SURFACE__;border:1px solid __BORDER__;border-radius:12px}
[data-baseweb="tab-list"]{gap:8px;flex-wrap:wrap}
button[data-baseweb="tab"]{border-radius:999px;padding:6px 18px;background:__SURFACE__;border:1px solid __BORDER__;color:__MUTED__}
button[data-baseweb="tab"] p{color:inherit!important;font-weight:600}
button[data-baseweb="tab"][aria-selected="true"]{background:__ACCENT__;border-color:__ACCENT__;color:#fff}
[data-baseweb="tab-highlight"],[data-baseweb="tab-border"]{display:none}
.stDownloadButton button{background:__ACCENT__;border:0;border-radius:999px}
.stDownloadButton button p{color:#fff!important}
.stDownloadButton button:hover{background:__ACCENT_DARK__}
[role="tabpanel"]{position:relative;padding-top:16px}
.hero{position:relative;min-height:420px;padding:44px 0 28px;overflow:hidden;border-radius:22px;background:__SURFACE__;border:1px solid __BORDER__}
.hero-art{position:absolute;inset:0 0 0 50%;background-size:cover;background-position:center;
 -webkit-mask-image:linear-gradient(to right,transparent 0,#000 45%);mask-image:linear-gradient(to right,transparent 0,#000 45%)}
.hero-body{position:relative;max-width:min(640px,100%);padding:0 40px}
.hero .eyebrow{font-size:.8rem;letter-spacing:.12em;text-transform:uppercase;color:__ACCENT__;font-weight:700;margin-bottom:10px}
.hero h1{font-weight:800;font-size:clamp(2.1rem,4.6vw,3.6rem);line-height:1.06;margin:0 0 14px;padding:0!important;color:__TEXT__}
.hero .intro{max-width:60ch;line-height:1.65;color:__TEXT__;margin-bottom:6px}
@media (max-width:800px){.hero-art{inset:0;opacity:.18}.hero-body{padding:0 16px}}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin:18px 0 8px}
.card{background:__SURFACE__;border:1px solid __BORDER__;border-radius:16px;padding:16px 18px 14px;position:relative}
.card .rq{font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:__ACCENT__;font-weight:700}
.card .q{font-size:.92rem;color:__MUTED__;margin:4px 0 10px;line-height:1.4}
.card .big{font-family:'Playfair Display',Georgia,serif;font-size:2.3rem;font-weight:800;line-height:1.05;color:__TEXT__}
.card .big.bad{color:__BAD__}
.card .lbl{font-weight:600;margin-top:4px;line-height:1.35}
.card .note{color:__MUTED__;font-size:.86rem;margin-top:8px;line-height:1.45}
.badge{display:inline-block;padding:3px 12px;border-radius:999px;font-size:.78rem;font-weight:700;letter-spacing:.02em;
 border:1px solid __BORDER__;background:__BG__;color:__TEXT__;margin:0 6px 6px 0}
.badge.hist{border-color:__SPOT__;color:__SPOT__}.badge.live{border-color:__PERP__;color:__PERP__}
.badge.warn{border-color:__BAD__;color:__BAD__}
.verdict{border-radius:16px;padding:18px 22px;margin:8px 0 14px;border:1px solid __BORDER__;background:__SURFACE__}
.verdict .head{font-family:'Playfair Display',Georgia,serif;font-size:1.5rem;font-weight:800;line-height:1.2}
.verdict.no .head{color:__BAD__}
.verdict p{margin:.4rem 0 0;color:__TEXT__}
.rq{list-style:none;margin:4px 0 12px;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:8px}
.rq li{background:__SURFACE__;border:1px solid __BORDER__;border-radius:12px;padding:9px 14px;line-height:1.4}
.rq b{color:__ACCENT__;margin-right:8px}
"""
    for key, value in {"__FONTS__": FONTS, "__BG__": t["bg"], "__SURFACE__": t["surface"], "__TEXT__": t["text"],
                       "__MUTED__": t["muted"], "__BORDER__": t["border"], "__INVERT__": t["invert"],
                       "__ACCENT__": accent, "__ACCENT_DARK__": _shade(accent, 0.7),
                       "__SPOT__": s["spot"], "__PERP__": s["perp"], "__BAD__": BAD,
                       "__BODYFONT__": FONT_STACKS.get(font, FONT_STACKS["DM Sans"]),
                       "__PX__": f"{size_pt * 4 / 3:.1f}"}.items():
        css = css.replace(key, value)
    if effects:
        if cursor in CURSORS and cursor != "Mặc định":
            css += f'html,body,.stApp,.stApp *{{cursor:url("data:image/svg+xml,{quote(cursor_svg(cursor))}") 16 16, auto !important}}'
        kind = TRANSITIONS.get(transition)
        if kind:
            css += TRANSITION_CSS.replace("__K__", kind)
    return f"<style>{css}</style>"


def hero_html() -> str:
    """Trang chủ: tên đề tài lớn và đoạn giới thiệu; ảnh minh họa chỉ nằm trong dải đầu trang."""
    art = f"background-image:url({asset_uri('hero_coins.avif')})" if asset_uri("hero_coins.avif") else ""
    return (f"<div class='hero'><div class='hero-art' style=\"{art}\"></div><div class='hero-body'>"
            "<div class='eyebrow'>Crypto Price Discovery and Cross-Exchange Arbitrage · BTC/USDT</div>"
            "<h1>Giá Bitcoin hình thành ở đâu, và chênh lệch giữa các sàn có còn lãi?</h1>"
            f"<div class='intro'>{INTRO}</div></div></div>")


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

CHART_HTML = """<!doctype html><html><body style="margin:0;background:transparent;font-family:system-ui,Segoe UI,sans-serif;color:__TEXT__">
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;padding:0 4px 6px"><div id="lg" style="font-size:13px"></div>
<div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:__MUTED__">Cuộn chuột: thu phóng · Kéo: lướt · Bấm đúp: đặt lại</span>
<button id="rs" style="background:__SURFACE__;color:__TEXT__;border:1px solid __BORDER__;border-radius:999px;padding:5px 14px;cursor:pointer;font-size:13px">Đặt lại</button>
<button id="rp" style="background:__ACCENT__;color:#fff;border:0;border-radius:999px;padding:5px 14px;cursor:pointer;font-size:13px">Chạy lại</button></div></div>
<div id="b" style="position:relative;height:360px"></div>
<div id="tp" style="position:fixed;display:none;pointer-events:none;background:__SURFACE__;border:1px solid __BORDER__;border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 6px 16px rgba(0,0,0,.2)"></div>
<script>
const D=__DATA__,C=__COLS__,DS=__DASH__,U=__UNIT__,ANIM=__ANIM__,H=360,P={l:58,r:44,t:14,b:30},MINSPAN=5*60000;
const N=Object.keys(D),box=document.getElementById('b'),tp=document.getElementById('tp');
const dash=k=>(DS[k]||[]).join(' ');
document.getElementById('lg').innerHTML=N.map((n,k)=>'<span style="margin-right:16px;white-space:nowrap"><svg width="26" height="8" style="vertical-align:middle;margin-right:6px"><line x1="0" x2="26" y1="4" y2="4" stroke="'+C[k]+'" stroke-width="3" stroke-dasharray="'+dash(k)+'"/></svg>'+n+'</span>').join('');
const A=[].concat(...N.map(n=>D[n])).filter(p=>p[1]!==null),x0=Math.min(...A.map(p=>p[0])),x1=Math.max(...A.map(p=>p[0]));
let v0=x0,v1=x1,lastW=0,drag=null;
const lb=(a,t)=>{let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;a[m][0]<t?lo=m+1:hi=m}return lo};
const vis=a=>a.slice(Math.max(0,lb(a,v0)-1),Math.min(a.length,lb(a,v1+1)+1));
const near=(a,t)=>{let lo=0,hi=a.length-1;while(hi-lo>1){const m=(lo+hi)>>1;a[m][0]<=t?lo=m:hi=m}return a[hi][0]<=t?a[hi]:a[lo][0]<=t?a[lo]:null};
const fmt=t=>{const s=new Date(t).toISOString();return s.slice(8,10)+'/'+s.slice(5,7)+' '+s.slice(11,16)};
const num=v=>v.toFixed(2).replace('.',',');
function clampView(){const span=Math.max(MINSPAN,Math.min(v1-v0,x1-x0));
 if(v0<x0){v0=x0;v1=x0+span}if(v1>x1){v1=x1;v0=x1-span}v0=Math.max(x0,v0);v1=Math.min(x1,v1)}
function draw(play){
 const W=box.clientWidth;if(!W){lastW=0;return}
 const shown=N.map(n=>vis(D[n])),vals=[].concat(...shown).filter(p=>p[1]!==null&&p[0]>=v0&&p[0]<=v1).map(p=>p[1]);
 let y1=vals.length?Math.max(...vals)*1.05:1;const y0=0;if(!(y1>y0))y1=1;
 const sx=t=>P.l+(t-v0)/((v1-v0)||1)*(W-P.l-P.r),sy=v=>H-P.b-(v-y0)/(y1-y0)*(H-P.t-P.b);
 let s='<svg width="'+W+'" height="'+H+'"><defs><clipPath id="cp"><rect x="'+P.l+'" y="0" width="'+(W-P.l-P.r)+'" height="'+H+'"/></clipPath></defs>';
 s+='<text transform="translate(12 '+((H-P.b+P.t)/2)+') rotate(-90)" text-anchor="middle" font-size="11" fill="__MUTED__">'+U+'</text>';
 for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4,y=sy(v);s+='<line x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y+'" y2="'+y+'" stroke="__BORDER__"/><text x="'+(P.l-6)+'" y="'+(y+4)+'" text-anchor="end" font-size="11" fill="__MUTED__">'+v.toFixed(1).replace('.',',')+'</text>'}
 for(let i=0;i<=5;i++){const t=v0+(v1-v0)*i/5;s+='<text x="'+sx(t)+'" y="'+(H-8)+'" text-anchor="'+(i==5?'end':'middle')+'" font-size="11" fill="__MUTED__">'+fmt(t)+'</text>'}
 s+='<g clip-path="url(#cp)">';
 N.forEach((n,k)=>{let pen=false;const d=shown[k].map(p=>{if(p[1]===null){pen=false;return ''}const cmd=pen?'L':'M';pen=true;return cmd+sx(p[0]).toFixed(1)+' '+sy(p[1]).toFixed(1)}).join('');
  s+='<path class="ln" data-k="'+k+'" d="'+d+'" fill="none" stroke="'+C[k]+'" stroke-width="1.8" stroke-linejoin="round" stroke-dasharray="'+dash(k)+'"/>'});
 s+='</g><line id="cx" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="__MUTED__" stroke-dasharray="3 3" style="display:none"/><rect id="ov" x="'+P.l+'" y="'+P.t+'" width="'+(W-P.l-P.r)+'" height="'+(H-P.t-P.b)+'" fill="transparent" style="cursor:grab"/></svg>';
 box.innerHTML=s;
 const run=ANIM&&(play||lastW===0);lastW=W;
 if(run)box.querySelectorAll('.ln').forEach((p,i)=>{const L=p.getTotalLength();p.style.strokeDasharray=L;p.style.strokeDashoffset=L;p.getBoundingClientRect();
  p.style.transition='stroke-dashoffset 1.8s cubic-bezier(.4,0,.2,1) '+(i*.3)+'s';p.style.strokeDashoffset=0;
  p.addEventListener('transitionend',()=>{p.style.transition='';p.style.strokeDasharray='';p.style.strokeDashoffset=''},{once:true})});
 const ov=box.querySelector('#ov'),cx=box.querySelector('#cx');
 ov.onmousemove=e=>{if(drag)return;const x=e.clientX-box.getBoundingClientRect().left,t=v0+(x-P.l)/(W-P.l-P.r)*(v1-v0);
  cx.setAttribute('x1',x);cx.setAttribute('x2',x);cx.style.display='';
  tp.innerHTML='<b>'+fmt(t)+' UTC</b>'+N.map((n,k)=>{const q=near(D[n],t);return '<br><span style="color:'+C[k]+'">&#9632;</span> '+n+': '+(q&&q[1]!==null?num(q[1])+' '+U+' (lúc '+fmt(q[0])+')':'thiếu dữ liệu')}).join('');
  tp.style.display='block';tp.style.left=Math.min(e.clientX+14,innerWidth-220)+'px';tp.style.top=(e.clientY+10)+'px'};
 ov.onmouseleave=()=>{tp.style.display='none';cx.style.display='none'};
 ov.addEventListener('wheel',e=>{e.preventDefault();const W2=box.clientWidth,x=e.clientX-box.getBoundingClientRect().left,
  t=v0+(x-P.l)/(W2-P.l-P.r)*(v1-v0),f=e.deltaY<0?0.8:1.25;
  v0=t-(t-v0)*f;v1=t+(v1-t)*f;clampView();draw(false)},{passive:false});
 ov.addEventListener('mousedown',e=>{drag={x:e.clientX,a:v0,b:v1};tp.style.display='none'});
}
window.addEventListener('mousemove',e=>{if(!drag)return;const W=box.clientWidth,dt=(e.clientX-drag.x)/(W-P.l-P.r)*(drag.b-drag.a);
 v0=drag.a-dt;v1=drag.b-dt;clampView();draw(false)});
window.addEventListener('mouseup',()=>{drag=null});
box.addEventListener('dblclick',()=>{v0=x0;v1=x1;draw(false)});
document.getElementById('rs').onclick=()=>{v0=x0;v1=x1;draw(false)};
document.getElementById('rp').onclick=()=>{v0=x0;v1=x1;draw(true)};
new ResizeObserver(()=>{if(box.clientWidth!==lastW)draw(false)}).observe(box);
</script></body></html>"""


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


def anim_chart_html(lines: dict, colors: list, animate: bool, t: dict, dashes: list | None = None,
                    unit: str = "bps") -> str:
    """Đồ thị đường tự vẽ khi mở thẻ: có hover xem giá trị, nút Chạy lại. lines = {tên: Series index thời gian}.
    dashes = nét đứt cho từng đường (vd [[], [6, 4]]); unit = đơn vị trục tung."""
    data = {}
    for name, ser in lines.items():
        idx = ser.index.tz_localize("UTC") if ser.index.tz is None else ser.index
        data[name] = [[int(ts.timestamp() * 1000), None if pd.isna(v) else round(float(v), 3)]
                      for ts, v in zip(idx, ser.to_numpy())]
    html = CHART_HTML
    for key, value in {"__DATA__": json.dumps(data), "__COLS__": json.dumps(colors[:len(data)]),
                       "__DASH__": json.dumps((dashes or [])[:len(data)]), "__UNIT__": json.dumps(unit),
                       "__ANIM__": "true" if animate else "false", "__TEXT__": t["text"], "__MUTED__": t["muted"],
                       "__BORDER__": t["border"], "__SURFACE__": t["surface"], "__ACCENT__": colors[0]}.items():
        html = html.replace(key, value)
    return html


def styled(chart, t: dict):
    """Áp màu chữ / lưới của giao diện cho biểu đồ Altair; nền trong suốt để ăn theo thẻ."""
    return (chart.properties(background="transparent")
            .configure_view(stroke=None)
            .configure_axis(labelColor=t["muted"], titleColor=t["muted"], gridColor=t["border"],
                            domainColor=t["border"], tickColor=t["border"], labelFontSize=12, titleFontSize=12,
                            titleFontWeight="normal")
            .configure_legend(labelColor=t["text"], titleColor=t["muted"], orient="top", labelFontSize=12,
                              symbolStrokeWidth=3)
            .configure_text(color=t["text"]))


def market_scale(mode: str, markets) -> tuple:
    """(màu, nét) cố định cho spot/perp: chỉ các nhóm có trong dữ liệu, đúng thứ tự spot rồi perp."""
    names = [MARKET_NAME[m] for m in ("spot", "perp") if m in set(markets)]
    keys = [m for m in ("spot", "perp") if m in set(markets)]
    color = alt.Scale(domain=names, range=[SERIES[mode][k] for k in keys])
    dash = alt.Scale(domain=names, range=[DASH[k] or [1, 0] for k in keys])
    return color, dash


def ccdf_chart(d7: pd.DataFrame, mode: str, t: dict):
    data = duration_ccdf(d7)
    data["Nhóm"] = data["market_type"].map(MARKET_NAME).fillna(data["market_type"])
    color, dash = market_scale(mode, data["market_type"])
    base = alt.Chart(data).encode(
        x=alt.X("duration_s:Q", scale=alt.Scale(type="log"), title="Kéo dài ít nhất (giây, thang log)"),
        y=alt.Y("share:Q", scale=alt.Scale(type="log"), axis=alt.Axis(format="%"),
                title="Tỷ lệ sự kiện (thang log)"),
        color=alt.Color("Nhóm:N", scale=color, title=None),
        tooltip=[alt.Tooltip("Nhóm:N"), alt.Tooltip("duration_s:Q", title="Kéo dài ít nhất (giây)"),
                 alt.Tooltip("share:Q", title="Tỷ lệ sự kiện", format=".2%")])
    lines = base.mark_line(interpolate="step-after", strokeWidth=2.2).encode(
        strokeDash=alt.StrokeDash("Nhóm:N", scale=dash, legend=None))
    return styled((lines + base.mark_point(size=28, filled=True)).properties(height=320), t)


def waterfall_chart(row: pd.Series, t: dict):
    data = cost_waterfall(row)
    data["nhan"] = data["value"].map(lambda v: "0 bps" if abs(v) < 5e-3 else f"{vn(v, 2 if abs(v) < 1 else 1)} bps")
    color = alt.Scale(domain=["gap", "cost", "net"], range=[GOOD, BAD, t["text"]])
    order = data["step"].tolist()
    bars = alt.Chart(data).mark_bar(cornerRadius=4, height=26).encode(
        y=alt.Y("step:N", sort=order, title=None, axis=alt.Axis(labelLimit=260)),
        x=alt.X("start:Q", title="bps mỗi lệnh (trung bình)", scale=alt.Scale(padding=60)), x2="end:Q",
        color=alt.Color("kind:N", scale=color, legend=None),
        tooltip=[alt.Tooltip("step:N", title="Bước"), alt.Tooltip("nhan:N", title="Giá trị")])
    labels = alt.Chart(data).transform_calculate(xr="max(datum.start, datum.end)").mark_text(
        align="left", dx=6, fontSize=12, fontWeight="bold").encode(y=alt.Y("step:N", sort=order),
                                                                    x="xr:Q", text="nhan:N")
    zero = alt.Chart(pd.DataFrame({"x": [0]})).mark_rule(color=t["muted"]).encode(x="x:Q")
    return styled((bars + labels + zero).properties(height=230), t)


def pnl_chart(ledger: pd.DataFrame, mode: str, t: dict):
    data = cumulative_pnl(ledger)
    # Mỗi đường bắt đầu từ 0 tại lệnh đầu tiên của nhóm, để thấy rõ lệnh đầu cũng lỗ.
    start = data.groupby("market_type", as_index=False)["ts_fill"].min().assign(pnl_usdt=0.0, cum_pnl_usdt=0.0)
    data = pd.concat([start, data], ignore_index=True)
    data["Nhóm"] = data["market_type"].map(MARKET_NAME).fillna(data["market_type"])
    color, dash = market_scale(mode, data["market_type"])
    base = alt.Chart(data).encode(
        x=alt.X("ts_fill:T", title="Thời điểm khớp lệnh (UTC)", axis=alt.Axis(format="%d/%m/%Y")),
        y=alt.Y("cum_pnl_usdt:Q", title="Lãi/lỗ cộng dồn (USDT)"),
        color=alt.Color("Nhóm:N", scale=color, title=None),
        tooltip=[alt.Tooltip("Nhóm:N"), alt.Tooltip("ts_fill:T", title="Khớp lúc (UTC)", format="%d/%m/%Y %H:%M"),
                 alt.Tooltip("pnl_usdt:Q", title="Lệnh này (USDT)", format=".2f"),
                 alt.Tooltip("cum_pnl_usdt:Q", title="Cộng dồn (USDT)", format=".2f")])
    lines = base.mark_line(interpolate="step-after", strokeWidth=2.2).encode(
        strokeDash=alt.StrokeDash("Nhóm:N", scale=dash, legend=None))
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=t["muted"], strokeDash=[2, 2]).encode(y="y:Q")
    return styled((zero + lines + base.mark_point(size=50, filled=True)).properties(height=300), t)


def bounds_chart(window: pd.DataFrame, mode: str, t: dict):
    data = window[["venue", "is_lower", "is_upper", "is_mid"]].copy()
    data["Sàn"] = data["venue"].map(venue_name)
    data["market_type"] = data["venue"].str.rsplit("_", n=1).str[1]
    data["Nhóm"] = data["market_type"].map(MARKET_NAME).fillna(data["market_type"])
    order = data.sort_values("is_mid", ascending=False)["Sàn"].tolist()
    color, _ = market_scale(mode, data["market_type"])
    y = alt.Y("Sàn:N", sort=order, title=None)
    tips = [alt.Tooltip("Sàn:N"), alt.Tooltip("is_lower:Q", title="Cận dưới", format=".1%"),
            alt.Tooltip("is_upper:Q", title="Cận trên", format=".1%")]
    rules = alt.Chart(data).mark_rule(strokeWidth=9, opacity=.55, strokeCap="round").encode(
        y=y, x=alt.X("is_lower:Q", scale=alt.Scale(domain=[0, 1]), axis=alt.Axis(format="%"),
                     title="Information Share: khoảng từ cận dưới đến cận trên"),
        x2="is_upper:Q", color=alt.Color("Nhóm:N", scale=color, title=None), tooltip=tips)
    mids = alt.Chart(data).mark_tick(thickness=3, size=20, color=t["text"]).encode(y=y, x="is_mid:Q", tooltip=tips)
    return styled((rules + mids).properties(height=max(150, 44 * len(data))), t)


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
LIVE_COLS = ["ts_grid_utc", "venue", "market_type", "bid", "ask", "mid", "spread_abs", "spread_bps",
             "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "data_kind"]


def live_latest(live: pd.DataFrame) -> pd.DataFrame:
    """Báo giá live mới nhất của từng venue (ts_grid_utc lớn nhất) kèm spread trung vị và số quote của cả mẫu.
    Không lấp, không nội suy: ô thiếu (ví dụ depth) để trống. Đơn vị: giá USDT, spread bps, depth USDT."""
    ordered = live.sort_values(["venue", "ts_grid_utc"])
    last = ordered.groupby("venue", sort=True).tail(1).set_index("venue")
    stats = ordered.groupby("venue")["spread_bps"].agg(spread_bps_median="median", n_quotes="size")
    cols = ["market_type", "ts_grid_utc", "bid", "ask", "spread_bps",
            "depth_bid_10bps_usdt", "depth_ask_10bps_usdt"]
    return last[cols].join(stats).reset_index()


@st.cache_data(show_spinner=False)
def _load_live(source: str, mtime: float, venues: tuple = ()):
    """Đọc live_quotes_long (D2). Trả về (DataFrame hoặc None, thông báo). Sai nguồn hoặc sai cột thì báo, không đoán."""
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
        if df["data_kind"].isna().any() or set(df["data_kind"].unique()) != {"live"}:
            raise ValueError("data_kind phải là 'live'")
        df["ts_grid_utc"] = pd.to_datetime(df["ts_grid_utc"], utc=True)
        if df["ts_grid_utc"].isna().any():
            raise ValueError("ts_grid_utc có giá trị thiếu")
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


def _results_mtime(folder: str) -> float:
    return max([_mtime(str(p)) for p in Path(folder).glob("*")] or [0.0])


@st.cache_data(show_spinner=False)
def _load_results(folder: str, mtime: float) -> dict:
    """Đọc T5 (bắt buộc cho phần backtest), T5b và sổ lệnh backtest A ứng với độ trễ trong T5 (tùy chọn).
    Trả về dict t5/t5b/ledger_a (DataFrame hoặc None) và msg. Thiếu cột thì báo, không đoán."""
    base = Path(folder)
    out = dict(t5=None, t5b=None, ledger_a=None, msg=None)
    try:
        t5 = pd.read_csv(base / "T5_arbitrage_net.csv")
        missing = [c for c in T5_COLS if c not in t5.columns]
        if missing:
            raise ValueError(f"T5 thiếu cột {missing}")
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
            if all(c in ledger.columns for c in LEDGER_COLS):
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
    "venue": "Thị trường giao dịch: tên sàn và loại hợp đồng. Ví dụ Binance · Giao ngay là BTC/USDT giao ngay trên Binance.",
    "market_type": "Giao ngay (spot) hoặc hợp đồng tương lai vĩnh cửu (perp: hợp đồng tương lai không có ngày đáo hạn, giá được neo về giá giao ngay bằng phí funding). Chỉ so sánh các sàn cùng loại với nhau.",
    "close_usdt": "Giá đóng cửa của nến 1 phút ở phút cuối của dữ liệu lịch sử, tính bằng USDT.",
    "missing": "Được đánh dấu khi sàn không có giá ở phút này (ô giá để trống, không điền thay).",
    "ts_grid_utc": "Mốc thời gian (UTC) trên lưới 5 giây mà báo giá này được gán vào.",
    "bid": "Giá mua cao nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "ask": "Giá bán thấp nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "spread_bps": "Chênh lệch giữa giá bán và giá mua so với giá giữa, tính bằng bps (1 bps = 0,01%). Càng nhỏ thì vào lệnh càng rẻ.",
    "spread_bps_median": "Trung vị chênh lệch mua–bán trên toàn bộ mẫu live (đại diện cho cả mẫu, không phải báo giá cuối).",
    "n_quotes": "Số báo giá live của sàn này trong mẫu.",
    "depth_bid_10bps_usdt": "Tổng giá trị lệnh mua đang chờ trong khoảng 0,1% dưới giá giữa, tính bằng USDT. Càng lớn thì lệnh lớn càng ít trượt giá.",
    "depth_ask_10bps_usdt": "Tổng giá trị lệnh bán đang chờ trong khoảng 0,1% trên giá giữa, tính bằng USDT.",
    "is_lower": "Cận dưới của Information Share: phần tối thiểu sàn này đóng góp vào việc hình thành giá chung (0 đến 100%). Tính qua mọi thứ tự xếp các sàn.",
    "is_upper": "Cận trên của Information Share. Khoảng giữa hai cận càng rộng thì càng khó nói sàn này đóng góp bao nhiêu.",
    "is_mid": "Điểm giữa của hai cận. Chỉ để xếp thứ tự tham khảo, không phải kết luận.",
    "component_share": "Component Share (Gonzalo–Granger): trọng số có dấu của sàn trong thành phần giá chung. Tổng các sàn trong một cửa sổ bằng 1, nhưng từng sàn có thể âm hoặc lớn hơn 1 nên không đọc như phần trăm; độ lớn chỉ nói mô hình dựa vào sàn đó mạnh hay yếu.",
    "ils": "Information Leadership Share: kết hợp Information Share và Component Share để tách sàn phản ứng nhanh khỏi sàn ít nhiễu. Ở đây là trung bình theo từng cặp sàn nên cộng các dòng lại không bằng 1.",
    "status": "Ước lượng được nghĩa là mô hình chạy được cho cửa sổ này. Giá trị khác là cửa sổ lỗi; xem cột Kết luận.",
    "reason": "Kết luận của cửa sổ. 'Chưa phân biệt được' nghĩa là khoảng đóng góp của các sàn chồng lên nhau, nên chưa đủ bằng chứng nói sàn nào dẫn giá.",
    "su_kien": "Số sự kiện chênh lệch giá trong nhóm. Một sự kiện gom các quan sát liên tiếp mà chênh lệch vượt ngưỡng.",
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
    "venue": ("Sàn", "text"), "market_type": ("Loại thị trường", "text"),
    "close_usdt": ("Giá đóng cửa (USDT)", "price"), "missing": ("Thiếu dữ liệu", "bool"),
    "ts_grid_utc": ("Thời điểm (UTC)", "time_s"), "bid": ("Giá mua (bid, USDT)", "price"),
    "ask": ("Giá bán (ask, USDT)", "price"), "spread_bps": ("Chênh lệch mua–bán (bps)", "bps"),
    "spread_bps_median": ("Chênh lệch trung vị (bps)", "bps"), "n_quotes": ("Số báo giá", "int"),
    "depth_bid_10bps_usdt": ("Độ sâu mua ±0,1% (USDT)", "big"),
    "depth_ask_10bps_usdt": ("Độ sâu bán ±0,1% (USDT)", "big"),
    "is_lower": ("Đóng góp – cận dưới", "share"), "is_upper": ("Đóng góp – cận trên", "share"),
    "is_mid": ("Đóng góp – điểm giữa", "share"), "component_share": ("Component Share (có dấu)", "num"),
    "ils": ("Chỉ số dẫn dắt ILS", "num"), "status": ("Trạng thái", "text"), "reason": ("Kết luận", "text"),
    "su_kien": ("Số sự kiện", "int"), "trung_vi_bps": ("Trung vị (bps)", "bps"), "thieu": ("Thiếu giá trị", "int"),
    "ts_start": ("Bắt đầu (UTC)", "time"), "ts_end": ("Kết thúc (UTC)", "time"),
    "duration_s": ("Kéo dài (giây)", "int"), "buy_exchange": ("Mua ở", "text"), "sell_exchange": ("Bán ở", "text"),
    "gross_bps": ("Trước phí (bps)", "bps"), "net_bps": ("Sau phí (bps)", "bps"),
}
QUOTE_COLS = ["venue", "market_type", "close_usdt", "missing"]
LIVE_SHOWN_COLS = ["venue", "market_type", "ts_grid_utc", "bid", "ask", "spread_bps",
                   "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "spread_bps_median", "n_quotes"]
RANK_COLS = ["venue", "is_lower", "is_upper", "is_mid", "component_share", "ils", "status", "reason"]
GAP_SUMMARY_COLS = ["market_type", "su_kien", "trung_vi_bps", "thieu"]
GAP_EVENT_COLS = ["ts_start", "duration_s", "market_type", "buy_exchange", "sell_exchange", "gross_bps", "net_bps"]


def label_of(col: str) -> str:
    return COLUMN_SPECS.get(col, (col, ""))[0]


def col_config(cols):
    """Tên cột tiếng Việt, định dạng số/thời gian và tooltip giải thích (rê chuột lên tiêu đề cột)."""
    cfg = {}
    for c in cols:
        if c not in GLOSSARY:
            continue
        label, kind = COLUMN_SPECS.get(c, (c, "text"))
        common = dict(label=label, help=GLOSSARY[c])
        if kind == "price":
            cfg[c] = st.column_config.NumberColumn(format="%.1f", **common)
        elif kind == "bps":
            cfg[c] = st.column_config.NumberColumn(format="%.2f", **common)
        elif kind == "num":
            cfg[c] = st.column_config.NumberColumn(format="%.3f", **common)
        elif kind == "share":
            cfg[c] = st.column_config.NumberColumn(format="percent", **common)
        elif kind == "big":
            cfg[c] = st.column_config.NumberColumn(format="compact", **common)
        elif kind == "int":
            cfg[c] = st.column_config.NumberColumn(format="%d", **common)
        elif kind == "time":
            cfg[c] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm", timezone="UTC", **common)
        elif kind == "time_s":
            cfg[c] = st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm:ss", timezone="UTC", **common)
        elif kind == "bool":
            cfg[c] = st.column_config.CheckboxColumn(**common)
        else:
            cfg[c] = st.column_config.TextColumn(**common)
    return cfg


def pretty(df: pd.DataFrame) -> pd.DataFrame:
    """Bản sao để HIỂN THỊ: tên sàn, loại thị trường, trạng thái bằng tiếng Việt. Dữ liệu tải về vẫn là bản gốc."""
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
        out["reason"] = out["reason"].map(lambda r: REASON_TEXT.get(r, r) if isinstance(r, str) else "")
    return out


def table(df: pd.DataFrame, cols: list):
    st.dataframe(pretty(df[cols]), hide_index=True, column_config=col_config(cols))


def explain(cols, title="Giải thích các cột"):
    """Khung mở ra liệt kê ý nghĩa từng cột của bảng ngay phía trên."""
    with st.expander(title):
        st.markdown("\n".join(f"- **{label_of(c)}**: {GLOSSARY[c]}" for c in cols if c in GLOSSARY))


TAB_LABELS = ["Trang chủ", "Giá các sàn", "Độ lệch giá", "Sàn dẫn giá", "Chênh lệch và arbitrage", "Tải dữ liệu"]
GUIDE_STEPS = [
    ("Trang chủ", "câu trả lời ngắn cho 5 câu hỏi nghiên cứu, số liệu lấy thẳng từ kết quả."),
    ("Giá các sàn", "giá BTC trên 6 thị trường: dữ liệu lịch sử và dữ liệu live để riêng."),
    ("Độ lệch giá", "các sàn cùng loại lệch giá nhau bao nhiêu theo thời gian. Cuộn chuột để phóng to."),
    ("Sàn dẫn giá", "sàn nào đóng góp nhiều vào giá chung, và vì sao chưa kết luận được."),
    ("Chênh lệch và arbitrage", "chênh lệch kéo dài bao lâu, và có còn lãi sau phí, độ trễ không."),
    ("Tải dữ liệu", "lấy bảng đang xem về máy dạng CSV."),
]


def _default_path(contract: Path) -> str:
    """Không tự chuyển provenance khi thiếu file; người dùng chọn nguồn rõ ràng."""
    return str(contract)


def backtest_name(backtest: str) -> str:
    return "Backtest A · lịch sử" if str(backtest).startswith("A_") else "Backtest B · live"


def latency_text(latency: str) -> str:
    lat = pd.Timedelta(latency)
    return (f"{int(lat.total_seconds() // 60)} phút" if lat >= pd.Timedelta(minutes=1)
            else f"{int(lat.total_seconds())} giây")


def _fmt_day(ts) -> str:
    return f"{pd.Timestamp(ts):%d/%m/%Y}"


def home_cards(panel, venues, d6, d7, res) -> list:
    """Thẻ kết quả trang chủ. Chỉ thêm thẻ khi có file nguồn; mọi số đều tính từ file, không gõ tay."""
    cards = [dict(rq="Dữ liệu", q="Mẫu lịch sử dùng cho mọi kết quả",
                  big=f"{vn(len(panel))} phút", lbl=f"{len(venues)} thị trường, nến 1 phút",
                  note=f"{_fmt_day(panel.index[0])} → {_fmt_day(panel.index[-1])} (UTC)")]
    if d6 is not None and not d6.empty:
        windows = d6.groupby(["window_start", "window_end"])
        decided = int(windows["leader"].apply(lambda s: s.dropna().astype(str).str.strip().ne("").any()).sum())
        cards.append(dict(rq="RQ1 · RQ3", q=RQS[0], big=f"{vn(decided)}/{vn(windows.ngroups)}",
                          lbl="cửa sổ 1 ngày xác định được sàn dẫn giá", bad=decided == 0,
                          note="Khoảng đóng góp của các sàn chồng lên nhau: dữ liệu chưa đủ để nói sàn nào, "
                               "hay spot hay perp, dẫn giá."))
    if d7 is not None and not d7.empty:
        single = (d7["n_obs"].eq(1).mean() if "n_obs" in d7 else np.nan)
        note = (f"{vn(single * 100, 1)}% sự kiện chỉ có một quan sát. " if pd.notna(single) else "")
        cards.append(dict(rq="RQ4", q=RQS[3], big=f"{vn(d7['duration_s'].median())} giây",
                          lbl=f"thời gian tồn tại trung vị của {vn(len(d7))} sự kiện chênh lệch",
                          note=note + f"90% sự kiện kết thúc trong {vn(d7['duration_s'].quantile(.9))} giây."))
    t5 = res.get("t5")
    if t5 is not None:
        sig, pos = int(t5["n_filled"].sum()), int(t5["n_positive_net"].sum())
        note = "; ".join(f"{backtest_name(b)}: {vn(int(g['n_filled'].sum()))} lệnh, "
                         f"{vn(g['total_pnl_usdt'].sum(), 2)} USDT" for b, g in t5.groupby("backtest", sort=True)) + "."
        t5b = res.get("t5b")
        if t5b is not None:
            fs = fee_scenarios(t5b)
            note += (f" Kiểm tra độ nhạy: {vn(fs['with_profit'])}/{vn(fs['n'])} kịch bản còn tính phí có lệnh lãi; "
                     "chỉ khi bỏ hẳn phí mới có lãi.")
        cards.append(dict(rq="RQ5", q=RQS[4], big=f"{vn(pos)}/{vn(sig)}", bad=pos == 0,
                          lbl="lệnh backtest có lãi sau phí và độ trễ", note=note))
    return cards


# --------------------------------------------------------------------------- giao diện
def main():
    st.set_page_config(page_title="Giá BTC và arbitrage giữa các sàn", page_icon="₿", layout="wide")

    with st.sidebar.expander("Hướng dẫn xem", expanded=True):
        st.markdown("\n".join(f"{i}. **{name}**: {text}" for i, (name, text) in enumerate(GUIDE_STEPS, 1)))
        st.caption("Rê chuột lên tên cột để xem ý nghĩa; mở khung Giải thích các cột dưới mỗi bảng.")
    st.sidebar.caption("Mô phỏng nghiên cứu, không khuyến nghị giao dịch.")

    with st.sidebar.expander("Giao diện"):
        mode = st.radio("Chế độ", list(MODES), horizontal=True, key="mode")
        preset = st.selectbox("Bảng màu", list(PRESETS) + [CUSTOM], key="preset")
        accent = (st.color_picker("Màu chủ đạo", "#F7931A", key="accent") if preset == CUSTOM
                  else PRESETS[preset])
        font = st.selectbox("Kiểu chữ nội dung", list(FONT_STACKS), key="font")
        size_pt = st.selectbox("Cỡ chữ (pt)", SIZES_PT, index=SIZES_PT.index(12), key="size")
        effects = st.toggle("Hiệu ứng (con trỏ, chuyển tab, đồ thị tự vẽ)", value=True, key="effects")
        cursor = st.selectbox("Con trỏ chuột", CURSORS, key="cursor", disabled=not effects)
        transition = st.selectbox("Kiểu chuyển tab", list(TRANSITIONS), key="transition", disabled=not effects)

    # Các ô đường dẫn phải là text_input đầu tiên ở thanh bên, theo thứ tự D3, D6, D7 (test dựa vào thứ tự này).
    with st.sidebar.expander("Nâng cao: nguồn dữ liệu"):
        source = st.text_input("Bảng giá 1 phút (D3, Parquet)", str(PANEL_DEFAULT))
        d6_path = st.text_input("Kết quả sàn dẫn giá (D6, tùy chọn)", _default_path(D6_DEFAULT))
        d7_path = st.text_input("Sự kiện chênh lệch (D7, tùy chọn)", _default_path(D7_DEFAULT))
        live_path = st.text_input("Báo giá live (D2, tùy chọn)", str(LIVE_DEFAULT))
        results_dir = st.text_input("Thư mục kết quả backtest (T5, T5b)", str(RESULTS_DEFAULT))

    st.markdown(theme_css(mode, accent, effects, cursor, transition, font, size_pt), unsafe_allow_html=True)
    st.markdown(f"<div class='brand'>{cursor_svg('Đồng Bitcoin', 24)}<span><b>BTC/USDT</b> · Binance · OKX · Bybit"
                " · giao ngay và tương lai vĩnh cửu</span></div>", unsafe_allow_html=True)
    st.iframe(transition_js(), height=1)

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
    t = palette(mode, accent)
    series = SERIES[mode]

    d6, d6_msg = _load_table(d6_path, _mtime(d6_path), tuple(D6_COLS), tuple(D6_TIME), kind, tuple(venues))
    d7, d7_msg = _load_table(d7_path, _mtime(d7_path), tuple(D7_COLS), tuple(D7_TIME), kind, tuple(venues))
    live, live_msg = _load_live(live_path, _mtime(live_path), tuple(venues))
    if d6 is not None:
        d6 = d6[(d6.window_start >= panel.index[0]) & (d6.available_at <= asof)].copy()
    if d7 is not None:
        # A whole event's duration/summary is not available until ts_end.
        d7 = d7[(d7.ts_start >= panel.index[0]) & (d7.ts_end <= asof)].copy()
    # Kết quả backtest là của dữ liệu thật; không ghép với panel giả lập.
    if kind == "historical":
        res = _load_results(results_dir, _results_mtime(results_dir))
    else:
        res = dict(t5=None, t5b=None, ledger_a=None,
                   msg=f"Kết quả backtest chỉ có cho dữ liệu thật; bảng giá đang là dữ liệu {KIND_NAME.get(kind, kind)}.")
    manifest = _load_manifest(kind)

    tab_home, tab_quote, tab_disp, tab_lead, tab_gap, tab_data = st.tabs(TAB_LABELS)
    with tab_home:
        st.markdown(hero_html(), unsafe_allow_html=True)
        st.markdown(cards_html(home_cards(panel, venues, d6, d7, res)), unsafe_allow_html=True)
        st.markdown("#### Năm câu hỏi nghiên cứu")
        st.markdown(rq_html(), unsafe_allow_html=True)
        st.caption(f"Nguồn: dữ liệu {KIND_NAME.get(kind, kind)} · {len(venues)} thị trường · quan sát cuối "
                   f"{asof:%d/%m/%Y %H:%M} UTC. RQ2 (yếu tố quyết định vai trò dẫn giá) có trong báo cáo, "
                   "không có trên dashboard. Giá USDT, chênh lệch tính bằng bps (1 bps = 0,01%), thời gian UTC.")

    # 1. Giá các sàn
    last = panel.iloc[-1]
    quotes = pd.DataFrame([
        {"venue": v, "market_type": v.rsplit("_", 1)[1], "close_usdt": last[f"close__{v}"],
         "missing": last[f"missing__{v}"] if f"missing__{v}" in panel.columns else None}
        for v in venues])
    with tab_quote:
        st.subheader("1. Giá BTC trên từng sàn")
        with st.container(border=True):
            st.markdown(badge(f"Dữ liệu {KIND_NAME.get(kind, kind)} · nến 1 phút", "hist")
                        + badge(f"Phút cuối: {asof:%d/%m/%Y %H:%M} UTC"), unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("Số thị trường", len(venues))
            c2.metric("Số phút trong mẫu", vn(len(panel)))
            prices = quotes["close_usdt"].dropna()
            if len(prices) > 1:
                c3.metric("Cao nhất − thấp nhất ở phút cuối",
                          f"{vn((prices.max() - prices.min()) / prices.mean() * 1e4, 2)} bps")
            table(quotes, QUOTE_COLS)
            explain(QUOTE_COLS)
        with st.container(border=True):
            st.markdown("#### Báo giá live gần nhất")
            if live is None:
                st.write(live_msg)
            else:
                st.markdown(badge("Dữ liệu live · bid/ask mỗi 5 giây", "live")
                            + badge(f"{live['ts_grid_utc'].min():%d/%m/%Y %H:%M} → "
                                    f"{live['ts_grid_utc'].max():%d/%m/%Y %H:%M} UTC")
                            + badge(f"{vn(len(live))} báo giá"), unsafe_allow_html=True)
                table(live_latest(live), LIVE_SHOWN_COLS)
                explain(LIVE_SHOWN_COLS)
                st.caption("Mẫu live là một giai đoạn ngắn KHÁC giai đoạn dữ liệu lịch sử ở trên, nên giá hai bảng "
                           "khác nhau là bình thường; không so trực tiếp hai bảng.")

    # 2. Độ lệch giá
    dispersions = {m: dispersion_bps(panel, venues, m) for m in ("spot", "perp")}
    with tab_disp:
        st.subheader("2. Các sàn lệch giá nhau bao nhiêu?")
        st.caption("Mỗi phút: (giá cao nhất − giá thấp nhất) / giá trung bình giữa các sàn cùng loại, tính bằng bps "
                   "(1 bps = 0,01%). Số càng lớn thì các sàn càng lệch giá nhau.")
        choice = st.radio("Khoảng thời gian", list(RANGES) + ["Tùy chọn"], index=1, horizontal=True, key="range")
        custom = None
        if choice == "Tùy chọn":
            first, last_day = panel.index[0].date(), panel.index[-1].date()
            custom = st.date_input("Từ ngày đến ngày", (first, last_day), min_value=first, max_value=last_day, key="drange")
        mask = range_mask(panel.index, choice, custom)
        raw = {m: ser[mask] for m, ser in dispersions.items() if ser is not None}
        raw = {m: r for m, r in raw.items() if r.notna().any()}
        if not raw:
            st.write("Không có dữ liệu độ lệch giá trong khoảng này (cần ít nhất hai sàn cùng loại).")
        else:
            cols = st.columns(2 * len(raw))
            for i, (market, r) in enumerate(raw.items()):
                valid = r.dropna()
                cols[2 * i].metric(f"{market} · trung vị", f"{vn(valid.median(), 2)} bps",
                                   help=f"{MARKET_NAME[market]}: trung vị độ lệch giá trong khoảng đang chọn.")
                cols[2 * i + 1].metric(f"{market} · phút cuối", f"{vn(valid.iloc[-1], 2)} bps",
                                       help=f"{MARKET_NAME[market]}: phút cuối có đủ giá, "
                                            f"{valid.index[-1]:%d/%m/%Y %H:%M} UTC.")
            st.iframe(anim_chart_html({MARKET_NAME[m]: thin(r) for m, r in raw.items()},
                                            [series[m] for m in raw], effects, t, [DASH[m] for m in raw]),
                            height=430)
            st.caption("Đồ thị giữ đỉnh tại đúng thời điểm gốc; chỗ thiếu giá để ngắt đường, không nối thay.")
            with st.expander("Cách tính độ lệch giá"):
                st.markdown("Tại mỗi phút, lấy giá cao nhất trừ giá thấp nhất giữa các sàn **cùng loại** (giao ngay với "
                            "giao ngay, vĩnh cửu với vĩnh cửu), chia cho giá trung bình rồi nhân 10.000 để ra bps. "
                            "Phút nào thiếu giá của một sàn trong nhóm thì để trống, không điền thay.")

    # 3 và 4. Sàn dẫn giá
    window = None
    if d6 is None:
        window_msg = d6_msg
    else:
        window = current_window(d6, asof)
        window_msg = None if window is not None else "Chưa có cửa sổ rolling nào sẵn sàng tại thời điểm này."
    with tab_lead:
        st.subheader("3. Sàn nào đang dẫn dắt giá?")
        if window is None:
            st.write(window_msg)
        else:
            leader, reason = leader_of(window)
            if leader:
                st.success(f"Sàn dẫn giá: {venue_name(leader)}")
            else:
                code = reason.split(":")[0].strip()
                text = ("các khoảng đóng góp của các sàn chồng lên nhau" if code == "overlapping_bounds" else reason)
                st.warning(f"Chưa xác định được sàn dẫn giá: {text} ({code}).")
            windows = d6.groupby(["window_start", "window_end"])
            decided = int(windows["leader"].apply(lambda s: s.dropna().astype(str).str.strip().ne("").any()).sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("Số cửa sổ rolling (1 ngày, bước 1 giờ)", vn(windows.ngroups))
            c2.metric("Cửa sổ xác định được sàn dẫn", vn(decided))
            c3.metric("Cửa sổ mới nhất kết thúc", f"{window['window_end'].iloc[0]:%d/%m/%Y}",
                      help=f"{window['window_end'].iloc[0]:%d/%m/%Y %H:%M} UTC")
            st.caption("Chỉ công nhận một sàn dẫn giá khi cận dưới của nó lớn hơn cận trên của mọi sàn khác "
                       "(hai khoảng không chồng nhau).")
            stale = asof - window["window_end"].iloc[0]
            if stale > pd.Timedelta(days=1):
                st.info(f"Kết quả sàn dẫn giá có đến {window['window_end'].iloc[0]:%d/%m/%Y}, tức {stale.days} ngày "
                        "trước phút cuối của dữ liệu: chỉ tính trên tập phát triển và xác thực, tập kiểm tra cuối "
                        "không dùng ở bước này.")
        st.subheader("4. Mức đóng góp vào giá chung của từng sàn")
        if window is None:
            st.write(window_msg)
        else:
            st.markdown(f"Cửa sổ {window['window_start'].iloc[0]:%d/%m/%Y %H:%M} → "
                        f"{window['window_end'].iloc[0]:%d/%m/%Y %H:%M} UTC. Mỗi thanh là khoảng Information Share "
                        "của một sàn (vạch đậm là điểm giữa). Thanh càng dài thì càng khó nói sàn đó đóng góp bao nhiêu; "
                        "các thanh chồng lên nhau nghĩa là chưa phân biệt được sàn dẫn.")
            st.altair_chart(bounds_chart(window, mode, t), width="stretch", theme=None)
            table(window.sort_values("is_mid", ascending=False), RANK_COLS)
            st.caption("Component Share có thể âm hoặc lớn hơn 1: đó là trọng số có dấu (tổng các sàn bằng 1), "
                       "không phải phần trăm. ILS ở đây là trung bình theo từng cặp sàn nên cộng lại không bằng 1.")
            explain(RANK_COLS)

    # 5, 6, 7. Chênh lệch và arbitrage
    with tab_gap:
        scope = split_label(d7, manifest) if d7 is not None else None
        if d7 is not None and not d7.empty:
            st.markdown(badge(f"Dữ liệu {KIND_NAME.get(kind, kind)} · {vn(len(d7))} sự kiện", "hist")
                        + badge(f"{d7['ts_start'].min():%d/%m/%Y} → {d7['ts_end'].max():%d/%m/%Y}")
                        + (badge(f"chỉ {scope}", "warn") if scope else ""), unsafe_allow_html=True)
            if scope:
                st.caption(f"Sự kiện chênh lệch được tính trên {scope} để không dùng trước dữ liệu kiểm tra cuối. "
                           "Một sự kiện = các quan sát liên tiếp mà giá bán ở sàn đắt cao hơn giá mua ở sàn rẻ từ "
                           "ngưỡng trở lên; chỉ ghép các sàn cùng loại.")

        st.subheader("5. Chênh lệch giá trước phí")
        if d7 is None:
            st.write(d7_msg)
        elif d7.empty:
            st.write("Không có sự kiện chênh lệch nào trong dữ liệu này.")
        else:
            cols = st.columns(4)
            cols[0].metric("Số sự kiện", vn(len(d7)))
            cols[1].metric("Kéo dài trung vị", f"{vn(d7['duration_s'].median())} giây")
            cols[2].metric("Kéo dài lâu nhất", f"{vn(d7['duration_s'].max())} giây")
            cols[3].metric("Chênh lệch trước phí trung vị", f"{vn(d7['gross_bps'].median(), 2)} bps")
            st.markdown("#### Chênh lệch tồn tại bao lâu?")
            st.altair_chart(ccdf_chart(d7, mode, t), width="stretch", theme=None)
            st.caption("Đọc: tại 120 giây, đường cho biết bao nhiêu phần trăm sự kiện kéo dài ít nhất 120 giây. "
                       "Đường rơi nhanh nghĩa là chênh lệch đóng lại nhanh.")
            table(gap_summary(d7, "gross_bps"), GAP_SUMMARY_COLS)
            st.markdown(f"#### {TOP_EVENTS} sự kiện có chênh lệch trước phí lớn nhất")
            table(d7.sort_values("gross_bps", ascending=False).head(TOP_EVENTS), GAP_EVENT_COLS)
            explain(GAP_SUMMARY_COLS + GAP_EVENT_COLS)

        st.subheader("6. Chênh lệch giá sau phí")
        if d7 is None:
            st.write(d7_msg)
        elif d7.empty:
            st.write("Không có sự kiện chênh lệch nào trong dữ liệu này.")
        else:
            cost = (d7["gross_bps"] - d7["net_bps"]).median()
            cols = st.columns(3)
            cols[0].metric("Trước phí (trung vị)", f"{vn(d7['gross_bps'].median(), 2)} bps")
            cols[1].metric("Chi phí hai chân (trung vị)", f"{vn(cost, 2)} bps")
            cols[2].metric("Sau phí (trung vị)", f"{vn(d7['net_bps'].median(), 2)} bps")
            table(gap_summary(d7, "net_bps"), GAP_SUMMARY_COLS)
            st.caption("Sau phí = trước phí − phí taker hai chân − nửa chênh lệch mua–bán − trượt giá − chi phí khác "
                       "(theo cấu hình của TV6). Funding rate không nằm trong số này.")
            explain(["net_bps", "trung_vi_bps"])

        st.subheader("7. Còn lãi sau phí và độ trễ không?")
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
                              int((positive["market_type"] == market).sum()))
                if len(group):
                    note = f"trên {vn(len(group))} sự kiện"
                    if "any_net_positive" in group:
                        note += f"; {vn(int(group['any_net_positive'].sum()))} sự kiện có ít nhất một quan sát dương sau phí"
                    column.caption(note)
            if positive.empty:
                st.write("Không có sự kiện nào còn lãi sau chi phí trong dữ liệu này.")
            st.caption("Sự kiện thiếu giá trị sau phí không được tính. Bước này chưa tính độ trễ: lệnh thật chỉ khớp "
                       "sau khi ra quyết định.")

        st.markdown("#### Bước 2: khi lệnh khớp sau độ trễ (backtest)")
        t5 = res.get("t5")
        if t5 is None:
            st.write(res.get("msg"))
        else:
            rows = t5[t5["n_filled"] > 0]
            pos = int(t5["n_positive_net"].sum())
            # Mỗi backtest một nguồn (A lịch sử, B live): báo riêng, không cộng gộp hai loại dữ liệu.
            parts = [f"{backtest_name(b)}: {vn(int(g['n_positive_net'].sum()))}/{vn(int(g['n_filled'].sum()))} lệnh "
                     f"có lãi, tổng {vn(g['total_pnl_usdt'].sum(), 2)} USDT" for b, g in t5.groupby("backtest", sort=True)]
            head = "Không có lệnh nào có lãi" if pos == 0 else f"{vn(pos)} lệnh có lãi"
            st.markdown(f"<div class='verdict {'no' if pos == 0 else ''}'><div class='head'>{head} sau phí và độ trễ"
                        f"</div><p>{escape('. '.join(parts))}. Chênh lệch giữa các sàn có thật nhưng nhỏ hơn chi phí "
                        "giao dịch và thường đóng lại trước khi lệnh kịp khớp.</p></div>", unsafe_allow_html=True)
            cols = st.columns(max(1, len(rows)))
            for col, (_, r) in zip(cols, rows.iterrows()):
                col.metric(f"{backtest_name(r['backtest'])} · {r['market_type']} · trễ {latency_text(r['latency'])}",
                           f"{int(r['n_positive_net'])}/{int(r['n_filled'])} lệnh có lãi",
                           # dấu trừ ASCII để Streamlit tô đỏ mũi tên khi lỗ
                           f"{r['total_pnl_usdt']:.2f} USDT".replace(".", ","), delta_color="normal",
                           help=f"{backtest_name(r['backtest'])}, {MARKET_NAME[r['market_type']]}: lệnh khớp sau "
                                f"{latency_text(r['latency'])}; số dưới là tổng lãi/lỗ.")
            if not rows.empty:
                st.markdown("#### Vì sao không còn lãi? Chi phí trung bình mỗi lệnh")
                names = {f"{backtest_name(r['backtest'])} · {MARKET_NAME[r['market_type']]}": i
                         for i, r in rows.iterrows()}
                pick = st.radio("Nhóm lệnh", list(names), horizontal=True, key="wf")
                st.altair_chart(waterfall_chart(rows.loc[names[pick]], t), width="stretch", theme=None)
                st.caption("Xanh: chênh lệch giá giữa hai sàn lúc ra quyết định. Đỏ: từng khoản chi phí, trong đó "
                           "'giá biến động trong lúc chờ khớp' là phần chênh lệch mất đi trong lúc chờ khớp. Thanh cuối là lãi/lỗ "
                           "ròng trung bình mỗi lệnh (đúng số trong T5).")
            ledger = res.get("ledger_a")
            if ledger is not None and ledger["status"].eq("filled").any():
                st.markdown("#### Lãi/lỗ cộng dồn của backtest A (dữ liệu lịch sử)")
                st.altair_chart(pnl_chart(ledger, mode, t), width="stretch", theme=None)
                st.caption("Mỗi điểm là một lệnh đã khớp (0,1 BTC). Đường đi xuống từng bậc: lệnh nào cũng lỗ.")
            t5b = res.get("t5b")
            if t5b is not None:
                fs = fee_scenarios(t5b)
                st.markdown("#### Kiểm tra độ nhạy chi phí (T5b)")
                st.markdown(f"Đổi phí (×1, ×0,5, ×0), độ trễ và quy mô lệnh: **{vn(fs['with_profit'])}/{vn(fs['n'])}** "
                            f"kịch bản còn tính phí có lệnh lãi ({vn(fs['positive'])}/{vn(fs['signals'])} lệnh). "
                            "Phí ×0 là cận trên lý thuyết (bỏ hẳn phí), không phải chi phí thật.")
                grid = t5b[(t5b["slippage_bps"] == 0) & (t5b["qty_btc"] == 0.1)].copy()
                if not grid.empty:
                    grid["Backtest"] = grid["backtest"].map(lambda b: "A · lịch sử (tập kiểm tra cuối)"
                                                            if b.startswith("A_") else "B · live")
                    grid["Loại thị trường"] = grid["market_type"].map(MARKET_NAME)
                    grid["Độ trễ"] = grid["latency"]
                    grid["fee"] = grid["fee_multiplier"].map(lambda f: f"Phí ×{vn(f, 1).rstrip('0').rstrip(',')}")
                    grid["cell"] = [f"{int(p)}/{int(n)}" for p, n in zip(grid["n_net_positive"], grid["n_signals"])]
                    wide = grid.pivot_table(index=["Backtest", "Loại thị trường", "Độ trễ"], columns="fee",
                                            values="cell", aggfunc="first")
                    wide = wide[[c for c in ("Phí ×1", "Phí ×0,5", "Phí ×0") if c in wide.columns]].reset_index()
                    st.dataframe(wide, hide_index=True)
                    st.caption("Mỗi ô: số lệnh có lãi / số tín hiệu (trượt giá 0 bps, 0,1 BTC). Backtest A ở bảng này "
                               "chạy một lần trên tập kiểm tra cuối với cấu hình đã đóng băng.")
        st.caption("Đây là mô phỏng nghiên cứu, không phải khuyến nghị giao dịch.")

    # Tải dữ liệu: đúng những gì đang hiển thị, không tính thêm số mới.
    with tab_data:
        st.markdown("#### Tải bảng đang xem về máy (CSV)")
        label = st.selectbox("Khoảng thời gian của bảng giá và độ lệch giá", list(DOWNLOAD_DAYS), key="days")
        cut = panel.index[-1] - pd.Timedelta(days=DOWNLOAD_DAYS[label])
        recent = panel.loc[panel.index > cut]
        disp = [s.loc[s.index > cut] for s in dispersions.values() if s is not None]
        left, right = st.columns(2)
        with left.container(border=True):
            st.markdown("**Giá và độ lệch giá**")
            st.download_button("Giá phút cuối của 6 thị trường", to_csv_bytes(quotes.set_index("venue")),
                               "bao_gia_cuoi.csv", "text/csv", key="dl_quotes")
            st.download_button(f"Bảng giá 1 phút, {label}", to_csv_bytes(recent), "panel_1m_wide_gan_nhat.csv",
                               "text/csv", key="dl_panel")
            if disp:
                st.download_button(f"Độ lệch giá, {label}", to_csv_bytes(pd.concat(disp, axis=1)),
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
        st.caption("File CSV giữ tên cột gốc của dự án (để dùng lại trong code). Muốn toàn bộ bảng giá thì lấy file "
                   "Parquet trên Drive; CSV ở đây chỉ để xem nhanh.")


if __name__ == "__main__":
    main()
