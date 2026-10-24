"""C2.06 (TV2) - Hình F7: spot so với perp trong cùng một sàn (RQ3), từ bảng T3c của TV5.

Chạy từ gốc repo:
    python scripts/make_figure_f7.py

Đầu vào : outputs/tables/T3c_spot_perp.csv (TV5; mỗi dòng = một sàn x một split dev/val; không có mẫu kiểm tra cuối).
Đầu ra  : outputs/figures/F7_spot_perp_is_ils.png

Cách đọc: hàng trên là Information Share. Thanh đứng là khoảng [is_lower, is_upper], chấm là is_mid, spot lệch trái,
perp lệch phải, mỗi sàn một ô, dev và val ở hai nhóm. Hàng dưới là ILS (Information Leadership Share, tổng spot + perp
của cùng sàn bằng 1), đường ngang 0,5 là mức không bên nào dẫn. Khi khoảng IS chồng nhau thì KHÔNG kết luận bên nào
dẫn; hình chỉ mô tả và ghi lại cột conclusion của TV5. Đơn vị: tỷ lệ không thứ nguyên trong [0, 1].
"""
from pathlib import Path
import argparse
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.utils.pipeline_stages import validate_output_location

COLORS = {"spot": "#3A6FD8", "perp": "#1B2A41"}
SPLITS = ("dev", "val")
REQUIRED = ["split", "exchange", "is_lower_spot", "is_upper_spot", "is_mid_spot", "ils_spot",
            "is_lower_perp", "is_upper_perp", "is_mid_perp", "ils_perp", "conclusion", "status"]


def load_t3c(path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    missing = [c for c in REQUIRED if c not in frame.columns]
    if missing:
        raise ValueError(f"T3c thiếu cột: {missing}")
    return frame


def overlap(row: pd.Series) -> bool:
    """True nếu khoảng IS của spot và perp chồng nhau (khi đó không có cơ sở nói bên nào dẫn)."""
    return bool(max(row["is_lower_spot"], row["is_lower_perp"]) <= min(row["is_upper_spot"], row["is_upper_perp"]))


def ils_flip(frame: pd.DataFrame) -> dict:
    """Với mỗi sàn: dấu của (ils_spot - 0,5) ở dev và val. Khác dấu nghĩa là kết luận đảo chiều giữa hai giai đoạn."""
    out = {}
    for exchange, part in frame.groupby("exchange"):
        by = part.set_index("split")
        if not all(s in by.index for s in SPLITS):
            continue
        signs = [np.sign(by.loc[s, "ils_spot"] - 0.5) for s in SPLITS]
        out[exchange] = {"dev": float(by.loc["dev", "ils_spot"]), "val": float(by.loc["val", "ils_spot"]),
                         "flip": bool(signs[0] != signs[1])}
    return out


def plot_spot_perp(frame: pd.DataFrame, path) -> Path:
    exchanges = sorted(frame["exchange"].unique())
    flips = ils_flip(frame)
    fig, axes = plt.subplots(2, len(exchanges), figsize=(4.6 * len(exchanges), 7.4), sharey="row", squeeze=False)
    offsets = {"spot": -0.12, "perp": 0.12}
    for c, exchange in enumerate(exchanges):
        top, bottom = axes[0][c], axes[1][c]
        for i, split in enumerate(SPLITS):
            row = frame[(frame["exchange"] == exchange) & (frame["split"] == split)]
            if row.empty:
                continue
            row = row.iloc[0]
            for market in ("spot", "perp"):
                x = i + offsets[market]
                top.vlines(x, row[f"is_lower_{market}"], row[f"is_upper_{market}"], color=COLORS[market], linewidth=6, alpha=0.35)
                top.plot(x, row[f"is_mid_{market}"], "o", color=COLORS[market], markersize=6,
                         label=market if (i == 0) else None)
                bottom.bar(x, row[f"ils_{market}"], width=0.2, color=COLORS[market], label=market if i == 0 else None)
                bottom.text(x, row[f"ils_{market}"] + 0.02, f"{row[f'ils_{market}']:.2f}", ha="center", fontsize=8)
            if not row["status"] == "ok":
                top.text(i, 0.5, f"status = {row['status']}", ha="center", fontsize=8, color="#a00")
        top.set_title(exchange, fontsize=11)
        top.set_ylim(-0.02, 1.02)
        bottom.set_ylim(0, 1.35)
        bottom.axhline(0.5, color="#888", linestyle=":", linewidth=0.9)
        for ax in (top, bottom):
            ax.set_xticks(range(len(SPLITS)))
            ax.set_xticklabels(SPLITS)
            ax.set_xlim(-0.6, len(SPLITS) - 0.4)
            ax.grid(alpha=0.25, axis="y")
        concl = ", ".join(sorted(set(frame[frame["exchange"] == exchange]["conclusion"].astype(str))))
        info = flips.get(exchange)
        text = f"TV5: {concl}"
        if info:
            text += f"\nILS spot dev → val: {info['dev']:.2f} → {info['val']:.2f}"
            if info["flip"]:
                text += " (đổi phía 0,5)"
        bottom.annotate(text, xy=(0.02, 0.97), xycoords="axes fraction", ha="left", va="top", fontsize=8,
                        bbox=dict(boxstyle="round", fc="white", ec="#ccc", alpha=0.9))
        if c == 0:
            top.set_ylabel("Information Share [lower, upper] và midpoint", fontsize=9)
            bottom.set_ylabel("ILS (spot + perp = 1)", fontsize=9)
            top.legend(loc="lower left", fontsize=8)
    fig.suptitle("Spot vs perp trong cùng sàn: IS và ILS, dev và val | historical", fontsize=13, y=0.995)
    fig.text(0.5, 0.005, "Thanh = khoảng IS, chấm = midpoint; hàng dưới là ILS, đường chấm = 0,5. Khoảng IS chồng nhau nên không xác định bên dẫn.",
             ha="center", fontsize=8, color="#444")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description="C2.06 F7: spot vs perp IS and ILS")
    parser.add_argument("--t3c", default=str(ROOT / "outputs/tables/T3c_spot_perp.csv"))
    parser.add_argument("--out", default=str(ROOT / "outputs/figures/F7_spot_perp_is_ils.png"))
    args = parser.parse_args(argv)
    validate_output_location(Path(args.out), root=ROOT, mode="historical")
    path = plot_spot_perp(load_t3c(args.t3c), args.out)
    print(f"Wrote {path}")
    return path


if __name__ == "__main__":
    main()