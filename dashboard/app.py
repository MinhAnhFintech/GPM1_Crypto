"""C2.07 (TV2) - Dashboard: bảy khối nghiên cứu chia thành các thẻ, chạy trên dữ liệu giả lập.

Chạy từ gốc repo:
    python -m streamlit run dashboard/app.py

Đầu vào (đường dẫn sửa được ở thanh bên):
    D3  data/processed/panel_1m_wide.parquet        bảng rộng 1 phút (index ts_available, UTC)
    D6  outputs/tables/rolling_is.parquet           rolling IS/CS/ILS (tùy chọn, TV5)
    D7  outputs/tables/gap_events_historical.parquet sự kiện gap (tùy chọn, TV6)
Chọn rõ file fixtures/synthetic hoặc historical/live; không tự chuyển nguồn khi thiếu file.

Quy ước: giá USDT; gap theo bps; thời gian UTC; mỗi venue = {exchange}_{market_type}.
Chỉ hiển thị cái đã có: ô nào chưa có nguồn thì ghi "chờ", KHÔNG tự tính hay bịa số.
Chỉ ghép cặp cùng market_type. funding_rate không nằm trong net_gap_bps.
Phần giao diện (màu, sáng/tối, hiệu ứng) chỉ đổi cách hiển thị, không đổi số liệu.
Mô phỏng nghiên cứu, không khuyến nghị giao dịch.
"""
from pathlib import Path
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
import streamlit.components.v1 as components

from src.utils.io_utils import read_parquet
from src.utils.schema import data_kind, venues_from_panel, validate_frame, VENUE_PATTERN

PANEL_DEFAULT = ROOT / "data/processed/panel_1m_wide.parquet"
D6_DEFAULT = ROOT / "outputs/tables/rolling_is.parquet"
D7_DEFAULT = ROOT / "outputs/tables/gap_events_historical.parquet"

D6_COLS = ["window_start", "window_end", "available_at", "venue", "is_lower", "is_upper", "is_mid",
           "component_share", "ils", "leader", "status", "reason"]
D6_TIME = ["window_start", "window_end", "available_at"]
D7_COLS = ["event_id", "ts_start", "ts_end", "duration_s", "market_type", "buy_exchange", "sell_exchange",
           "gross_bps", "net_bps", "regime"]
D7_TIME = ["ts_start", "ts_end"]
MAX_POINTS = 5000      # số điểm tối đa vẽ mỗi đường (giữ cực đại mỗi nhóm để không mất đỉnh)
RECENT_EVENTS = 20
DOWNLOAD_DAYS = {"1 ngày gần nhất": 1, "7 ngày gần nhất": 7, "30 ngày gần nhất": 30}

PRESETS = {"Xanh dương": "#3A6FD8", "Bạc hà": "#1E9E8A", "Xanh lá": "#4F9A45", "Vàng bơ": "#C9961A",
           "Cam Bitcoin": "#F7931A", "Đào": "#E08246", "Hồng phấn": "#D85C86", "Tím oải hương": "#7C60CF"}
CUSTOM = "Tùy chỉnh"
# tints = tỷ lệ pha màu nền / thẻ / viền với màu chủ đạo (tông pastel như bảng màu Chrome)
MODES = {
    "Sáng": dict(text="#1B2A41", muted="#5B6B82", base="#FFFFFF", tints=(0.90, 0.96, 0.76), invert="none"),
    "Tối": dict(text="#EEF1F8", muted="#9FAAC0", base="#0B0F19", tints=(0.90, 0.84, 0.70),
                invert="invert(0.92) hue-rotate(180deg)"),
}


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


# --------------------------------------------------------------------------- giao diện (chỉ hiển thị)
ASSETS = Path(__file__).resolve().parent / "assets"
FONTS = ("https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700"
         "&family=Playfair+Display:wght@700;800&display=swap")
CURSORS = ["Đồng Bitcoin", "Đồng Tether", "Ethereum", "Nến giá", "Mặc định"]
TRANSITIONS = {"Đẩy sang": "push", "Mờ dần": "fade", "Lật sách": "flip", "Thu phóng": "zoom",
               "Quét ngang": "wipe", "Mở cửa": "doors", "Không chuyển": None}
# (ảnh, kiểu): big = ảnh chìm lớn cỡ nửa màn hình; float = thẻ ảnh nổi 3D nhỏ
WATERMARKS = {"quote": ("dark.avif", "big"), "disp": ("coin_btc.avif", "big"), "lead": ("bit.avif", "big"),
              "gap": ("cyber.avif", "big"), "data": ("data.avif", "float"),}
FONT_STACKS = {"Times New Roman": "'Times New Roman',Times,serif", "Georgia": "Georgia,serif",
               "Cambria": "Cambria,Georgia,serif", "Arial": "Arial,Helvetica,sans-serif",
               "Calibri": "Calibri,Carlito,Arial,sans-serif", "Verdana": "Verdana,sans-serif",
               "Tahoma": "Tahoma,sans-serif", "DM Sans": "'DM Sans',sans-serif", "Courier New": "'Courier New',monospace"}
