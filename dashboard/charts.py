"""Biểu đồ Plotly của dashboard: mỗi hàm nhận DataFrame (đã đọc từ file kết quả) và trả về Figure.

Không tính lại mô hình: chỉ gom nhóm và vẽ. Màu suy ra từ màu chủ đạo của giao diện (apply_palette) để mọi biểu đồ
cùng một tông: Spot = màu chủ đạo, nét liền; Perpetual futures = màu lân cận, nét đứt. Biểu đồ có 6 thị trường dùng
ba sắc độ cùng họ cho ba sàn và nét liền/đứt theo loại hợp đồng. Nhãn số luôn có dấu +/−, không chỉ dựa vào màu.
"""
import colorsys

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard import fmt

SERIES = {"Sáng": {"spot": "#2a78d6", "perp": "#eb6834"}, "Tối": {"spot": "#3987e5", "perp": "#d95926"}}
EXCHANGE_COLOR = {"Sáng": {"binance": "#D4A017", "okx": "#2B2B2B", "bybit": "#E8742C"},
                  "Tối": {"binance": "#E3B33A", "okx": "#D5D8DE", "bybit": "#F08A4B"}}
DASH = {"spot": "solid", "perp": "dash"}
GOOD, BAD = "#1f9e6e", "#d6453d"
MARKET_NAME = {"spot": "Spot", "perp": "Perpetual futures"}
EXCHANGE_NAME = {"binance": "Binance", "okx": "OKX", "bybit": "Bybit"}
FONT = "Be Vietnam Pro, sans-serif"


def _hls(hex_color: str, dh: float = 0.0, dl: float = 0.0, ds: float = 0.0) -> str:
    """Xoay sắc độ (dh, theo vòng 0–1) và chỉnh độ sáng/bão hòa của một màu hex."""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + dh) % 1, min(.85, max(.2, l + dl)), min(1, max(0, s + ds)))
    return "#" + "".join(f"{round(x * 255):02x}" for x in (r, g, b))


def apply_palette(mode: str, accent: str) -> dict:
    """Đặt màu biểu đồ theo màu chủ đạo: một họ màu lân cận (±50° sắc độ), sáng hơn ở chế độ tối.
    Trả về dict màu đã dùng (để CSS và chú thích dùng chung)."""
    global GOOD, BAD
    lift = 0.08 if mode == "Tối" else 0.0
    base = _hls(accent, 0, lift)
    warm, cool = _hls(accent, 0.14, lift + 0.04, 0.05), _hls(accent, -0.14, lift - 0.02, 0.0)
    SERIES[mode] = {"spot": base, "perp": warm}
    EXCHANGE_COLOR[mode] = {"binance": base, "okx": cool, "bybit": warm}
    GOOD, BAD = base, _hls("#e5484d", 0, lift)
    return dict(spot=base, perp=warm, cool=cool, good=GOOD, bad=BAD)
# Tên file khi bấm nút tải PNG của từng biểu đồ (mã hình của báo cáo nếu có).
PNG = {"F2": "F2_price_dispersion", "F3": "F3_rolling_is_bounds", "F4": "F4_gap_duration", "F5": "F5_cumulative_pnl",
       "F6": "F6_leadlag_xcorr", "T5": "T5_cost_waterfall", "T6b": "T6b_gap_by_regime", "IS": "IS_midpoint_over_time",
       "RQ1": "RQ1_leader_share", "VOL": "T1_volume_by_venue", "SHARE": "volume_share_daily",
       "PREM": "price_premium_by_venue", "PX": "exchange_price"}


def png_config(code: str) -> dict:
    """Cấu hình thanh công cụ Plotly: nút tải PNG đặt tên theo mã hình, bỏ các nút không cần."""
    return {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
            "toImageButtonOptions": {"format": "png", "filename": PNG.get(code, code), "scale": 2}}


def venue_style(venue: str, mode: str) -> dict:
    exchange, _, market = venue.rpartition("_")
    return dict(color=EXCHANGE_COLOR[mode].get(exchange, "#888"), dash=DASH.get(market, "solid"))


