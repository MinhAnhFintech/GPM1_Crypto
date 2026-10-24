"""C2.06 (TV2) - Hình F3: rolling Information Share kèm dải cận dưới/cận trên theo từng venue.

Chạy từ gốc repo:
    python scripts/make_figure_f3.py

Đầu vào : outputs/tables/rolling_is.parquet (D6, TV5). Đã chạy trên mẫu phát triển và xác thực; không có mẫu kiểm tra cuối.
Đầu ra  : outputs/figures/F3_rolling_is_bounds.png (historical) hoặc outputs/synthetic/figures/ (synthetic).

Cách đọc: mỗi ô là một venue (hàng = spot/perp, cột = sàn). Dải tô là [is_lower, is_upper], đường là is_mid. Trục x là
window_end (UTC), tức thời điểm cửa sổ kết thúc. Cửa sổ có status khác "ok" để TRỐNG (không nội suy) và được đếm trong
chú thích. Các cửa sổ lịch sử có thể được công bố cùng lúc (available_at bằng thời điểm khóa đặc tả) nên trục x KHÔNG
dùng available_at; đường nét đứt đánh dấu thời điểm khóa đặc tả khi nó muộn hơn cửa sổ đầu tiên.
Nếu dải của các venue chồng nhau thì KHÔNG kết luận venue dẫn (xem leader_of trong dashboard); hình chỉ mô tả.
"""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.utils.io_utils import read_parquet
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind

COLORS = {"spot": "#3A6FD8", "perp": "#1B2A41"}
LINESTYLES = {"spot": "-", "perp": "--"}
SMOOTH_WINDOWS = 24  # trung vị trượt 24 cửa sổ; với bước 60 phút của TV5 là 1 ngày. Chỉ để dễ đọc, không dùng cho kết luận.
REQUIRED = ["window_start", "window_end", "available_at", "venue", "is_lower", "is_upper", "is_mid", "status"]


def _split(venue: str):
    exchange, market = venue.rsplit("_", 1)
    return exchange, market


def prepare(d6: pd.DataFrame) -> pd.DataFrame:
    """Giữ nguyên dòng; đặt is_lower/is_upper/is_mid = NaN ở cửa sổ có status khác 'ok' (để ngắt đường, không nội suy)."""
    missing = [c for c in REQUIRED if c not in d6.columns]
    if missing:
        raise ValueError(f"D6 thiếu cột {missing}")
    out = d6.copy()
    ok = out["status"].astype(str).str.lower().eq("ok")
    out.loc[~ok, ["is_lower", "is_upper", "is_mid"]] = np.nan
    out["ok"] = ok
    return out.sort_values(["venue", "window_end"]).reset_index(drop=True)


def rolling_median(part: pd.DataFrame, windows: int) -> pd.Series:
    """Trung vị trượt của is_mid theo thứ tự window_end; ô trống (status != ok) không được nội suy."""
    ordered = part.sort_values("window_end")
    return ordered["is_mid"].rolling(windows, min_periods=max(windows // 2, 1)).median().reindex(part.index)


def band_summary(frame: pd.DataFrame) -> dict:
    """Số cửa sổ ok / tổng và độ rộng trung vị của dải (is_upper - is_lower), cho từng venue."""
    result = {}
    for venue, part in frame.groupby("venue"):
        width = (part["is_upper"] - part["is_lower"]).dropna()
        result[venue] = {"ok": int(part["ok"].sum()), "total": int(len(part)),
                         "median_width": float(width.median()) if len(width) else float("nan")}
    return result


def lock_time(frame: pd.DataFrame):
    """Thời điểm khóa đặc tả = available_at nhỏ nhất, nếu muộn hơn window_end đầu tiên; ngược lại None."""
    first_end, first_available = frame["window_end"].min(), frame["available_at"].min()
    return first_available if first_available > first_end else None


def plot_rolling_is(d6: pd.DataFrame, kind: str, path) -> Path:
    """Vẽ lưới ô rolling IS kèm dải cận. Trục y chung 0 đến 1."""
    frame = prepare(d6)
    venues = sorted(frame["venue"].unique())
    exchanges = sorted({_split(v)[0] for v in venues})
    markets = [m for m in ("spot", "perp") if any(_split(v)[1] == m for v in venues)]
    summary = band_summary(frame)
    lock = lock_time(frame)
    fig, axes = plt.subplots(len(markets), len(exchanges), figsize=(5.0 * len(exchanges), 3.2 * len(markets) + 1.4),
                             sharex=True, sharey=True, squeeze=False)
    for r, market in enumerate(markets):
        for c, exchange in enumerate(exchanges):
            ax, venue = axes[r][c], f"{exchange}_{market}"
            part = frame[frame["venue"] == venue]
            if part.empty:
                ax.set_visible(False)
                continue
            x = part["window_end"]
            ax.fill_between(x, part["is_lower"], part["is_upper"], color=COLORS[market], alpha=0.25, linewidth=0)
            ax.plot(x, part["is_mid"], color=COLORS[market], linewidth=0.3, alpha=0.5)
            ax.plot(x, rolling_median(part, SMOOTH_WINDOWS), color=COLORS[market], linestyle=LINESTYLES[market], linewidth=1.8)
            if lock is not None:
                ax.axvline(lock, color="#888", linestyle=":", linewidth=0.9)
            s = summary[venue]
            ax.set_title(venue, fontsize=10)
            ax.annotate(f"median bound width = {s['median_width']:.2f}\nok windows: {s['ok']:,}/{s['total']:,}",
                        xy=(0.02, 0.04), xycoords="axes fraction", ha="left", va="bottom", fontsize=8,
                        bbox=dict(boxstyle="round", fc="white", ec="#ccc", alpha=0.85))
            ax.set_ylim(-0.02, 1.02)
            ax.grid(alpha=0.25)
            if c == 0:
                ax.set_ylabel("Information Share", fontsize=9)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    total, failed = len(frame), int((~frame["ok"]).sum())
    fig.suptitle(f"Rolling Information Share with bounds | {kind}", fontsize=13, y=0.995)
    note = (f"Band = [IS lower, IS upper]; thin line = midpoint, thick line = 1-day rolling median of midpoint; x = window end (UTC).\n{total // max(len(venues), 1):,} windows per venue; "
            f"{failed:,} venue-window rows with status != ok are left blank. ")
    if lock is not None:
        note += f"Dotted line: specification locked {lock:%Y-%m-%d %H:%M} UTC (left = dev, right = val)."
    fig.text(0.5, 0.004, note, ha="center", fontsize=8, color="#444")
    fig.autofmt_xdate()
    fig.tight_layout(rect=(0, 0.05, 1, 0.97))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description="C2.06 F3: rolling information share with bounds")
    parser.add_argument("--rolling", default=str(ROOT / "outputs/tables/rolling_is.parquet"))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    d6 = read_parquet(args.rolling, "D6")
    kind = data_kind(d6)
    default_out = ROOT / ("outputs/synthetic/figures" if kind == "synthetic" else "outputs/figures") / "F3_rolling_is_bounds.png"
    output_path = Path(args.out) if args.out else default_out
    validate_output_location(output_path, root=ROOT, mode="synthetic" if kind == "synthetic" else "historical")
    path = plot_rolling_is(d6, kind, output_path)
    print(f"Wrote {path}")
    return path


if __name__ == "__main__":
    main()