"""C3.07 (đầu vào của mô hình B): Information Share trên TỪNG PHIÊN live, dùng đúng đặc tả đã khóa của TV5.

Làm gì: với mỗi phiên trong live_quotes_long (D2), đưa giá giữa của các venue về lưới 1 phút theo thời điểm nhận (align_live_quotes,
quote mới nhất có ts_recv <= mốc lưới, chỉ giữ nếu cũ không quá --max-age), lấy log giá, rồi gọi static_shares (IS cận dưới/trên,
component share) và pairwise_ils (ILS) của src/price_discovery/analysis.py. Mỗi phiên là một đoạn riêng: không ghép phiên.

Đặc tả: k_ar_diff, kiểu thành phần tất định lấy từ outputs/tables/T2_vecm_spec.json (chọn trên mẫu phát triển, không chọn lại ở đây).
Hạng đồng liên kết cho phiên live đặt = số venue - 1 (một xu hướng chung: cùng một tài sản BTC/USDT); đây là giả định, không phải
kết quả kiểm định Johansen trên 180 điểm. Venue của đặc tả mà không có trong D2 (vd Coinbase/Kraken) bị bỏ, và được in ra.

Chạy (tại thư mục repo):
    python -m src.price_discovery.run_live_is
    python -m src.price_discovery.run_live_is --k-ar-diff 1        # nếu đặc tả khóa quá nhiều trễ cho 180 điểm/phiên
Kết quả: data/processed/live_session_is.parquet (session_id, venue, is_lower, is_upper, is_mid, component_share, ils, status, reason).
Đưa vào mô hình B:  python notebooks/tv3/liquidity_regression.py --live-is data/processed/live_session_is.parquet
"""
from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SKEW_SESSIONS = ()
OUT_COLS = ["session_id", "venue", "window_start", "window_end", "n_obs", "k_ar_diff", "is_lower", "is_upper", "is_mid",
            "component_share", "ils", "status", "reason", "data_kind"]


def live_spec(spec: dict, venues: list[str], k_ar_diff: int | None = None) -> dict:
    """Đặc tả khóa của TV5, giới hạn vào các venue có dữ liệu live; một xu hướng chung (hạng = n - 1)."""
    pair_ranks = {"|".join(sorted((a, b))): 1 for a, b in combinations(venues, 2)}
    return {**spec, "venues": list(venues), "k_ar_diff": int(spec["k_ar_diff"] if k_ar_diff is None else k_ar_diff),
            "coint_rank": len(venues) - 1, "system_supported": True, "pair_ranks": pair_ranks}


def _failed(session_id, venues, reason, n_obs=0, k=None, start=None, end=None):
    return [{"session_id": session_id, "venue": v, "window_start": start, "window_end": end, "n_obs": n_obs, "k_ar_diff": k,
             "is_lower": np.nan, "is_upper": np.nan, "is_mid": np.nan, "component_share": np.nan, "ils": np.nan,
             "status": "failed", "reason": reason, "data_kind": "live"} for v in venues]


def estimate_session(session_id, panel, venues, spec_live, *, logs_fn, shares_fn, pairs_fn=None, max_missing_frac=0.05):
    """Một phiên -> các dòng kết quả (không bao giờ ném lỗi thống kê; ghi lý do vào dòng)."""
    k = spec_live["k_ar_diff"]
    try:
        logs = logs_fn(panel, venues, max_missing_frac)
    except ValueError as exc:
        return _failed(session_id, venues, str(exc), k=k)
    start, end, n_obs = logs.index.min(), logs.index.max(), len(logs)
    try:
        system = shares_fn(logs, spec_live).set_index("venue")
        if (system.status != "ok").any():
            return _failed(session_id, venues, str(system.reason.iloc[0]), n_obs, k, start, end)
    except (ValueError, np.linalg.LinAlgError) as exc:
        return _failed(session_id, venues, str(exc), n_obs, k, start, end)
    ils = pd.Series(dtype=float)
    if pairs_fn is not None:
        try:
            pairs = pairs_fn(logs, spec_live)
            ils = pairs[pairs.status.eq("ok")].groupby("venue").ils.mean()
        except (ValueError, np.linalg.LinAlgError):
            pass
    rows = []
    for v in venues:
        rows.append({"session_id": session_id, "venue": v, "window_start": start, "window_end": end, "n_obs": n_obs, "k_ar_diff": k,
                     **{c: float(system.loc[v, c]) for c in ("is_lower", "is_upper", "is_mid", "component_share")},
                     "ils": float(ils[v]) if v in ils.index else np.nan, "status": "ok", "reason": "", "data_kind": "live"})
    return rows