SIZES_PT = [10, 11, 12, 13, 14, 16, 18, 20]     # cỡ chữ như Word; 12 pt = 16 px (mặc định)
RANGES = {"1 ngày": 1, "7 ngày": 7, "30 ngày": 30, "Toàn bộ": None}
RQS = [
    "Sàn nào đóng góp nhiều nhất vào efficient price của BTC?",
    "Price leadership có phụ thuộc volume, spread và depth không?",
    "Spot hay perpetual futures dẫn giá?",
    "Cross-exchange price gaps tồn tại bao lâu?",
    "Sau phí, spread, slippage và latency proxy, bao nhiêu “arbitrage signals” còn có lợi nhuận?",
]
INTRO = ("Dự án so sánh giá Bitcoin (BTC/USDT, nến 1 phút) trên 6 venue: Binance, OKX và Bybit, mỗi sàn có cả spot "
         "và perpetual. Nhóm đo sàn nào dẫn dắt giá chung bằng Information Share, Component Share và Information "
         "Leadership Share, rồi kiểm tra chênh lệch giá còn lãi hay không sau phí, spread và độ trễ. "
         "Đây là mô phỏng nghiên cứu, không phải khuyến nghị giao dịch.")


@st.cache_resource(show_spinner=False)
def asset_uri(name: str) -> str:
    path = ASSETS / name

    if not path.exists():
        return ""

    ext = path.suffix.lower()

    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".avif": "image/avif",
    }.get(ext, "application/octet-stream")

    encoded = base64.b64encode(path.read_bytes()).decode()

    return f"data:{mime};base64,{encoded}"


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
              font: str = "Times New Roman", size_pt: int = 12) -> str:
    """CSS: font, màu pastel, thẻ dạng viên thuốc, hero, hình chìm; hiệu ứng (con trỏ, chuyển tab) chỉ khi effects=True."""
    t = palette(mode, accent)
    css = """@import url('__FONTS__');
html{font-size:__PX__px}
html,body,[data-testid="stAppViewContainer"],[data-testid="stSidebar"],button,input,select,
.stApp p,.stApp label,.stApp li,.stApp [data-testid="stMetricValue"]{font-family:__BODYFONT__!important}
.brand{display:flex;align-items:center;gap:12px;margin:0 0 4px;font-family:'Playfair Display',Georgia,serif;font-weight:700;font-size:1.4rem;color:__TEXT__}
.block-container{padding-top:1.4rem!important}
.stApp h1,.stApp h2,.stApp h3,.hero h1{font-family:'Playfair Display',Georgia,serif!important;letter-spacing:-.01em}
.stApp{background:__BG__;color:__TEXT__}
[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"]{background:__SURFACE__;border-right:1px solid __BORDER__}
.stApp h2,.stApp h3,.stApp label,.stApp p,.stApp li,.stApp [data-testid="stMetricValue"],.stApp [data-testid="stAlert"] *{color:__TEXT__}
.stApp [data-testid="stCaptionContainer"],.stApp [data-testid="stMetricLabel"] *{color:__MUTED__}
[data-baseweb="input"],[data-baseweb="select"]>div{background:__SURFACE__!important;border-radius:10px}
[data-baseweb="input"] input{color:__TEXT__!important}
[data-testid="stMetric"]{background:__SURFACE__;border:1px solid __BORDER__;border-left:4px solid __ACCENT__;border-radius:12px;padding:10px 14px}
[data-testid="stDataFrame"]{filter:__INVERT__;border:1px solid __BORDER__;border-radius:10px}
[data-baseweb="tab-list"]{gap:8px}
button[data-baseweb="tab"]{border-radius:999px;padding:6px 18px;background:__SURFACE__;border:1px solid __BORDER__;color:__MUTED__}
button[data-baseweb="tab"] p{color:inherit!important;font-weight:500}
button[data-baseweb="tab"][aria-selected="true"]{background:__ACCENT__;border-color:__ACCENT__;color:#fff}
[data-baseweb="tab-highlight"],[data-baseweb="tab-border"]{display:none}
.stDownloadButton button{background:__ACCENT__;border:0;border-radius:999px}
.stDownloadButton button p{color:#fff!important}
.stDownloadButton button:hover{background:__ACCENT_DARK__}
[role="tabpanel"]{position:relative;isolation:isolate;padding-top:16px}
.wm{position:absolute;right:0;top:0;width:58%;height:min(100%,680px);min-height:440px;background-size:cover;background-position:center;
 opacity:.22;pointer-events:none;z-index:-1;
 -webkit-mask-image:linear-gradient(to left,#000 30%,transparent);mask-image:linear-gradient(to left,#000 30%,transparent)}
.fl{position:absolute;right:8px;top:0;width:92px;height:92px;border-radius:22px;background-size:cover;background-position:center;pointer-events:none;z-index:2;
 box-shadow:0 18px 28px -8px rgba(0,0,0,.4),0 3px 8px rgba(0,0,0,.2),inset 0 0 0 3px rgba(255,255,255,.6);
 transform:perspective(600px) rotateY(-18deg) rotateX(8deg) rotateZ(3deg)}
@media (max-width:800px){.wm{width:100%;opacity:.12}}
.hero{position:relative;min-height:460px;padding:48px 0 20px;overflow:hidden;border-radius:20px}
.hero-art{position:absolute;inset:0 0 0 36%;background-size:cover;background-position:center;
 -webkit-mask-image:linear-gradient(to right,transparent 0,#000 48%);mask-image:linear-gradient(to right,transparent 0,#000 48%)}
.hero-body{position:relative;max-width:min(740px,100%);padding:0 40px}
.hero h1{font-weight:800;font-size:clamp(2.5rem,5.6vw,4.6rem);line-height:1.02;margin:0 0 16px;padding:0!important;color:__TEXT__}
.hero .sub{font-size:1.15rem;font-style:italic;max-width:48ch;color:__MUTED__;margin-bottom:14px}
.hero .intro{max-width:62ch;line-height:1.65;color:__TEXT__;margin-bottom:16px}
.hero .rq{list-style:none;margin:0;padding:0;display:grid;gap:8px;max-width:64ch}
.hero .rq li{background:__SURFACE__e8;border:1px solid __BORDER__;border-radius:12px;padding:8px 14px;line-height:1.4}
.hero .rq b{color:__ACCENT__;margin-right:8px}
@media (max-width:800px){.hero-art{inset:0;opacity:.22}.hero-body{padding:0 12px}}
"""
    for key, value in {"__FONTS__": FONTS, "__BG__": t["bg"], "__SURFACE__": t["surface"], "__TEXT__": t["text"],
                       "__MUTED__": t["muted"], "__BORDER__": t["border"], "__INVERT__": t["invert"],
                       "__ACCENT__": accent, "__ACCENT_DARK__": _shade(accent, 0.7),
                       "__BODYFONT__": FONT_STACKS.get(font, FONT_STACKS["Times New Roman"]),
                       "__PX__": f"{size_pt * 4 / 3:.1f}"}.items():
        css = css.replace(key, value)
    if effects:
        if cursor in CURSORS and cursor != "Mặc định":
            css += f'html,body,.stApp,.stApp *{{cursor:url("data:image/svg+xml,{quote(cursor_svg(cursor))}") 16 16, auto !important}}'
        css += ("@keyframes fl-bob{0%,100%{transform:perspective(600px) rotateY(-18deg) rotateX(8deg) rotateZ(3deg) translateY(0)}"
                "50%{transform:perspective(600px) rotateY(-18deg) rotateX(8deg) rotateZ(3deg) translateY(-9px)}}"
                ".fl{animation:fl-bob 4.5s ease-in-out infinite}")
        kind = TRANSITIONS.get(transition)
        if kind:
            css += TRANSITION_CSS.replace("__K__", kind)
    return f"<style>{css}</style>"