def venue_label(venue: str) -> str:
    exchange, _, market = venue.rpartition("_")
    return f"{EXCHANGE_NAME.get(exchange, exchange)} · {'Spot' if market == 'spot' else 'Perp'}"


def layout(fig: go.Figure, t: dict, height: int = 340, title: str | None = None, legend: bool = True) -> go.Figure:
    """Khung chung: nền trong suốt, lưới ngang nhạt, không lưới dọc, legend ngang phía trên, số kiểu Việt Nam."""
    fig.update_layout(
        height=height, margin=dict(l=8, r=12, t=48 if title else 30, b=8), separators=",.",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", hovermode="closest",
        font=dict(family=FONT, color=t["text"], size=13), showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=13)),
        hoverlabel=dict(bgcolor=t["surface"], font=dict(color=t["text"], family=FONT), bordercolor=t["border"]))
    if title:
        fig.update_layout(title=dict(text=title, x=0, font=dict(size=15)))
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=t["border"], tickfont=dict(color=t["muted"], size=12),
                     title_font=dict(color=t["muted"], size=12), automargin=True)
    fig.update_yaxes(gridcolor=t["border"], zeroline=False, linecolor="rgba(0,0,0,0)", automargin=True,
                     tickfont=dict(color=t["muted"], size=12), title_font=dict(color=t["muted"], size=12))
    return fig


def signed(x: float, digits: int = 2) -> str:
    """Nhãn có dấu +/− để không chỉ dựa vào màu."""
    if pd.isna(x):
        return "–"
    return ("+" if x > 0 else "") + fmt.num(x, digits)


# --------------------------------------------------------------------------- độ phân tán giá
def hourly_band(series: pd.Series) -> pd.DataFrame:
    """Trung vị và dải p10–p90 theo giờ của độ phân tán (chỉ gom nhóm để vẽ, không đổi số liệu)."""
    r = series.dropna().resample("1h")
    return pd.DataFrame({"median": r.median(), "p10": r.quantile(0.10), "p90": r.quantile(0.90)}).dropna()