def build_live_is(quotes: pd.DataFrame, spec: dict, *, align_fn, logs_fn, shares_fn, pairs_fn=None, grid="1min", max_age="60s",
                  exclude=SKEW_SESSIONS, k_ar_diff=None, venues=None, max_missing_frac=0.05, say=print) -> pd.DataFrame:
    wanted = list(venues or spec["venues"])
    quotes = quotes[~quotes["session_id"].isin(list(exclude))]
    rows = []
    for sid, q in quotes.groupby("session_id", sort=True):
        wide = align_fn(q, grid_freq=grid, max_age=max_age)
        have = [v for v in wanted if f"mid__{v}" in wide.columns and wide[f"mid__{v}"].notna().any()]
        dropped = [v for v in wanted if v not in have]
        if dropped:
            say(f"  {sid}: bỏ venue không có trong phiên: {dropped}")
        if len(have) < 2:
            rows += _failed(sid, wanted, "fewer than two venues with quotes")
            continue
        panel = pd.DataFrame({f"log_close__{v}": np.log(wide[f"mid__{v}"].where(wide[f"mid__{v}"] > 0)) for v in have}, index=wide.index)
        rows += estimate_session(sid, panel, have, live_spec(spec, have, k_ar_diff), logs_fn=logs_fn, shares_fn=shares_fn,
                                 pairs_fn=pairs_fn, max_missing_frac=max_missing_frac)
    return pd.DataFrame(rows, columns=OUT_COLS)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quotes", type=Path, default=ROOT / "data/processed/live_quotes_long.parquet")
    ap.add_argument("--spec", type=Path, default=ROOT / "outputs/tables/T2_vecm_spec.json")
    ap.add_argument("--out", type=Path, default=ROOT / "data/processed/live_session_is.parquet")
    ap.add_argument("--grid", default="1min", help="lưới thời gian (mặc định 1min, cùng lưới với đặc tả đã khóa)")
    ap.add_argument("--max-age", default="60s", help="quote cũ hơn mức này (tính đến mốc lưới) bị coi là thiếu")
    ap.add_argument("--k-ar-diff", type=int, help="ghi đè số trễ của đặc tả khóa (ghi lại vào cột k_ar_diff)")
    ap.add_argument("--venues", help="danh sách venue, cách nhau dấu phẩy (mặc định: venue của đặc tả)")
    ap.add_argument("--exclude-sessions", default=",".join(SKEW_SESSIONS), help="session_id loại ra (mặc định phiên lệch đồng hồ)")
    ap.add_argument("--no-ils", action="store_true", help="bỏ ILS (chạy nhanh hơn)")
    a = ap.parse_args(argv)

    from src.price_discovery import analysis as A
    from src.synchronization.live import align_live_quotes
    from src.utils.io_utils import read_parquet

    spec = json.loads(a.spec.read_text(encoding="utf-8"))
    quotes = read_parquet(a.quotes, "D2")
    quotes = quotes[quotes["data_kind"].astype(str).eq("live")] if "data_kind" in quotes.columns else quotes
    exclude = tuple(s for s in a.exclude_sessions.split(",") if s)
    venues = [v.strip() for v in a.venues.split(",")] if a.venues else None
    print(f"đặc tả khóa: k_ar_diff={spec['k_ar_diff']}, venue={spec['venues']}; lưới {a.grid}, max_age {a.max_age}")
    out = build_live_is(quotes, spec, align_fn=align_live_quotes, logs_fn=A.log_prices, shares_fn=A.static_shares,
                        pairs_fn=None if a.no_ils else A.pairwise_ils, grid=a.grid, max_age=a.max_age, exclude=exclude,
                        k_ar_diff=a.k_ar_diff, venues=venues)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(a.out, index=False)
    ok = out[out.status.eq("ok")]
    print(f"\n{out.session_id.nunique()} phiên, {len(ok)}/{len(out)} dòng venue × phiên ok -> {a.out}")
    if len(out) - len(ok):
        print(out[out.status.ne("ok")].drop_duplicates("session_id")[["session_id", "reason"]].to_string(index=False))
    if len(ok):
        k, n = int(ok.k_ar_diff.iloc[0]), ok.groupby("session_id").venue.nunique().max()
        if ok.n_obs.min() < 10 * n * (k + 1):
            print(f"CẢNH BÁO: {int(ok.n_obs.min())} điểm/phiên cho {n} venue với k_ar_diff={k} là rất ít tham số/điểm; "
                  "đọc IS live cẩn thận hoặc thử --k-ar-diff nhỏ hơn và hỏi TV5.")
    return out


if __name__ == "__main__":
    main()