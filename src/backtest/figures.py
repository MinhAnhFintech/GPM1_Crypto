"""TV6 C6.08: F4 (how long gaps last) and F5 (cumulative simulated P&L).

Design (data-viz method): both plots have one axis and two series, so colour never carries identity alone.
  * identity = contract group: spot-spot (categorical slot 1, blue, solid line, circle) and perp-perp
    (slot 2, orange, dashed line, square). Both slots pass the palette validator in light and dark mode.
  * F4 form: complementary CDF P(duration >= x) on log-log axes (a heavy-tailed positive quantity, a
    histogram would hide the tail), one panel per data source because their time grids differ (60 s vs 5 s).
  * F5 form: step line of cumulative USDT P&L over the sample window, one panel per backtest.
  * text uses ink tokens, never the series colour; grid and axes are recessive; markers are >= 7 pt with a surface ring.
Every figure states its data_kind, sample and the fact that it is a simulation, not trading advice.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

THEMES = {
    "light": {"surface": "#fcfcfb", "primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
              "grid": "#e1e0d9", "axis": "#c3c2b7", "spot": "#2a78d6", "perp": "#eb6834"},
    "dark": {"surface": "#1a1a19", "primary": "#f0efec", "secondary": "#c3c2b7", "muted": "#898781",
             "grid": "#2c2c2a", "axis": "#383835", "spot": "#3987e5", "perp": "#d95926"},
}
GROUPS = {"spot": {"label": "spot–spot", "linestyle": "-", "marker": "o"},
          "perp": {"label": "perp–perp", "linestyle": "--", "marker": "s"}}
DISCLAIMER = "Mô phỏng nghiên cứu, không phải khuyến nghị giao dịch."


def _pyplot():
    """Writable matplotlib cache and a non-interactive backend (same convention as notebooks/templates)."""
    os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2]/"tmp/matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _style(ax, theme):
    t = THEMES[theme]
    ax.set_facecolor(t["surface"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(t["axis"])
    ax.tick_params(colors=t["muted"], labelsize=8.5, length=3)
    ax.grid(True, color=t["grid"], linewidth=0.6)
    ax.set_axisbelow(True)
    ax.xaxis.label.set_color(t["secondary"])
    ax.yaxis.label.set_color(t["secondary"])
    ax.title.set_color(t["primary"])


def _seconds(value, _pos=None):
    value = float(value)
    if value >= 3600:
        return f"{value/3600:g} giờ"
    if value >= 60:
        return f"{value/60:g} phút"
    return f"{value:g} s"


SECOND_TICKS = [5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200]


def _round_duration_ticks(ax, lo, hi):
    """Readable log ticks (5 s ... 2 h) that fall inside the data range."""
    ticks = [v for v in SECOND_TICKS if lo*0.8 <= v <= hi*1.25]
    ax.set_xticks(ticks)
    ax.set_xticks([], minor=True)


def _percent(value, _pos=None):
    value = float(value)*100
    return f"{value:g}%"


def ccdf(durations) -> pd.DataFrame:
    """Complementary CDF of event durations: for each distinct duration x, the share of events lasting >= x (0-1)."""
    values = np.sort(np.asarray(durations, dtype=float))
    if len(values) == 0:
        return pd.DataFrame({"duration_s": [], "share_at_least": []})
    unique = np.unique(values)
    share = 1.0 - np.searchsorted(values, unique, side="left")/len(values)
    return pd.DataFrame({"duration_s": unique, "share_at_least": share})


def _save(fig, out_path):
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160, facecolor=fig.get_facecolor())
    return output


def plot_gap_duration(sources: dict, threshold_bps: float, out_path, theme: str = "light"):
    """F4: how long do cross-venue gaps last? One panel per source, spot-spot and perp-perp apart.

    sources: {panel title: (events DataFrame in D7 form, grid step in seconds)}. Units: seconds; share 0-1.
    Time: durations are measured on the grid of each source (resolution = one grid step); events touching the
    start/end of a series are censored and are drawn as measured.
    """
    plt = _pyplot()
    t = THEMES[theme]
    fig, axes = plt.subplots(1, len(sources), figsize=(11.2, 4.9), sharey=True, layout="constrained")
    fig.set_facecolor(t["surface"])
    axes = np.atleast_1d(axes)
    summary = []
    for ax, (title, (events, grid_s)) in zip(axes, sources.items()):
        _style(ax, theme)
        kind = events.attrs.get("data_kind") or (events["data_kind"].iloc[0] if len(events) else "n/a")
        for market_type, spec in GROUPS.items():
            part = events[events["market_type"] == market_type]
            if part.empty:
                continue
            curve = ccdf(part["duration_s"])
            ax.plot(curve.duration_s, curve.share_at_least, color=t[market_type], linestyle=spec["linestyle"], linewidth=2,
                    marker=spec["marker"], markersize=7, markeredgecolor=t["surface"], markeredgewidth=1.5,
                    markevery=max(1, len(curve)//10),               # dense curves: a marker every few points, not on all
                    label=f"{spec['label']} (n = {len(part):,})".replace(",", " "))
            median = float(part["duration_s"].median())
            summary.append((title, spec["label"], len(part), median, float((part["n_obs"] == 1).mean()*100)))
        ax.set_xscale("log")
        ax.set_yscale("log")
        _round_duration_ticks(ax, float(events["duration_s"].min()), float(events["duration_s"].max()))
        ax.xaxis.set_major_formatter(plt.FuncFormatter(_seconds))
        ax.yaxis.set_major_formatter(plt.FuncFormatter(_percent))
        ax.set_title(f"{title}\ndata_kind = {kind} · bước lưới {grid_s:g} s", fontsize=10.5, loc="left")
        ax.set_xlabel("Thời gian gap tồn tại (log)")
        leg = ax.legend(loc="upper right", fontsize=8.5, frameon=False)
        for text in leg.get_texts():
            text.set_color(t["secondary"])
    axes[0].set_ylabel("Tỷ lệ sự kiện kéo dài ≥ x (thang log)")
    fig.suptitle(f"F4 · Gap giữa các sàn tồn tại bao lâu? (gross ≥ {threshold_bps:g} bps)", fontsize=12.5, color=t["primary"], x=0.01,
                 ha="left", fontweight="bold")
    fig.get_layout_engine().set(rect=(0, 0.05, 1, 0.95))             # reserve a strip for the caption
    fig.text(0.01, 0.012, "Độ phân giải bằng một bước lưới; sự kiện chạm đầu/cuối dữ liệu được vẽ như đã đo. " + DISCLAIMER,
             fontsize=8, color=t["muted"], ha="left", va="bottom")
    _save(fig, out_path)
    plt.close(fig)
    return summary


def cumulative_pnl(ledger: pd.DataFrame, market_type: str) -> pd.DataFrame:
    """Step series of cumulative filled P&L (USDT) for one group, ordered by fill time."""
    part = ledger[(ledger["market_type"] == market_type) & (ledger["status"] == "filled")].sort_values("ts_fill")
    return pd.DataFrame({"ts_fill": part["ts_fill"].to_numpy(), "pnl_usdt": part["pnl_usdt"].cumsum().to_numpy()})


def plot_cumulative_pnl(panels: dict, out_path, theme: str = "light"):
    """F5: cumulative simulated P&L (USDT) per backtest, spot-spot and perp-perp apart.

    panels: {panel title: (ledger in D8 form, (window start, window end), qty_btc)}. Units: USDT, UTC time.
    A group with no filled signal is named in the legend with n = 0 and has no line (nothing to draw).
    """
    plt = _pyplot()
    import matplotlib.dates as mdates
    t = THEMES[theme]
    fig, axes = plt.subplots(1, len(panels), figsize=(11.2, 4.9), layout="constrained")
    fig.set_facecolor(t["surface"])
    axes = np.atleast_1d(axes)
    totals = {}
    for ax, (title, (ledger, (start, end), qty)) in zip(axes, panels.items()):
        _style(ax, theme)
        kind = ledger["data_kind"].iloc[0] if len(ledger) else ledger.attrs.get("data_kind", "n/a")
        ax.axhline(0, color=t["axis"], linewidth=1)
        for market_type, spec in GROUPS.items():
            curve = cumulative_pnl(ledger, market_type)
            label = f"{spec['label']} (n = {len(curve)})"
            if curve.empty:
                ax.plot([], [], linestyle="none", label=label + ": không có lệnh")      # name it, draw nothing
                continue
            x = pd.concat([pd.Series([pd.Timestamp(start)]), curve["ts_fill"], pd.Series([pd.Timestamp(end)])], ignore_index=True)
            y = pd.concat([pd.Series([0.0]), curve["pnl_usdt"], pd.Series([curve["pnl_usdt"].iloc[-1]])], ignore_index=True)
            ax.step(x, y, where="post", color=t[market_type], linestyle=spec["linestyle"], linewidth=2, label=label)
            ax.plot(curve["ts_fill"], curve["pnl_usdt"], linestyle="none", color=t[market_type], marker=spec["marker"],
                    markersize=7, markeredgecolor=t["surface"], markeredgewidth=1.5)
            ax.annotate(f"{curve['pnl_usdt'].iloc[-1]:+.1f}".replace("-", "−"), (pd.Timestamp(end), curve["pnl_usdt"].iloc[-1]), xytext=(-4, 7),
                        textcoords="offset points", ha="right", fontsize=8.5, color=t["secondary"])
            totals[(title, spec["label"])] = float(curve["pnl_usdt"].iloc[-1])
        ax.set_xlim(pd.Timestamp(start), pd.Timestamp(end))
        if pd.Timestamp(end) - pd.Timestamp(start) <= pd.Timedelta(days=30):      # live sample: day/month
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
        else:                                                                   # historical sample: month/year
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%Y"))
        ax.set_title(f"{title}\ndata_kind = {kind} · mỗi lệnh {qty:g} BTC", fontsize=10.5, loc="left")
        ax.set_xlabel("Thời điểm khớp (UTC)")
        ax.set_ylabel("P&L tích lũy giả định (USDT)")
        leg = ax.legend(loc="lower left", fontsize=8.5, frameon=False)
        for text in leg.get_texts():
            text.set_color(t["secondary"])
    fig.suptitle("F5 · P&L tích lũy giả định sau phí taker và độ trễ", fontsize=12.5, color=t["primary"], x=0.01, ha="left",
                 fontweight="bold")
    fig.get_layout_engine().set(rect=(0, 0.05, 1, 0.95))
    fig.text(0.01, 0.012, "Hai backtest dùng mẫu và quy mô khác nhau nên không cộng gộp; số lệnh rất ít. " + DISCLAIMER,
             fontsize=8, color=t["muted"], ha="left", va="bottom")
    _save(fig, out_path)
    plt.close(fig)
    return totals