def dispersion_fig(bands: dict, mode: str, t: dict, raw: bool = False) -> go.Figure:
    """Hai khung xếp dọc (spot trên, vĩnh cửu dưới) cùng trục tung. raw=True vẽ dữ liệu 1 phút."""
    markets = list(bands)
    fig = make_subplots(rows=len(markets), cols=1, shared_xaxes=True, vertical_spacing=0.08,
                        subplot_titles=[MARKET_NAME[m] for m in markets])
    top = 0.0
    for i, m in enumerate(markets, 1):
        data, color = bands[m], SERIES[mode][m]
        if raw:
            fig.add_trace(go.Scatter(x=data.index, y=data, mode="lines", name=MARKET_NAME[m], showlegend=False,
                                     line=dict(color=color, width=1.2, dash=DASH[m]),
                                     hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>%{y:,.2f} bps<extra></extra>"), i, 1)
            top = max(top, float(data.max()))
        else:
            fig.add_trace(go.Scatter(x=data.index, y=data["p90"], mode="lines", line=dict(width=0), showlegend=False,
                                     hoverinfo="skip"), i, 1)
            fig.add_trace(go.Scatter(x=data.index, y=data["p10"], mode="lines", line=dict(width=0), fill="tonexty",
                                     fillcolor=_alpha(color, 0.22), name="Dải p10–p90" if i == 1 else None,
                                     showlegend=False, hoverinfo="skip"), i, 1)
            fig.add_trace(go.Scatter(x=data.index, y=data["median"], mode="lines", name=MARKET_NAME[m],
                                     showlegend=False, line=dict(color=color, width=2, dash=DASH[m]),
                                     customdata=np.stack([data["p10"], data["p90"]], axis=-1),
                                     hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>Trung vị %{y:,.2f} bps<br>"
                                                   "p10–p90: %{customdata[0]:,.2f} – %{customdata[1]:,.2f} bps"
                                                   "<extra></extra>"), i, 1)
            top = max(top, float(data["p90"].max()))
    layout(fig, t, height=170 * len(markets) + 60, legend=False)
    fig.update_yaxes(range=[0, top * 1.08], title_text="bps", tickformat=",.1f")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=6)
    for a in fig.layout.annotations:
        a.update(x=0, xanchor="left", font=dict(size=13, color=t["text"]))
    return fig


# --------------------------------------------------------------------------- sàn dẫn giá
def is_time_fig(d6: pd.DataFrame, selected: str, mode: str, t: dict) -> go.Figure:
    """IS điểm giữa theo thời gian của 6 thị trường, trung bình theo ngày (cho dễ đọc; số liệu gốc theo giờ nằm trong
    rolling_is), dải mờ cận dưới–cận trên của thị trường đang chọn."""
    fig = go.Figure()
    daily = (d6.assign(day=d6["window_end"].dt.floor("D"))
             .groupby(["venue", "day"], as_index=False)[["is_lower", "is_upper", "is_mid"]].mean()
             .rename(columns={"day": "window_end"}))
    for venue in sorted(daily["venue"].unique()):
        g = daily[daily["venue"] == venue].sort_values("window_end")
        if venue == selected:
            col = venue_style(venue, mode)["color"]
            fig.add_trace(go.Scatter(x=g["window_end"], y=g["is_upper"], mode="lines", line=dict(width=0),
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=g["window_end"], y=g["is_lower"], mode="lines", line=dict(width=0),
                                     fill="tonexty", fillcolor=_alpha(col, 0.16), showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(
            x=g["window_end"], y=g["is_mid"], mode="lines", name=venue_label(venue),
            line=dict(width=2.6 if venue == selected else 1.4, **venue_style(venue, mode)),
            opacity=1 if venue == selected else 0.8,
            hovertemplate="%{x|%d/%m/%Y}<br>" + venue_label(venue) + ": IS điểm giữa trung bình ngày %{y:.1%}<extra></extra>"))
    layout(fig, t, height=400)
    fig.update_yaxes(range=[0, 1], tickformat=".0%", title_text="Information Share (điểm giữa)")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=7)
    return fig


def leader_share_fig(counts: dict, total: int, mode: str, t: dict) -> go.Figure:
    """Một thanh 100%: số cửa sổ dẫn giá theo từng thị trường + 'chưa phân biệt được'."""
    fig = go.Figure()
    undecided = total - sum(counts.values())
    items = [(venue_label(v), n, venue_style(v, mode)["color"]) for v, n in counts.items() if n]
    items.append(("Chưa phân biệt được", undecided, "#aeb4bd"))
    for name, n, color in items:
        if n <= 0:
            continue
        fig.add_trace(go.Bar(y=[""], x=[n / total], orientation="h", name=name, marker_color=color,
                             text=[f"{name}: {fmt.pct(n / total, 1)}"] if n / total > 0.06 else None,
                             textposition="inside", insidetextanchor="middle",
                             textfont=dict(color="#1B2A41" if name.startswith("Chưa") else "#fff", size=14),
                             hovertemplate=f"{name}<br>{fmt.num(n)} / {fmt.num(total)} cửa sổ<extra></extra>"))
    layout(fig, t, height=130)
    fig.update_layout(barmode="stack", margin=dict(l=8, r=12, t=40, b=24))
    fig.update_xaxes(range=[0, 1], tickformat=".0%")
    fig.update_yaxes(showgrid=False, showticklabels=False)
    return fig


def bounds_fig(window: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """Khoảng IS (cận dưới–cận trên) của 6 thị trường trong một cửa sổ; vạch đậm là điểm giữa."""
    data = window.sort_values("is_mid")
    fig = go.Figure()
    for market in ("spot", "perp"):
        g = data[data["venue"].str.endswith("_" + market)]
        if g.empty:
            continue
        fig.add_trace(go.Bar(
            y=[venue_label(v) for v in g["venue"]], x=(g["is_upper"] - g["is_lower"]), base=g["is_lower"],
            orientation="h", name=MARKET_NAME[market], marker_color=_alpha(SERIES[mode][market], 0.55),
            customdata=np.stack([g["is_lower"], g["is_upper"], g["is_mid"]], axis=-1),
            hovertemplate="%{y}<br>Cận dưới %{customdata[0]:.1%} · cận trên %{customdata[1]:.1%}"
                          "<br>Điểm giữa %{customdata[2]:.1%}<extra></extra>"))
    fig.add_trace(go.Scatter(y=[venue_label(v) for v in data["venue"]], x=data["is_mid"], mode="markers",
                             name="Điểm giữa", marker=dict(symbol="line-ns", size=18, line=dict(width=3, color=t["text"])),
                             hoverinfo="skip"))
    layout(fig, t, height=max(230, 48 * len(data) + 70))
    fig.update_layout(barmode="overlay")
    fig.update_xaxes(range=[0, 1], tickformat=".0%", title_text="Information Share: khoảng từ cận dưới đến cận trên")
    fig.update_yaxes(showgrid=False)
    return fig


def leadlag_fig(xcorr: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """Tương quan lợi suất ở độ trễ −5…+5 phút cho các cặp perp–spot cùng sàn, bỏ độ trễ 0 (xem riêng).
    Quy ước của file: độ trễ dương = thị trường a (perp) dẫn thị trường b (spot)."""
    fig = go.Figure()
    for exchange in ("binance", "okx", "bybit"):
        g = xcorr[(xcorr["venue_a"] == f"{exchange}_perp") & (xcorr["venue_b"] == f"{exchange}_spot")]
        g = g[g["lag"] != 0].sort_values("lag")
        if g.empty:
            continue
        fig.add_trace(go.Scatter(x=g["lag"], y=g["correlation"], mode="lines+markers", name=EXCHANGE_NAME[exchange],
                                 line=dict(color=EXCHANGE_COLOR[mode][exchange], width=2),
                                 marker=dict(size=7),
                                 hovertemplate=f"{EXCHANGE_NAME[exchange]}: độ trễ %{{x}} phút<br>"
                                               "tương quan %{y:.4f}<extra></extra>"))
    layout(fig, t, height=320)
    fig.add_hline(y=0, line=dict(color=t["muted"], dash="dot", width=1))
    fig.update_xaxes(title_text="Độ trễ (phút): dương = perp dẫn trước spot", dtick=1, showgrid=False)
    fig.update_yaxes(title_text="Tương quan lợi suất", tickformat=".3f")
    return fig


# --------------------------------------------------------------------------- chênh lệch giá
def ccdf_fig(ccdf: pd.DataFrame, mode: str, t: dict, step_s: int) -> go.Figure:
    """F4: tỷ lệ sự kiện kéo dài ít nhất x giây, thang log–log, spot và perp tách màu."""
    fig = go.Figure()
    for market in ("spot", "perp"):
        g = ccdf[ccdf["market_type"] == market]
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g["duration_s"], y=g["share"], mode="lines+markers", name=MARKET_NAME[market], line_shape="hv",
            line=dict(color=SERIES[mode][market], width=2.2, dash=DASH[market]), marker=dict(size=5),
            hovertemplate="%{x:,.0f} giây trở lên: %{y:.2%} số sự kiện<extra>" + MARKET_NAME[market] + "</extra>"))
    layout(fig, t, height=360)
    fig.update_xaxes(type="log", title_text="Kéo dài ít nhất (giây, thang log)", tickvals=[10, 100, 1000, 10000],
                     ticktext=["10", "100", "1.000", "10.000"], showgrid=True, gridcolor=t["border"])
    fig.update_yaxes(type="log", title_text="Tỷ lệ sự kiện (thang log)", tickvals=[1e-4, 1e-3, 1e-2, 1e-1, 1],
                     ticktext=["0,01%", "0,1%", "1%", "10%", "100%"])
    # Trục log: hình dạng dùng giá trị thật, còn chú thích dùng log10 của giá trị.
    fig.add_shape(type="line", xref="x", yref="paper", x0=step_s, x1=step_s, y0=0, y1=1,
                  line=dict(color=t["muted"], dash="dot", width=1))
    fig.add_annotation(x=np.log10(step_s), y=0.02, xref="x", yref="paper", text=f"1 quan sát = {fmt.num(step_s)} giây",
                       showarrow=False, xanchor="left", font=dict(size=11, color=t["muted"]))
    return fig


def waterfall_fig(data: pd.DataFrame, t: dict, height: int = 280, compact: bool = False) -> go.Figure:
    """Thác chi phí trung bình mỗi lệnh (bps): gap lúc quyết định trừ dần từng khoản ra lãi/lỗ ròng."""
    colors = {"gap": GOOD, "cost": BAD, "net": t["text"]}
    short = {"Chênh lệch lúc ra quyết định": "Chênh lệch ban đầu", "− Phí taker hai chân": "− Phí",
             "− Nửa chênh lệch mua–bán": "− Mua–bán", "− Trượt giá": "− Trượt giá",
             "− Giá biến động trong lúc chờ khớp": "− Giá biến động", "= Lãi/lỗ ròng mỗi lệnh": "= Lãi/lỗ ròng"}
    steps = data["step"].map(short).fillna(data["step"]) if compact else data["step"]
    fig = go.Figure(go.Bar(
        y=steps, x=data["end"] - data["start"], base=data["start"], orientation="h",
        marker_color=[colors[k] for k in data["kind"]],
        text=[("0 bps" if abs(v) < 5e-3 else signed(v, 2 if abs(v) < 1 else 1) + " bps") for v in data["value"]],
        textposition="outside", cliponaxis=False, textfont=dict(size=12 if compact else 13, color=t["text"]),
        hovertemplate="%{y}: %{text}<extra></extra>"))
    layout(fig, t, height=height, legend=False)
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(title_text=None if compact else "bps mỗi lệnh (trung bình)", showgrid=False, zeroline=True,
                     zerolinecolor=t["muted"], tickformat=",.0f")
    lo, hi = min(0, data[["start", "end"]].min().min()), max(0, data[["start", "end"]].max().max())
    # chừa chỗ cho nhãn số ngoài thanh (bản nhỏ ở trang chủ chừa nhiều hơn vì nhãn trục tung dài)
    fig.update_xaxes(range=[lo * (2.6 if compact else 1.6) - (10 if compact else 4), hi * 1.6 + 6])
    return fig


def pnl_fig(pnl: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """F5: lãi/lỗ cộng dồn (USDT) theo thời điểm khớp lệnh, dạng bậc thang, từng loại thị trường."""
    fig = go.Figure()
    for market in ("spot", "perp"):
        g = pnl[pnl["market_type"] == market].sort_values("ts_fill")
        if g.empty:
            continue
        fig.add_trace(go.Scatter(
            x=g["ts_fill"], y=g["cum_pnl_usdt"], mode="lines+markers", name=MARKET_NAME[market], line_shape="hv",
            line=dict(color=SERIES[mode][market], width=2.2, dash=DASH[market]), marker=dict(size=7),
            customdata=g["pnl_usdt"],
            hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>Lệnh này %{customdata:,.2f} USDT<br>Cộng dồn %{y:,.2f} USDT"
                          "<extra>" + MARKET_NAME[market] + "</extra>"))
    layout(fig, t, height=330)
    fig.add_hline(y=0, line=dict(color=t["muted"], dash="dot", width=1), annotation_text="0 (hòa vốn)",
                  annotation_position="top left", annotation_font=dict(size=11, color=t["muted"]))
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=6, tickangle=0)
    fig.update_yaxes(title_text="Lãi/lỗ cộng dồn (USDT)", tickformat=",.0f")
    return fig


def t6b_fig(regime: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """T6b: tốc độ xuất hiện chênh lệch giá theo chế độ so với mức tham chiếu (vạch tại 1,0).
    regime: cột label, market_type, ratio (chỉ các mức khác mức tham chiếu)."""
    fig = go.Figure()
    for market in ("spot", "perp"):
        g = regime[regime["market_type"] == market]
        if g.empty:
            continue
        fig.add_trace(go.Bar(y=g["label"], x=g["ratio"], orientation="h", name=MARKET_NAME[market],
                             marker_color=SERIES[mode][market], text=["×" + fmt.num(v, 2) for v in g["ratio"]],
                             textposition="outside", cliponaxis=False, textfont=dict(color=t["text"], size=12),
                             hovertemplate="%{y}: gấp %{x:,.2f} lần mức tham chiếu<extra>" + MARKET_NAME[market]
                                           + "</extra>"))
    layout(fig, t, height=60 * regime["label"].nunique() + 120)
    fig.update_layout(barmode="group")
    fig.add_vline(x=1, line=dict(color=t["text"], width=1.5))
    fig.add_annotation(x=1, y=1, xref="x", yref="paper", xanchor="left", xshift=6, yanchor="top", showarrow=False,
                       text="1,0 = bằng mức tham chiếu", font=dict(size=11, color=t["muted"]))
    fig.update_xaxes(title_text="Số sự kiện mỗi 1.000 phút so với mức tham chiếu (lần)", rangemode="tozero",
                     showgrid=True, gridcolor=t["border"], tickformat=",.1f")
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


def _alpha(hex_color: str, a: float) -> str:
    rgb = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{a})"


# --------------------------------------------------------------------------- các sàn và so sánh
def volume_bar_fig(t1: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """Khối lượng giao dịch trung bình mỗi ngày (USDT) của 6 thị trường; màu theo sàn, perp có sọc chéo."""
    data = t1.sort_values("avg_daily_volume_usdt")
    fig = go.Figure(go.Bar(
        y=[venue_label(v) for v in data["venue"]], x=data["avg_daily_volume_usdt"], orientation="h",
        marker=dict(color=[venue_style(v, mode)["color"] for v in data["venue"]],
                    pattern=dict(shape=["/" if v.endswith("_perp") else "" for v in data["venue"]],
                                 fgcolor="rgba(255,255,255,.6)", size=7)),
        text=[fmt.usdt_big(v) + " USDT" for v in data["avg_daily_volume_usdt"]], textposition="outside",
        cliponaxis=False, textfont=dict(color=t["text"], size=12), customdata=data["avg_daily_volume_btc"],
        hovertemplate="%{y}<br>%{x:,.0f} USDT mỗi ngày<br>%{customdata:,.0f} BTC mỗi ngày<extra></extra>"))
    layout(fig, t, height=320, legend=False)
    fig.update_xaxes(showticklabels=False, range=[0, data["avg_daily_volume_usdt"].max() * 1.28],
                     title_text="KL trung bình mỗi ngày (USDT) · sọc chéo = perp")
    fig.update_yaxes(showgrid=False)
    return fig


def volume_share_fig(daily: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """Thị phần khối lượng (BTC) theo ngày của 6 thị trường, cộng lại 100% (vùng xếp chồng)."""
    share = daily.div(daily.sum(axis=1), axis=0).mul(100).dropna(how="all")
    fig = go.Figure()
    order = sorted(share.columns, key=lambda v: (v.endswith("_spot"), v))
    for venue in order:
        style = venue_style(venue, mode)
        light = venue.endswith("_spot")
        fig.add_trace(go.Scatter(
            x=share.index, y=share[venue], mode="lines", stackgroup="one", name=venue_label(venue),
            line=dict(width=0.8, color=style["color"]), fillcolor=_alpha(style["color"], 0.35 if light else 0.8),
            hovertemplate="%{x|%d/%m/%Y}<br>" + venue_label(venue) + ": %{y:,.1f}% khối lượng<extra></extra>"))
    layout(fig, t, height=380)
    fig.update_layout(margin=dict(t=86))
    fig.update_yaxes(range=[0, 100], ticksuffix="%", title_text="Thị phần khối lượng (BTC)")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=5, tickangle=0)
    return fig


def premium_fig(premiums: dict, mode: str, t: dict) -> go.Figure:
    """Giá từng sàn cao/thấp hơn trung bình các sàn CÙNG LOẠI bao nhiêu bps (trung vị theo giờ)."""
    markets = list(premiums)
    fig = make_subplots(rows=len(markets), cols=1, shared_xaxes=True, vertical_spacing=0.1,
                        subplot_titles=[MARKET_NAME[m] for m in markets])
    for i, m in enumerate(markets, 1):
        for venue in premiums[m].columns:
            s = premiums[m][venue]
            fig.add_trace(go.Scatter(x=s.index, y=s, mode="lines", name=venue_label(venue), showlegend=True,
                                     line=dict(width=1.6, **venue_style(venue, mode)),
                                     hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>" + venue_label(venue)
                                                   + ": %{y:+,.2f} bps so với trung bình<extra></extra>"), i, 1)
        fig.add_hline(y=0, line=dict(color=t["muted"], dash="dot", width=1), row=i, col=1)
    layout(fig, t, height=200 * len(markets) + 110)
    fig.update_layout(margin=dict(t=90), legend=dict(y=1.1))
    fig.update_yaxes(title_text="bps", tickformat="+,.1f")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=6)
    for a in fig.layout.annotations:
        a.update(x=0, xanchor="left", font=dict(size=13, color=t["text"]))
    return fig


def exchange_price_fig(prices: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """Giá đóng cửa theo giờ của spot và perp trên cùng một sàn (USDT)."""
    fig = go.Figure()
    for market in ("spot", "perp"):
        if market in prices:
            fig.add_trace(go.Scatter(x=prices.index, y=prices[market], mode="lines", name=MARKET_NAME[market],
                                     line=dict(color=SERIES[mode][market], width=1.8, dash=DASH[market]),
                                     hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>%{y:,.1f} USDT<extra>"
                                                   + MARKET_NAME[market] + "</extra>"))
    layout(fig, t, height=320)
    fig.update_yaxes(title_text="Giá đóng cửa (USDT)", tickformat=",.0f")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=6)
    return fig


def prices_fig(prices: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """F1: giá đóng cửa đồng bộ (theo giờ) của 6 thị trường; màu theo sàn, nét đứt là perp."""
    fig = go.Figure()
    for venue in sorted(prices.columns, key=lambda v: (v.endswith("_perp"), v)):
        fig.add_trace(go.Scatter(x=prices.index, y=prices[venue], mode="lines", name=venue_label(venue),
                                 line=dict(width=1.5, **venue_style(venue, mode)),
                                 hovertemplate="%{x|%d/%m/%Y %H:%M} UTC<br>" + venue_label(venue)
                                               + ": %{y:,.1f} USDT<extra></extra>"))
    layout(fig, t, height=360)
    fig.update_layout(hovermode="x unified", margin=dict(t=60))
    fig.update_yaxes(title_text="Giá đóng cửa (USDT)", tickformat=",.0f")
    fig.update_xaxes(tickformat="%d/%m/%Y", nticks=6)
    return fig


T3C_REQUIRED = ["split", "exchange", "is_lower_spot", "is_upper_spot", "is_mid_spot", "ils_spot",
                "is_lower_perp", "is_upper_perp", "is_mid_perp", "ils_perp"]
SPLIT_NAME = {"dev": "Tập phát triển", "val": "Tập xác thực"}
CONCLUSION = {"chua_phan_biet_duoc": "chưa phân biệt được", "spot_dan": "Spot dẫn", "perp_dan": "Perp dẫn"}


def t3c_findings(t3c: pd.DataFrame) -> dict:
    """Rút kết luận từ T3c (không gõ tay): các khoảng IS có chồng nhau không, bên nào có ILS lớn hơn ở mỗi tập,
    sàn nào đổi phía giữa hai tập. Khoảng IS 'chồng nhau' khi cả hai khoảng cùng chứa điểm giữa của bên kia."""
    out = {"overlap_all": True, "flips": [], "steady": [], "ils": {}}
    for ex, g in t3c.groupby("exchange", sort=False):
        g = g.set_index("split")
        for _, r in g.iterrows():
            if not (r["is_lower_spot"] < r["is_upper_perp"] and r["is_lower_perp"] < r["is_upper_spot"]):
                out["overlap_all"] = False
        side = {sp: ("spot" if r["ils_spot"] > r["ils_perp"] else "perp") for sp, r in g.iterrows()}
        out["ils"][ex] = {sp: float(g.loc[sp, "ils_spot"]) for sp in g.index}
        first = side.get("dev", next(iter(side.values())))   # phía dẫn ở tập phát triển
        (out["flips"] if len(set(side.values())) > 1 else out["steady"]).append((ex, first))
    return out


def t3c_fig(t3c: pd.DataFrame, mode: str, t: dict) -> go.Figure:
    """F7 vẽ lại: mỗi sàn một cột. Hàng trên: khoảng IS (cận dưới–cận trên) và điểm giữa của Spot và Perp.
    Hàng dưới: ILS của Spot và Perp (cộng lại bằng 1), đường 0,5 = không bên nào dẫn."""
    exchanges = [e for e in ("binance", "okx", "bybit") if e in set(t3c["exchange"])]

    def title(ex):
        words = {CONCLUSION.get(str(c), str(c)) for c in t3c.loc[t3c["exchange"] == ex, "conclusion"]}             if "conclusion" in t3c else set()
        return EXCHANGE_NAME.get(ex, ex) + (f" · {', '.join(sorted(words))}" if words else "")

    titles = [title(e) for e in exchanges]
    fig = make_subplots(rows=2, cols=len(exchanges), shared_yaxes=True, vertical_spacing=0.16, horizontal_spacing=0.04,
                        subplot_titles=titles)
    for j, ex in enumerate(exchanges, 1):
        g = t3c[t3c["exchange"] == ex].copy()
        g["x"] = g["split"].map(SPLIT_NAME).fillna(g["split"])
        for m in ("spot", "perp"):
            col, show = SERIES[mode][m], j == 1
            fig.add_trace(go.Bar(
                x=g["x"], y=g[f"is_upper_{m}"] - g[f"is_lower_{m}"], base=g[f"is_lower_{m}"], name=MARKET_NAME[m],
                marker=dict(color=_alpha(col, 0.35), line=dict(color=col, width=1.5)), offsetgroup=m,
                legendgroup=m, showlegend=show, width=0.32,
                customdata=np.stack([g[f"is_lower_{m}"], g[f"is_upper_{m}"], g[f"is_mid_{m}"]], axis=-1),
                hovertemplate=f"{EXCHANGE_NAME.get(ex, ex)} · {MARKET_NAME[m]} · %{{x}}<br>IS từ %{{customdata[0]:.0%}} "
                              "đến %{customdata[1]:.0%}, điểm giữa %{customdata[2]:.0%}<extra></extra>"), 1, j)
            fig.add_trace(go.Bar(
                x=g["x"], y=g[f"ils_{m}"], name=MARKET_NAME[m], marker_color=col, offsetgroup=m, legendgroup=m,
                showlegend=False, width=0.32, text=[fmt.num(v, 2) for v in g[f"ils_{m}"]], textposition="outside",
                cliponaxis=False, textfont=dict(color=t["text"], size=12),
                hovertemplate=f"{EXCHANGE_NAME.get(ex, ex)} · {MARKET_NAME[m]} · %{{x}}<br>ILS %{{y:.2f}}<extra></extra>"), 2, j)
        fig.add_hline(y=0.5, line=dict(color=t["muted"], dash="dot", width=1), row=2, col=j)
        spot = g.set_index("split")["ils_spot"]
        if {"dev", "val"} <= set(spot.index):
            flip = (spot["dev"] - 0.5) * (spot["val"] - 0.5) < 0
            fig.add_annotation(row=2, col=j, xref="x domain", yref="y domain", x=0.5, y=1.0, yanchor="bottom",
                               showarrow=False, font=dict(size=11, color=BAD if flip else t["muted"]),
                               text=f"ILS Spot {fmt.num(spot['dev'], 2)} → {fmt.num(spot['val'], 2)}"
                                    + (" · đổi phía 0,5" if flip else ""))
    layout(fig, t, height=560)
    fig.update_layout(barmode="group", bargap=0.35, margin=dict(t=100), legend=dict(y=1.14))
    fig.update_yaxes(range=[0, 1.05], tickformat=".0%", row=1, col=1, title_text="Khoảng IS")
    fig.update_yaxes(range=[0, 1.18], tickformat=".1f", row=2, col=1, title_text="ILS (Spot + Perp = 1)")
    for a in fig.layout.annotations:
        if a.text in titles:
            a.update(font=dict(size=14, color=t["text"]))
    return fig
