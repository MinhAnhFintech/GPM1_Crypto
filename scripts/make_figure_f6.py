"""C2.06 (TV2) - Hình F6: tương quan chéo lead-lag của ret_1m giữa các cặp venue.

Chạy từ gốc repo:
    python scripts/make_figure_f6.py                          # toàn bộ mẫu (chỉ để kiểm tra code)
    python scripts/make_figure_f6.py --end 2026-03-01         # cắt mẫu: ts_available < end (UTC)

Đầu vào : data/processed/features_1m.parquet (D3b, cột ret_1m__{venue}); max_lag lấy từ config/config.yaml.
Đầu ra  : outputs/figures/F6_leadlag_xcorr.png (historical) hoặc outputs/synthetic/figures/ (synthetic).

Quy ước: corr(ret_A(t), ret_B(t+lag)), lag đo bằng phút (lưới 1 phút). Lag DƯƠNG nghĩa là venue A (tên đứng
trước trong tiêu đề từng ô) dẫn venue B. Mỗi cặp được xếp cố định: spot đứng trước perp, cùng loại thì theo thứ tự
chữ cái; đảo A và B chỉ đổi dấu lag, giữ nguyên tương quan. Giá trị thiếu giữ NaN, không điền, không nén lưới.
Cột lag 0 không vẽ (quá lớn so với các lag khác); rho(0) ghi trong từng ô. Trục y chung cho mọi ô.
Hình chỉ MÔ TẢ tương quan; ở lưới 1 phút tương quan cùng phút thường áp đảo nên không kết luận venue dẫn.
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
import yaml

from src.price_discovery.leadlag import lead_lag_xcorr
from src.utils.io_utils import read_parquet
from src.utils.schema import data_kind

PREFIX = "ret_1m__"
MARKET_RANK = {"spot": 0, "perp": 1}
GROUPS = [("Spot vs perpetual, same exchange", "cross"), ("Spot vs spot", "spot"), ("Perpetual vs perpetual", "perp")]
BASE_COLOR, PEAK_COLOR = "#9ecae1", "#1f77b4"


def _split(venue: str):
    exchange, market = venue.rsplit("_", 1)
    return exchange, market


def _order_key(venue: str):
    exchange, market = _split(venue)
    return MARKET_RANK[market], exchange


def orient_pairs(xcorr: pd.DataFrame) -> pd.DataFrame:
    """Xếp mỗi cặp theo thứ tự cố định (spot trước perp, rồi theo chữ cái). Đảo cặp thì lag đổi dấu
    (corr(A(t), B(t+k)) = corr(B(t+k), A(t))), tương quan giữ nguyên."""
    out = xcorr.drop(columns=["lag_convention"], errors="ignore").copy()
    swap = np.array([_order_key(a) > _order_key(b) for a, b in zip(out["venue_a"], out["venue_b"])], dtype=bool)
    a, b = out["venue_a"].to_numpy().copy(), out["venue_b"].to_numpy().copy()
    out["venue_a"], out["venue_b"] = np.where(swap, b, a), np.where(swap, a, b)
    for column in ("lag", "lag_s"):
        out[column] = np.where(swap, -out[column], out[column])
    return out.sort_values(["venue_a", "venue_b", "lag"]).reset_index(drop=True)


def select_pairs(oriented: pd.DataFrame) -> list:
    """[(tên nhóm, [(venue_a, venue_b), ...]), ...] theo GROUPS; bỏ nhóm không có cặp nào."""
    pairs = oriented[["venue_a", "venue_b"]].drop_duplicates().itertuples(index=False, name=None)
    buckets = {key: [] for _, key in GROUPS}
    for a, b in pairs:
        (ea, ma), (eb, mb) = _split(a), _split(b)
        if ma != mb:
            if ea == eb:
                buckets["cross"].append((a, b))
        else:
            buckets[ma].append((a, b))
    return [(title, buckets[key]) for title, key in GROUPS if buckets[key]]


def peak_lag(pair: pd.DataFrame):
    """Lag (phút) có tương quan lớn nhất; None nếu mọi giá trị đều NaN."""
    valid = pair.dropna(subset=["correlation"])
    return None if valid.empty else int(valid.loc[valid["correlation"].idxmax(), "lag"])


def lag_summary(pair: pd.DataFrame) -> str:
    """Chú thích mỗi ô: tương quan cùng phút rho(0) và độ bất đối xứng rho(+1) - rho(-1).
    Bất đối xứng dương nghĩa là venue đứng trước (A) dẫn nhẹ venue đứng sau (B)."""
    by_lag = pair.set_index("lag")["correlation"]
    text = f"rho(0) = {by_lag.get(0, np.nan):.3f}"
    if by_lag.get(1, np.nan) == by_lag.get(1, np.nan) and by_lag.get(-1, np.nan) == by_lag.get(-1, np.nan):
        text += f"\nrho(+1) - rho(-1) = {by_lag[1] - by_lag[-1]:+.3f}"
    return text


def plot_lead_lag(xcorr: pd.DataFrame, kind: str, sample: str, path) -> Path:
    """Vẽ lưới ô tương quan theo lag cho từng cặp (hàng = nhóm cặp). Trục y dùng chung để so sánh giữa các cặp."""
    oriented = orient_pairs(xcorr)
    groups = select_pairs(oriented)
    if not groups:
        raise ValueError("No venue pairs to plot")
    side_max = oriented.loc[oriented["lag"] != 0, "correlation"].abs().max()
    limit = max(float(side_max) * 1.8, 0.01) if side_max == side_max else 0.05
    nrows, ncols = len(groups), max(len(pairs) for _, pairs in groups)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.2 * ncols, 3.0 * nrows + 1.2), sharex=True, sharey=True, squeeze=False)
    for row, (title, pairs) in enumerate(groups):
        for col in range(ncols):
            ax = axes[row][col]
            if col >= len(pairs):
                ax.set_visible(False)
                continue
            a, b = pairs[col]
            pair = oriented[(oriented.venue_a == a) & (oriented.venue_b == b)]
            side = pair[pair["lag"] != 0]
            ax.bar(side["lag"], side["correlation"], color=BASE_COLOR, width=0.8)
            ax.axhline(0, color="#555", linewidth=0.6)
            ax.axvline(0, color="#999", linewidth=0.6, linestyle=":")
            ax.set_title(f"{a} → {b}", fontsize=10)
            ax.annotate(lag_summary(pair), xy=(0.5, 0.97), xycoords="axes fraction", ha="center", va="top", fontsize=8)
            ax.set_ylim(-limit, limit)
            ax.grid(alpha=0.25)
            if col == 0:
                ax.set_ylabel(f"{title}\nCorrelation", fontsize=9)
            if row == nrows - 1:
                ax.set_xlabel("Lag (minutes)", fontsize=9)
    max_lag = int(oriented["lag"].abs().max())
    axes[0][0].set_xticks(range(-max_lag, max_lag + 1))
    fig.suptitle(f"Lead-lag cross-correlation of 1-minute log returns | {kind}", fontsize=13, y=0.995)
    fig.text(0.5, 0.005, "corr(ret_A(t), ret_B(t+lag)). Positive lag: the first venue (A) leads the second (B). "
             f"Lag 0 is not drawn (see rho(0) in each panel). {sample}", ha="center", fontsize=8, color="#444")
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def _utc(value):
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def main(argv=None) -> Path:
    parser = argparse.ArgumentParser(description="C2.06 F6: lead-lag cross-correlation figure")
    parser.add_argument("--features", default=str(ROOT / "data/processed/features_1m.parquet"))
    parser.add_argument("--config", default=str(ROOT / "config/config.yaml"))
    parser.add_argument("--max-lag", type=int, default=None, help="mặc định lấy max_lag trong config")
    parser.add_argument("--start", default=None, help="ts_available >= start (UTC, tùy chọn)")
    parser.add_argument("--end", default=None, help="ts_available < end (UTC, tùy chọn)")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    features = read_parquet(args.features, "D3b")
    kind = data_kind(features)
    max_lag = args.max_lag
    if max_lag is None:
        max_lag = int(yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))["max_lag"])
    returns = features[[c for c in features.columns if c.startswith(PREFIX)]]
    if returns.shape[1] < 2:
        raise ValueError("features must contain at least two ret_1m__{venue} columns")
    mask = np.ones(len(returns), dtype=bool)
    if args.start:
        mask &= returns.index >= _utc(args.start)
    if args.end:
        mask &= returns.index < _utc(args.end)
    returns = returns[mask]
    if returns.empty:
        raise ValueError("No rows left after applying --start/--end")
    if not (args.start or args.end):
        print("WARNING: using the full sample. The final F6 should use the development sample (--end).")
    xcorr = lead_lag_xcorr(returns, max_lag)
    sample = f"Sample: {returns.index[0]:%Y-%m-%d %H:%M} to {returns.index[-1]:%Y-%m-%d %H:%M} UTC, {len(returns):,} minutes."
    default_out = ROOT / ("outputs/figures" if kind == "historical" else "outputs/synthetic/figures") / "F6_leadlag_xcorr.png"
    path = plot_lead_lag(xcorr, kind, sample, args.out or default_out)
    print(f"Wrote {path}")
    return path


if __name__ == "__main__":
    main()