def hero_html() -> str:
    """Trang chủ: tên đề tài lớn, câu hỏi nghiên cứu RQ1 đến RQ5 và đoạn giới thiệu."""
    art = f"background-image:url({asset_uri('hero_coins.avif')})" if asset_uri("hero_coins.avif") else ""
    rqs = "".join(f"<li><b>RQ{i}</b>{q}</li>" for i, q in enumerate(RQS, 1))
    return (f"<div class='hero'><div class='hero-art' style=\"{art}\"></div><div class='hero-body'>"
            "<h1>Crypto Price Discovery and Cross-Exchange Arbitrage</h1>"
            "<div class='sub'>Khám phá nơi hình thành giá và kiểm định cơ hội arbitrage sau chi phí giao dịch "
            "trên nhiều sàn crypto</div>"
            f"<div class='intro'>{INTRO}</div><ol class='rq'>{rqs}</ol></div></div>")


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
<div style="display:flex;justify-content:space-between;align-items:center;padding:0 4px 6px"><div id="lg" style="font-size:13px"></div>
<div style="display:flex;align-items:center;gap:8px"><span style="font-size:12px;color:__MUTED__">Cuộn chuột: thu phóng · Kéo: lướt · Bấm đúp: đặt lại</span>
<button id="rs" style="background:__SURFACE__;color:__TEXT__;border:1px solid __BORDER__;border-radius:999px;padding:5px 14px;cursor:pointer;font-size:13px">Đặt lại</button>
<button id="rp" style="background:__ACCENT__;color:#fff;border:0;border-radius:999px;padding:5px 14px;cursor:pointer;font-size:13px">Chạy lại</button></div></div>
<div id="b" style="position:relative;height:360px"></div>
<div id="tp" style="position:fixed;display:none;pointer-events:none;background:__SURFACE__;border:1px solid __BORDER__;border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 6px 16px rgba(0,0,0,.2)"></div>
<script>
const D=__DATA__,C=__COLS__,ANIM=__ANIM__,H=360,P={l:50,r:44,t:14,b:30},MINSPAN=5*60000;
const N=Object.keys(D),box=document.getElementById('b'),tp=document.getElementById('tp');
document.getElementById('lg').innerHTML=N.map((n,k)=>'<span style="margin-right:14px"><i style="display:inline-block;width:18px;height:3px;background:'+C[k]+';vertical-align:middle;margin-right:6px"></i>'+n+'</span>').join('');
const A=[].concat(...N.map(n=>D[n])).filter(p=>p[1]!==null),x0=Math.min(...A.map(p=>p[0])),x1=Math.max(...A.map(p=>p[0]));
let v0=x0,v1=x1,lastW=0,drag=null;
const lb=(a,t)=>{let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;a[m][0]<t?lo=m+1:hi=m}return lo};
const vis=a=>a.slice(Math.max(0,lb(a,v0)-1),Math.min(a.length,lb(a,v1+1)+1));
const near=(a,t)=>{let lo=0,hi=a.length-1;while(hi-lo>1){const m=(lo+hi)>>1;a[m][0]<=t?lo=m:hi=m}return a[hi][0]<=t?a[hi]:a[lo][0]<=t?a[lo]:null};
const fmt=t=>new Date(t).toISOString().slice(5,16).replace('T',' ');
function clampView(){const span=Math.max(MINSPAN,Math.min(v1-v0,x1-x0));
 if(v0<x0){v0=x0;v1=x0+span}if(v1>x1){v1=x1;v0=x1-span}v0=Math.max(x0,v0);v1=Math.min(x1,v1)}
function draw(play){
 const W=box.clientWidth;if(!W){lastW=0;return}
 const shown=N.map(n=>vis(D[n])),vals=[].concat(...shown).filter(p=>p[1]!==null&&p[0]>=v0&&p[0]<=v1).map(p=>p[1]);
 let y1=vals.length?Math.max(...vals)*1.05:1;const y0=0;if(!(y1>y0))y1=1;
 const sx=t=>P.l+(t-v0)/((v1-v0)||1)*(W-P.l-P.r),sy=v=>H-P.b-(v-y0)/(y1-y0)*(H-P.t-P.b);
 let s='<svg width="'+W+'" height="'+H+'"><defs><clipPath id="cp"><rect x="'+P.l+'" y="0" width="'+(W-P.l-P.r)+'" height="'+H+'"/></clipPath></defs>';
 for(let i=0;i<=4;i++){const v=y0+(y1-y0)*i/4,y=sy(v);s+='<line x1="'+P.l+'" x2="'+(W-P.r)+'" y1="'+y+'" y2="'+y+'" stroke="__BORDER__"/><text x="'+(P.l-6)+'" y="'+(y+4)+'" text-anchor="end" font-size="11" fill="__MUTED__">'+v.toFixed(1)+'</text>'}
 for(let i=0;i<=5;i++){const t=v0+(v1-v0)*i/5;s+='<text x="'+sx(t)+'" y="'+(H-8)+'" text-anchor="'+(i==5?'end':'middle')+'" font-size="11" fill="__MUTED__">'+fmt(t)+'</text>'}
 s+='<g clip-path="url(#cp)">';
 N.forEach((n,k)=>{let pen=false;const d=shown[k].map(p=>{if(p[1]===null){pen=false;return ''}const cmd=pen?'L':'M';pen=true;return cmd+sx(p[0]).toFixed(1)+' '+sy(p[1]).toFixed(1)}).join('');
  s+='<path class="ln" d="'+d+'" fill="none" stroke="'+C[k]+'" stroke-width="1.8" stroke-linejoin="round"/>'});
 s+='</g><line id="cx" y1="'+P.t+'" y2="'+(H-P.b)+'" stroke="__MUTED__" stroke-dasharray="3 3" style="display:none"/><rect id="ov" x="'+P.l+'" y="'+P.t+'" width="'+(W-P.l-P.r)+'" height="'+(H-P.t-P.b)+'" fill="transparent" style="cursor:grab"/></svg>';
 box.innerHTML=s;
 const run=ANIM&&(play||lastW===0);lastW=W;
 if(run)box.querySelectorAll('.ln').forEach((p,i)=>{const L=p.getTotalLength();p.style.strokeDasharray=L;p.style.strokeDashoffset=L;p.getBoundingClientRect();
  p.style.transition='stroke-dashoffset 1.8s cubic-bezier(.4,0,.2,1) '+(i*.3)+'s';p.style.strokeDashoffset=0});
 const ov=box.querySelector('#ov'),cx=box.querySelector('#cx');
 ov.onmousemove=e=>{if(drag)return;const x=e.clientX-box.getBoundingClientRect().left,t=v0+(x-P.l)/(W-P.l-P.r)*(v1-v0);
  cx.setAttribute('x1',x);cx.setAttribute('x2',x);cx.style.display='';
  tp.innerHTML='<b>'+fmt(t)+' UTC</b>'+N.map((n,k)=>{const q=near(D[n],t);return '<br><span style="color:'+C[k]+'">&#9632;</span> '+n+': '+(q&&q[1]!==null?q[1].toFixed(2)+' bps ('+fmt(q[0])+' UTC)':'thiếu dữ liệu')}).join('');
  tp.style.display='block';tp.style.left=Math.min(e.clientX+14,innerWidth-170)+'px';tp.style.top=(e.clientY+10)+'px'};
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


def wm_html(key: str) -> str:
    """Ảnh minh họa của từng tab: 'big' = ảnh chìm lớn cỡ nửa màn hình, 'float' = thẻ ảnh nổi 3D nhỏ."""
    name, kind = WATERMARKS[key]
    uri = asset_uri(name)
    if not uri:
        return ""
    return f"<div class='{'wm' if kind == 'big' else 'fl'}' style=\"background-image:url({uri})\"></div>"


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


def anim_chart_html(lines: dict, colors: list, animate: bool, t: dict) -> str:
    """Đồ thị đường tự vẽ khi mở thẻ: có hover xem giá trị, nút Chạy lại. lines = {tên: Series index thời gian}."""
    data = {}
    for name, ser in lines.items():
        idx = ser.index.tz_localize("UTC") if ser.index.tz is None else ser.index
        data[name] = [[int(ts.timestamp() * 1000), None if pd.isna(v) else round(float(v), 3)]
                      for ts, v in zip(idx, ser.to_numpy())]
    html = CHART_HTML
    for key, value in {"__DATA__": json.dumps(data), "__COLS__": json.dumps(colors[:len(data)]),
                       "__ANIM__": "true" if animate else "false", "__TEXT__": t["text"], "__MUTED__": t["muted"],
                       "__BORDER__": t["border"], "__SURFACE__": t["surface"], "__ACCENT__": colors[0]}.items():
        html = html.replace(key, value)
    return html


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


GLOSSARY = {
    "venue": "Nơi giao dịch, ghi theo dạng sàn_loại hợp đồng. Ví dụ binance_spot là Binance giao ngay, binance_perp là Binance hợp đồng vĩnh viễn.",
    "market_type": "Loại thị trường: spot (giao ngay) hoặc perp (hợp đồng vĩnh viễn). Chỉ so sánh các venue cùng loại.",
    "close_usdt": "Giá đóng cửa của nến 1 phút ở phút cuối của panel, tính bằng USDT.",
    "missing": "Có giá hay không ở phút cuối, False là có giá bình thường. True là sàn không có giá ở phút này (ô close_usdt để trống).",
    "ts_grid_utc": "Mốc thời gian (UTC) trên lưới 5 giây mà quote này được gán vào.",
    "bid": "Giá mua cao nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "ask": "Giá bán thấp nhất đang chờ trong sổ lệnh, tính bằng USDT.",
    "spread_bps": "Chênh lệch giữa giá bán và giá mua so với giá giữa, tính bằng bps. 1 bps = 0,01%. Càng nhỏ thì chi phí vào lệnh càng thấp.",
    "spread_bps_median": "Trung vị của spread_bps trên toàn bộ mẫu live (một giá trị đại diện cho cả mẫu, không phải quote cuối).",
    "n_quotes": "Số quote live của venue này trong mẫu.",
    "depth_bid_10bps_usdt": "Tổng khối lượng mua đang chờ trong khoảng 10 bps dưới giá giữa, tính bằng USDT. Càng lớn thì lệnh lớn càng ít làm trượt giá.",
    "depth_ask_10bps_usdt": "Tổng khối lượng bán đang chờ trong khoảng 10 bps trên giá giữa, tính bằng USDT.",
    "is_lower": "Cận dưới của Information Share (phần venue này đóng góp vào việc hình thành giá chung), từ 0 đến 1. Cận được tính qua mọi thứ tự xếp các venue.",
    "is_upper": "Cận trên của Information Share, từ 0 đến 1. Khoảng từ is_lower đến is_upper càng rộng thì càng khó nói venue này đóng góp bao nhiêu.",
    "is_mid": "Trung điểm của hai cận (is_lower và is_upper). Chỉ dùng để xếp thứ tự tham khảo, không phải kết luận.",
    "component_share": "Component Share: phần đóng góp của venue vào thành phần giá chung, tính theo tốc độ venue điều chỉnh về cân bằng dài hạn. Không bị ép vào khoảng 0 đến 1 nên có thể âm hoặc lớn hơn 1; tổng các venue trong một cửa sổ bằng 1.",
    "ils": "Information Leadership Share: kết hợp Information Share và Component Share để tách venue phản ứng nhanh khỏi venue ít nhiễu. Ở đây là trung bình theo từng cặp venue nên cộng các dòng lại không bằng 1.",
    "status": "ok nghĩa là mô hình ước lượng được cho cửa sổ này. Giá trị khác ok là cửa sổ lỗi; xem cột reason.",
    "reason": "Lý do của kết luận hoặc của lỗi. overlapping_bounds nghĩa là các khoảng Information Share của các venue chồng lên nhau nên chưa phân biệt được venue nào dẫn giá.",
    "su_kien": "Số sự kiện gap trong nhóm này. Một sự kiện gom các phút liên tiếp mà chênh lệch giá vượt ngưỡng.",
    "trung_vi_bps": "Trung vị của gap trong nhóm, tính bằng bps (một nửa số sự kiện có gap lớn hơn số này).",
    "thieu": "Số sự kiện chưa có giá trị gap (không được tính vào trung vị).",
    "ts_start": "Thời điểm (UTC) sự kiện gap bắt đầu.",
    "ts_end": "Thời điểm (UTC) sự kiện gap kết thúc.",
    "duration_s": "Thời gian sự kiện tồn tại, tính bằng giây.",
    "buy_exchange": "Sàn mua trong cặp (chân rẻ hơn).",
    "sell_exchange": "Sàn bán trong cặp (chân đắt hơn).",
    "gross_bps": "Chênh lệch giá giữa chân bán và chân mua, trước mọi chi phí, tính bằng bps.",
    "net_bps": "Gap sau khi trừ phí taker hai chân, spread, slippage và chi phí khác (ước tính theo cấu hình của TV6), tính bằng bps. Dương nghĩa là còn lãi trong mô phỏng.",
}
QUOTE_COLS = ["venue", "market_type", "close_usdt", "missing"]
LIVE_SHOWN_COLS = ["venue", "market_type", "ts_grid_utc", "bid", "ask", "spread_bps",
                   "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "spread_bps_median", "n_quotes"]
RANK_COLS = ["venue", "is_lower", "is_upper", "is_mid", "component_share", "ils", "status", "reason"]
GAP_SUMMARY_COLS = ["market_type", "su_kien", "trung_vi_bps", "thieu"]
GAP_EVENT_COLS = ["ts_start", "ts_end", "duration_s", "market_type", "buy_exchange", "sell_exchange", "gross_bps"]


def col_config(cols):
    """Tooltip cho tên cột (rê chuột lên tiêu đề cột). Chỉ thêm chữ giải thích, tên cột và số liệu giữ nguyên."""
    return {c: st.column_config.Column(help=GLOSSARY[c]) for c in cols if c in GLOSSARY}


def explain(cols, title="Giải thích các cột"):
    """Khung mở ra liệt kê ý nghĩa từng cột của bảng ngay phía trên."""
    with st.expander(title):
        st.markdown("\n".join(f"- **{c}**: {GLOSSARY[c]}" for c in cols if c in GLOSSARY))


def _default_path(contract: Path) -> str:
    """Không tự chuyển provenance khi thiếu file; người dùng chọn nguồn rõ ràng."""
    return str(contract)


# --------------------------------------------------------------------------- giao diện
def main():
    st.set_page_config(page_title="Crypto price discovery", layout="wide")

    # Ba ô đường dẫn phải là text_input đầu tiên ở thanh bên (test dựa vào thứ tự này).
    source = st.sidebar.text_input("Panel Parquet (D3)", str(PANEL_DEFAULT))
    d6_path = st.sidebar.text_input("rolling_is (D6, tùy chọn)", _default_path(D6_DEFAULT))
    d7_path = st.sidebar.text_input("gap_events (D7, tùy chọn)", _default_path(D7_DEFAULT))
    live_path = st.sidebar.text_input("live_quotes_long (tùy chọn)", str(LIVE_DEFAULT))

    st.sidebar.markdown("**Giao diện**")
    mode = st.sidebar.radio("Chế độ", list(MODES), horizontal=True, key="mode")
    preset = st.sidebar.selectbox("Bảng màu", list(PRESETS) + [CUSTOM], key="preset")
    accent = (st.sidebar.color_picker("Màu chủ đạo", "#F7931A", key="accent") if preset == CUSTOM
              else PRESETS[preset])
    effects = st.sidebar.toggle("Hiệu ứng (con trỏ, chuyển tab)", value=True, key="effects")
    cursor = st.sidebar.selectbox("Con trỏ chuột", CURSORS, key="cursor")
    transition = st.sidebar.selectbox("Kiểu chuyển tab", list(TRANSITIONS), key="transition")
    font = st.sidebar.selectbox("Kiểu chữ nội dung", list(FONT_STACKS), key="font")
    size_pt = st.sidebar.selectbox("Cỡ chữ (pt)", SIZES_PT, index=SIZES_PT.index(12), key="size")
    st.markdown(theme_css(mode, accent, effects, cursor, transition, font, size_pt), unsafe_allow_html=True)
    st.markdown(f"<div class='brand'>{cursor_svg('Đồng Bitcoin', 30)}"
                "<span>Crypto Price Discovery and Cross-Exchange Arbitrage</span></div>", unsafe_allow_html=True)
    components.html(transition_js(), height=0)

    try:
        panel = _load_panel(source, _mtime(source))
    except (OSError, ValueError) as exc:
        st.error(f"Không đọc được panel: {exc}")
        return
    if panel.empty:
        st.warning("Panel rỗng: không có dòng nào để hiển thị.")
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

    tab_home, tab_quote, tab_disp, tab_lead, tab_gap, tab_data = st.tabs(
        ["Trang chủ", "Báo giá", "Độ phân tán", "Venue dẫn giá", "Gap và arbitrage", "Tải dữ liệu"])
    with tab_home:
        st.markdown(hero_html(), unsafe_allow_html=True)
        st.info(f"data_kind = {kind} | {len(venues)} venue | quan sát cuối: {asof:%Y-%m-%d %H:%M} UTC")
        st.caption("Mô phỏng nghiên cứu, không khuyến nghị giao dịch. Giá USDT, gap bps, thời gian UTC.")

    # 1. Báo giá
    last = panel.iloc[-1]
    quotes = pd.DataFrame([
        {"venue": v, "market_type": v.rsplit("_", 1)[1], "close_usdt": last[f"close__{v}"],
         "missing": last[f"missing__{v}"] if f"missing__{v}" in panel.columns else None}
        for v in venues])
    with tab_quote:
        st.markdown(wm_html("quote"), unsafe_allow_html=True)
        st.subheader("1. Báo giá theo venue")
        c1, c2, c3 = st.columns(3)
        c1.metric("Số venue", len(venues))
        c2.metric("Số phút trong panel", f"{len(panel):,}")
        c3.metric("Quan sát cuối (UTC)", f"{asof:%d/%m %H:%M}")
        st.dataframe(quotes, hide_index=True, column_config=col_config(QUOTE_COLS))
        explain(QUOTE_COLS)
        st.caption(f"close_usdt = giá đóng cửa nến 1 phút (USDT) của phút cuối panel; data_kind = {kind}; market_type tách spot và perp.")
        st.markdown("**Báo giá live gần nhất (mẫu ngắn)**")
        if live is None:
            st.write(live_msg)
        else:
            st.dataframe(live_latest(live), hide_index=True, column_config=col_config(LIVE_SHOWN_COLS))
            explain(LIVE_SHOWN_COLS)
            st.caption(f"data_kind = live | mẫu {live['ts_grid_utc'].min():%Y-%m-%d %H:%M} → "
                       f"{live['ts_grid_utc'].max():%Y-%m-%d %H:%M} UTC, {len(live):,} quote | bid, ask: USDT; "
                       "spread_bps: bps; depth: USDT trong 10 bps quanh giá giữa. Mẫu live là một giai đoạn ngắn và KHÁC "
                       "giai đoạn của panel, không so trực tiếp với giá đóng cửa ở trên.")

    # 2. Độ phân tán giá
    dispersions = {m: dispersion_bps(panel, venues, m) for m in ("spot", "perp")}
    with tab_disp:
        st.markdown(wm_html("disp"), unsafe_allow_html=True)
        st.subheader("2. Độ phân tán giá (bps)")
        choice = st.radio("Khoảng thời gian", list(RANGES) + ["Tùy chọn"], index=1, horizontal=True, key="range")
        custom = None
        if choice == "Tùy chọn":
            first, last_day = panel.index[0].date(), panel.index[-1].date()
            custom = st.date_input("Từ ngày đến ngày", (first, last_day), min_value=first, max_value=last_day, key="drange")
        mask = range_mask(panel.index, choice, custom)
        raw = {m: ser[mask] for m, ser in dispersions.items() if ser is not None}
        raw = {m: r for m, r in raw.items() if r.notna().any()}
        if not raw:
            st.write("Không có dữ liệu độ phân tán trong khoảng này (cần ít nhất hai venue cùng market_type).")
        else:
            for col, (market, r) in zip(st.columns(len(raw)), raw.items()):
                valid = r.dropna()
                col.metric(f"{market}: lần cuối có đủ giá (bps)", f"{valid.iloc[-1]:.2f}")
                col.caption(f"{valid.index[-1]:%Y-%m-%d %H:%M} UTC")
            pal = palette(mode, accent)
            components.html(anim_chart_html({m: thin(r) for m, r in raw.items()}, [accent, pal["text"]], effects, pal),
                            height=430)
            st.caption("Giữ đỉnh tại thời gian gốc; khoảng thiếu để ngắt đường. Hover ghi thời gian của điểm được giữ, không phải giá nội suy.")
            with st.expander("Độ phân tán là gì?"):
                st.markdown("Tại mỗi phút, lấy giá cao nhất trừ giá thấp nhất giữa các venue **cùng loại** (spot với spot, perp với perp), "
                            "chia cho giá trung bình rồi nhân 10.000 để ra bps (1 bps = 0,01%). Số càng lớn nghĩa là các sàn càng lệch giá nhau "
                            "ở phút đó. Phút nào thiếu giá của một venue trong nhóm thì để trống, không điền thay.")

    # 3 và 4. Venue dẫn, xếp hạng IS
    window = None
    if d6 is None:
        window_msg = d6_msg
    else:
        window = current_window(d6, asof)
        window_msg = None if window is not None else "Chưa có cửa sổ rolling nào sẵn sàng tại thời điểm này."
    with tab_lead:
        st.markdown(wm_html("lead"), unsafe_allow_html=True)
        st.caption(f"D6: {Path(d6_path).name} | data_kind={kind} | chỉ kết quả đã sẵn sàng đến {asof:%Y-%m-%d %H:%M} UTC")
        st.subheader("3. Venue dẫn hiện tại")
        if window is None:
            st.write(window_msg)
        else:
            leader, reason = leader_of(window)
            if leader:
                st.success(f"Venue dẫn: {leader}")
            else:
                st.warning(f"Chưa xác định venue dẫn: {reason}")
            st.caption(f"Cửa sổ {window['window_start'].iloc[0]:%Y-%m-%d %H:%M} → "
                       f"{window['window_end'].iloc[0]:%Y-%m-%d %H:%M} UTC; sẵn sàng lúc "
                       f"{window['available_at'].iloc[0]:%Y-%m-%d %H:%M} UTC. Chỉ công nhận leader khi bounds không chồng nhau.")
            stale = asof - window["window_end"].iloc[0]
            if stale > pd.Timedelta(days=1):
                st.warning(f"Đây là cửa sổ rolling mới nhất có trong D6, kết thúc {stale.days} ngày trước quan sát cuối của panel. "
                           "D6 chỉ phủ mẫu phát triển và xác thực; chưa có kết quả cho mẫu kiểm tra cuối nên không có cửa sổ mới hơn.")
        st.subheader("4. Xếp hạng rolling IS")
        if window is None:
            st.write(window_msg)
        else:
            st.caption("Mỗi dòng là một venue trong cửa sổ rolling mới nhất. Information Share cho biết venue đóng góp bao nhiêu "
                       "vào việc hình thành giá chung; khoảng [is_lower, is_upper] rộng và chồng lên nhau nghĩa là chưa phân biệt được venue dẫn.")
            st.dataframe(window.sort_values("is_mid", ascending=False)[RANK_COLS], hide_index=True,
                         column_config=col_config(RANK_COLS))
            explain(RANK_COLS)

    # 5, 6, 7. Gap
    with tab_gap:
        st.markdown(wm_html("gap"), unsafe_allow_html=True)
        st.caption(f"D7: {Path(d7_path).name} | data_kind={kind} | sự kiện trong mẫu và ts_end ≤ {asof:%Y-%m-%d %H:%M} UTC")
        for title in ("5. Gap gross (bps)", "6. Gap net ước tính (bps)", "7. Cờ arbitrage"):
            st.subheader(title)
            if d7 is None:
                st.write(d7_msg)
            elif d7.empty:
                st.write("Không có sự kiện gap nào trong dữ liệu này.")
            elif title.startswith("5."):
                st.dataframe(gap_summary(d7, "gross_bps"), hide_index=True, column_config=col_config(GAP_SUMMARY_COLS))
                st.caption(f"{RECENT_EVENTS} sự kiện gần nhất; chỉ ghép cùng market_type.")
                st.dataframe(d7.sort_values("ts_end", ascending=False).head(RECENT_EVENTS)[GAP_EVENT_COLS],
                             hide_index=True, column_config=col_config(GAP_EVENT_COLS))
                explain(GAP_SUMMARY_COLS + GAP_EVENT_COLS)
            elif title.startswith("6."):
                st.dataframe(gap_summary(d7, "net_bps"), hide_index=True, column_config=col_config(GAP_SUMMARY_COLS))
                explain(["net_bps", "trung_vi_bps"])
                st.caption("Net = gross − phí − slippage − chi phí khác (ước tính theo cấu hình của TV6). "
                           "Funding rate không nằm trong net.")
            else:
                positive = d7[d7["net_bps"] > 0]
                columns = st.columns(2)
                for column, market in zip(columns, ("spot", "perp")):
                    column.metric(f"{market}: sự kiện còn lãi sau chi phí", int((positive["market_type"] == market).sum()))
                if positive.empty:
                    st.write("Không có sự kiện nào còn lãi sau chi phí trong dữ liệu này.")
                st.caption("Sự kiện có net_bps thiếu không được tính. Đây là mô phỏng nghiên cứu, không phải khuyến nghị giao dịch.")

    # Tải dữ liệu: đúng những gì đang hiển thị, không tính thêm số mới.
    with tab_data:
        st.markdown(wm_html("data"), unsafe_allow_html=True)
        label = st.selectbox("Khoảng thời gian của panel và độ phân tán", list(DOWNLOAD_DAYS), key="days")
        cut = panel.index[-1] - pd.Timedelta(days=DOWNLOAD_DAYS[label])
        recent = panel.loc[panel.index > cut]
        disp = [s.loc[s.index > cut] for s in dispersions.values() if s is not None]
        left, right = st.columns(2)
        left.download_button("Tải báo giá cuối (CSV)", to_csv_bytes(quotes.set_index("venue")), "bao_gia_cuoi.csv",
                             "text/csv", key="dl_quotes")
        left.download_button(f"Tải panel {label} (CSV)", to_csv_bytes(recent), "panel_1m_wide_gan_nhat.csv",
                             "text/csv", key="dl_panel")
        if disp:
            left.download_button(f"Tải độ phân tán {label} (CSV)", to_csv_bytes(pd.concat(disp, axis=1)),
                                 "dispersion_bps.csv", "text/csv", key="dl_disp")
        if d6 is not None:
            right.download_button("Tải rolling IS (CSV)", to_csv_bytes(d6.set_index("window_start")),
                                  "rolling_is.csv", "text/csv", key="dl_d6")
        if d7 is not None:
            right.download_button("Tải sự kiện gap (CSV)", to_csv_bytes(d7.set_index("event_id")),
                                  "gap_events.csv", "text/csv", key="dl_d7")
        if d6 is None or d7 is None:
            st.caption("Nút tải rolling IS và gap chỉ hiện khi file tương ứng đã có.")
        st.caption("Muốn toàn bộ panel thì lấy file Parquet trên Drive; CSV ở đây chỉ để xem nhanh.")


if __name__ == "__main__":
    main()
