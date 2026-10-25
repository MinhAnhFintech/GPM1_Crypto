"""C3.07 (T4) — Hồi quy thanh khoản: yếu tố nào đi cùng Information Share (IS) của một venue.

  Mô hình A (mẫu dài):           IS ~ vol_share + rv_60m + is_perp          (+ A_fe: thay is_perp bằng hiệu ứng cố định venue;
                                                                              A_ils: biến phụ thuộc ILS; A_<venue>: từng venue riêng)
  Mô hình B (mẫu live, ngắn):    IS ~ rv_live + spread_bps + log_depth + is_perp
        = các biến nền của A mà mẫu live có được (biến động: rv_live, tương ứng rv_60m của A) + spread và depth (theo PCCV C3.07:
        "B: thêm spread và depth"). Cần IS tính trên các phiên live: --live-is.
  B_no_rv (độ nhạy):             như B nhưng bỏ rv_live (đặc tả B cũ: chỉ spread + depth)
  B_no_spread (độ nhạy):         như B nhưng bỏ spread (spread bị giới hạn bởi tick size nên gần như không đổi)
  B_vol (tùy chọn):              B + vol_share theo phiên, nếu có --live-candles (nến 1 phút của các phiên live, volume_base). vol_share
                                 KHÔNG thể tính từ D2 (collector không ghi giao dịch/volume); phải lấy từ nến, cùng định nghĩa C2.04:
                                 tỷ trọng volume_base của venue trong tổng mọi venue ở từng phút, trung bình các phút của phiên.

rv_live (độ lệch chuẩn lợi suất log 1 phút của mid trong phiên) và rv_60m (độ lệch chuẩn 60 phút của ret_1m, mẫu dài) cùng ý nghĩa
nhưng khác khung thời gian: so DẤU và mức ý nghĩa giữa A và B, đừng so độ lớn hệ số.

Đơn vị quan sát (mô hình A): một venue × một cửa sổ rolling IS, gắn với window_end (không dùng available_at: với các cửa sổ của
mẫu phát triển available_at bị dồn về cuối mẫu phát triển nên không còn là thời điểm của cửa sổ).
Cửa sổ rolling chồng lấn nên phần dư tự tương quan, và các venue cùng một thời điểm phụ thuộc nhau (IS cộng lại xấp xỉ 1):
mô hình gộp dùng sai số chuẩn Driscoll-Kraay (cộng điểm số các venue theo thời gian rồi Newey-West), từng venue riêng dùng HAC
Newey-West như mẫu của TV4. Số trễ = số cửa sổ chồng lấn = ceil(độ dài cửa sổ / bước).

Các biến giải thích (trừ is_perp) được chuẩn hóa z trên mẫu ước lượng: hệ số = IS đổi bao nhiêu khi biến tăng 1 độ lệch chuẩn.
Kết quả chỉ là MỐI LIÊN HỆ (association), không phải quan hệ nhân quả.

Mỗi phiên live là một đoạn riêng: mô hình B dùng một quan sát cho mỗi (venue, phiên), không ghép các phiên. Sai số chuẩn của B
là sai số chuẩn theo cụm phiên (tổng điểm số trong từng phiên, hiệu chỉnh G/(G-1)·(n-1)/(n-k) như statsmodels cov_cluster),
p-value và khoảng tin cậy dùng phân phối t với G-1 bậc tự do. Phiên thiếu độ sâu bị loại nguyên phiên và lý do được ghi vào cột notes.

Nguồn dữ liệu được kiểm trước khi ghi: rolling_is và features_1m phải là historical, live phải là live (hoặc tất cả synthetic nếu
đường dẫn ghi nằm dưới thư mục có tên "synthetic"). Sai nhãn/không rõ nhãn -> báo lỗi, KHÔNG ghi gì, file T4 cũ giữ nguyên.

Chạy:
    python notebooks\\tv3\\liquidity_regression.py --inspect      # xem cột/khoảng thời gian của các file đầu vào
    python notebooks\\tv3\\liquidity_regression.py                 # chạy A và B, ghi outputs/tables/T4_leadership_determinants.csv
    python notebooks\\tv3\\liquidity_regression.py --live-is data\\processed\\live_session_is.parquet --live-candles data\\raw\\ohlcv_live\\candles.parquet
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import t as student_t

ROOT = Path(__file__).resolve().parents[2]
DEF_IS = ROOT / "outputs" / "tables" / "rolling_is.parquet"
DEF_FEAT = ROOT / "data" / "processed" / "features_1m.parquet"
DEF_LIVE = ROOT / "data" / "processed" / "live_quotes_long.parquet"
DEF_OUT = ROOT / "outputs" / "tables" / "T4_leadership_determinants.csv"

TIME_CANDS = ["window_end", "window_end_utc", "available_at", "ts_available", "ts_utc", "ts_grid_utc", "ts_open_utc"]
FEAT_TIME_CANDS = ["ts_utc", "ts_open_utc", "ts_grid_utc", "ts_1m_utc", "ts_available"]
MIN_COVER = 0.8          # cửa sổ đặc trưng phải có ít nhất 80% số phút
REAL_KINDS = {"rolling_is": "historical", "features_1m": "historical", "live": "live", "live_is": "live",
              "live_candles": "historical"}
SYNTH_KINDS = {k: "synthetic" for k in REAL_KINDS}


# ---------------------------------------------------------------- đọc file
def read_any(path: Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise SystemExit(f"Không thấy file: {path}")
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def kind_of(df: pd.DataFrame, label: str):
    """data_kind từ cột và từ df.attrs; hai nơi mâu thuẫn -> lỗi; không có -> None."""
    kinds = set(df["data_kind"].dropna().astype(str).unique()) if "data_kind" in df.columns else set()
    meta = df.attrs.get("data_kind")
    if meta:
        kinds.add(str(meta))
    if len(kinds) > 1:
        raise SystemExit(f"{label}: data_kind không nhất quán {sorted(kinds)}. Không ghi gì.")
    return next(iter(kinds)) if kinds else None


def output_zone(out: Path) -> str:
    """"synthetic" nếu đường dẫn ghi có thư mục tên synthetic (vd outputs/synthetic/...), còn lại là vùng kết quả thật."""
    return "synthetic" if any(p.lower() == "synthetic" for p in Path(out).resolve().parts) else "real"


def guard_input(df: pd.DataFrame, label: str, out: Path, required: bool = True) -> None:
    """Từ chối (trước khi ghi bất cứ thứ gì) nếu nhãn nguồn không khớp vùng ghi."""
    zone = output_zone(out)
    want = (REAL_KINDS if zone == "real" else SYNTH_KINDS)[label]
    got = kind_of(df, label)
    if got is None and not required:
        print(f"CẢNH BÁO: {label} không có data_kind (không kiểm được nguồn)")
        return
    if got != want:
        raise SystemExit(f"{label}: data_kind={got!r}, vùng ghi {zone!r} ({out}) cần {want!r}. "
                         f"Không ghi gì; file kết quả cũ giữ nguyên.")


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    """Ghi qua file tạm rồi thay: lỗi giữa chừng không để lại file hỏng, file cũ còn nguyên."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, float_format="%.6g")
    os.replace(tmp, path)


def utc(s) -> pd.Series:
    return pd.to_datetime(s, utc=True).astype("datetime64[ns, UTC]")


def venue_name(df: pd.DataFrame, venue_col: str | None = None) -> pd.Series:
    if venue_col and venue_col in df.columns:
        return df[venue_col].astype(str)
    if "venue" in df.columns:
        return df["venue"].astype(str)
    if {"exchange", "market_type"} <= set(df.columns):
        return df["exchange"].astype(str) + "_" + df["market_type"].astype(str)
    raise SystemExit(f"Không xác định được venue; cột hiện có: {list(df.columns)}. Dùng --venue-col.")


def pick(cols, cands):
    return next((c for c in cands if c in cols), None)


# ---------------------------------------------------------------- chuẩn hóa đầu vào
def tidy_is(df: pd.DataFrame, a) -> tuple[pd.DataFrame, float | None, float | None]:
    """-> (bảng t, venue, is_val), độ dài cửa sổ (phút), bước (phút)."""
    cols = list(df.columns)
    tcol = a.time_col or pick(cols, TIME_CANDS)
    if tcol is None:
        raise SystemExit(f"rolling_is: không thấy cột thời gian trong {cols}. Dùng --time-col.")
    df = df.copy()
    if "status" in cols:
        ok = df["status"].astype(str).str.lower().eq("ok")
        if not ok.any():
            raise SystemExit(f"rolling_is: không dòng nào có status == 'ok'. Giá trị: {df['status'].value_counts().to_dict()}")
        print(f"rolling_is: giữ {int(ok.sum()):,}/{len(df):,} dòng status=ok (bỏ {int((~ok).sum()):,} dòng lỗi, đã có lý do ở cột reason)")
        df = df[ok]
    df["t"] = utc(df[tcol])

    wide = [c for c in df.columns if c.startswith(("is_mid__", "is__"))]
    if a.is_col and a.is_col in df.columns:
        val = df[a.is_col].astype(float)
    elif "is_mid" in df.columns:
        val = df["is_mid"].astype(float)
    elif {"is_lower", "is_upper"} <= set(df.columns):
        val = (df["is_lower"].astype(float) + df["is_upper"].astype(float)) / 2
    elif pick(df.columns, ["information_share", "is"]):
        val = df[pick(df.columns, ["information_share", "is"])].astype(float)
    elif wide:
        val = None
    else:
        raise SystemExit(f"rolling_is: không thấy cột IS trong {cols}. Dùng --is-col.")

    if val is not None:
        out = pd.DataFrame({"t": df["t"], "venue": venue_name(df, a.venue_col), "is_val": val})
    else:                                          # dạng rộng: is_mid__binance_spot, ...
        long = df[["t"] + wide].melt(id_vars="t", var_name="v", value_name="is_val")
        long["venue"] = long["v"].str.split("__", n=1).str[1]
        out = long[["t", "venue", "is_val"]]
    if "ils" in df.columns and val is not None:
        out["ils"] = df["ils"].astype(float).to_numpy()
    if {"is_lower", "is_upper"} <= set(df.columns) and val is not None:
        out["is_width"] = (df["is_upper"].astype(float) - df["is_lower"].astype(float)).to_numpy()
    out = out.dropna(subset=["is_val"]).sort_values(["venue", "t"]).reset_index(drop=True)

    win = a.window_min
    ws, we = pick(cols, ["window_start", "window_start_utc"]), pick(cols, ["window_end", "window_end_utc"])
    if win is None and ws and we:
        win = float(((utc(df[we]) - utc(df[ws])).dt.total_seconds() / 60).median())
    step = a.step_min
    if step is None:
        d = out.groupby("venue")["t"].diff().dt.total_seconds().div(60).dropna()
        step = float(d[d > 0].median()) if len(d) else None
    return out, win, step

def time_to_column(df: pd.DataFrame, wanted: str | None, cands: list[str]):
    """features_1m có thể lưu giờ ở INDEX (vd ts_available, kiểu datetime): đưa index thành cột để tìm như các cột khác."""
    cols = list(df.columns)
    tcol = wanted or pick(cols, cands)
    if tcol in cols:
        return df, tcol
    if isinstance(df.index, pd.DatetimeIndex) and (wanted is None or wanted == df.index.name):
        name = df.index.name or "ts_index"
        return df.rename_axis(name).reset_index(), name
    return df, tcol        # None hoặc tên cờ sai -> để hàm gọi báo lỗi rõ, không lặng lẽ dùng index

def tidy_features(df: pd.DataFrame, a) -> pd.DataFrame:
    """-> t (phút), venue, vol_share, rv_60m."""
    df, tcol = time_to_column(df, a.feat_time_col, FEAT_TIME_CANDS)
    cols = list(df.columns)
    if tcol is None or tcol not in cols:
        raise SystemExit(f"features_1m: không thấy cột/index thời gian {tcol or FEAT_TIME_CANDS} trong cột {cols} "
                         f"(index: {df.index.name}). Dùng --feat-time-col.")
    df = df.copy()
    df["t"] = utc(df[tcol]).dt.floor("min")
    wide_vs = [c for c in cols if c.startswith("vol_share__")]
    if "vol_share" in cols:
        out = pd.DataFrame({"t": df["t"], "venue": venue_name(df, a.venue_col), "vol_share": df["vol_share"].astype(float)})
        rv_long = "rv_60m" in cols
        out["rv_60m"] = df["rv_60m"].astype(float) if rv_long else np.nan
    elif wide_vs:
        long = df[["t"] + wide_vs].melt(id_vars="t", var_name="v", value_name="vol_share")
        long["venue"] = long["v"].str.split("__", n=1).str[1]
        out = long[["t", "venue", "vol_share"]]
        rv_wide = {c.split("__", 1)[1]: c for c in cols if c.startswith("rv_60m__")}
        if rv_wide:
            rv = df[["t"] + list(rv_wide.values())].melt(id_vars="t", var_name="v", value_name="rv_60m")
            rv["venue"] = rv["v"].str.split("__", n=1).str[1]
            out = out.merge(rv[["t", "venue", "rv_60m"]], on=["t", "venue"], how="left")
        elif "rv_60m" in cols:                      # một cột rv chung cho thị trường
            out = out.merge(df[["t", "rv_60m"]].drop_duplicates("t"), on="t", how="left")
        else:
            raise SystemExit(f"features_1m: không thấy rv_60m trong {cols}")
    else:
        raise SystemExit(f"features_1m: không thấy vol_share hoặc vol_share__<venue> trong {cols}")
    if out["rv_60m"].isna().all() and "rv_60m" in cols:      # dạng dài nhưng rv là cột chung
        out = out.drop(columns="rv_60m").merge(df[["t", "rv_60m"]].drop_duplicates("t"), on="t", how="left")
    return out.sort_values(["venue", "t"]).reset_index(drop=True)


def feature_windows(feat: pd.DataFrame, win: float) -> pd.DataFrame:
    """vol_share trung bình trượt `win` phút kết thúc tại t (chỉ quá khứ); rv_60m lấy tại t (đã là độ lệch chuẩn 60 phút quá khứ)."""
    mp = max(1, int(math.ceil(MIN_COVER * win)))
    parts = []
    for v, g in feat.groupby("venue"):
        g = g.drop_duplicates("t").set_index("t").sort_index()
        r = g["vol_share"].rolling(f"{int(win)}min", min_periods=mp).mean()
        parts.append(pd.DataFrame({"venue": v, "t": g.index, "vol_share_w": r.values, "rv_60m": g["rv_60m"].values}))
    return pd.concat(parts, ignore_index=True)


def live_minutes(q: pd.DataFrame, band: int, exclude: list[str]) -> pd.DataFrame:
    """Mỗi (venue, phiên, phút): spread_bps trung vị, log_depth trung vị (độ sâu trong `band` bps, USDT, trung bình bid/ask)."""
    q = q.copy()
    q["venue"] = venue_name(q)
    if "data_kind" in q.columns:
        q = q[q["data_kind"].astype(str).eq("live")]
    if exclude:
        q = q[~q["session_id"].isin(exclude)]
    tcol = "ts_grid_utc" if "ts_grid_utc" in q.columns else "ts_recv_utc"
    q["t"] = utc(q[tcol]).dt.floor("min")
    if "spread_bps" not in q.columns:
        mid = (q["bid"] + q["ask"]) / 2
        q["spread_bps"] = (q["ask"] - q["bid"]) / mid * 1e4
    b, k = f"depth_bid_{band}bps_usdt", f"depth_ask_{band}bps_usdt"
    if b not in q.columns or k not in q.columns:
        raise SystemExit(f"live_quotes_long thiếu {b}/{k}; cột: {list(q.columns)}")
    depth = (q[b] + q[k]) / 2
    if "depth_cover_bps" in q.columns:              # sổ lệnh chưa phủ đủ `band` bps thì độ sâu bị cắt -> bỏ
        depth = depth.where(q["depth_cover_bps"] >= band)
    q["log_depth"] = np.log(depth.where(depth > 0))

    q["mid"] = (q["bid"] + q["ask"]) / 2
    return (q.groupby(["venue", "session_id", "t"])
              .agg(spread_bps=("spread_bps", "median"), log_depth=("log_depth", "median"), mid_last=("mid", "last"),
                   n_ticks=("bid", "size"))
              .reset_index())


def session_rv(lm: pd.DataFrame) -> pd.DataFrame:
    """rv_live: độ lệch chuẩn lợi suất log 1 phút của mid trong phiên (chỉ các cặp phút liền nhau), theo (venue, phiên)."""
    rows = []
    for (v, s), g in lm.sort_values("t").groupby(["venue", "session_id"]):
        r = np.log(g["mid_last"]).diff().where(g["t"].diff() == pd.Timedelta("1min")).dropna()
        rows.append({"venue": v, "session_id": s, "rv_live": float(r.std(ddof=1)) if len(r) > 5 else np.nan})
    return pd.DataFrame(rows, columns=["venue", "session_id", "rv_live"])


def session_vol_share(candles: pd.DataFrame, lm: pd.DataFrame) -> pd.DataFrame:
    """vol_share theo (venue, phiên) từ nến 1 phút: với mỗi phút, volume_base của venue / tổng volume_base của mọi venue có nến phút đó;
    rồi trung bình các phút nằm trong phiên (từ phút đầu tới phút cuối của quote live). Phiên có ít hơn MIN_COVER số phút -> NaN.
    candles: cột ts_open_utc (giờ MỞ nến), volume_base (BTC) và venue (hoặc exchange + market_type). Không nội suy phút thiếu."""
    c = candles.copy()
    c["venue"] = venue_name(c)
    c["t"] = utc(c["ts_open_utc"]).dt.floor("min")
    wide = c.pivot_table(index="t", columns="venue", values="volume_base", aggfunc="sum").sort_index()
    total = wide.sum(axis=1, min_count=1)
    share = wide.div(total.where(total > 0), axis=0)
    rows = []
    for s, g in lm.groupby("session_id"):
        t0, t1 = g["t"].min(), g["t"].max()
        n_min = int((t1 - t0) / pd.Timedelta("1min")) + 1
        sub = share.loc[t0:t1]
        for v in share.columns:
            col = sub[v].dropna()
            rows.append({"venue": v, "session_id": s,
                         "vol_share": float(col.mean()) if len(col) >= math.ceil(MIN_COVER * n_min) else np.nan})
    return pd.DataFrame(rows, columns=["venue", "session_id", "vol_share"])


def model_b_frame(li: pd.DataFrame, lm: pd.DataFrame, band: int) -> tuple[pd.DataFrame, dict]:
    """Một dòng mỗi (venue, phiên). Phiên thiếu IS hoặc thiếu spread/độ sâu ở bất kỳ venue nào bị loại NGUYÊN PHIÊN, kèm lý do."""
    sess = lm.groupby(["venue", "session_id"]).agg(spread_bps=("spread_bps", "median"), log_depth=("log_depth", "median")).reset_index()
    m = (li[["venue", "session_id", "is_val"]].merge(sess, on=["venue", "session_id"], how="left")
         .merge(session_rv(lm), on=["venue", "session_id"], how="left"))
    m["is_perp"] = m["venue"].str.contains("perp").astype(float)
    excluded = {}
    for s in sorted(set(lm["session_id"]) | set(li["session_id"])):
        sub = m[m["session_id"] == s]
        if sub.empty:
            excluded[s] = "không có dòng IS ok trong --live-is"
        elif sub["log_depth"].isna().any():
            excluded[s] = f"thiếu độ sâu ≤{band} bps (depth_cover_bps < {band} hoặc không có độ sâu)"
        elif sub["spread_bps"].isna().any():
            excluded[s] = "thiếu spread"
        elif sub["rv_live"].isna().any():
            excluded[s] = "thiếu rv_live (mid có ít hơn 6 lợi suất 1 phút liền nhau để tính biến động)"
    times = lm.groupby("session_id")["t"].agg(t_start="min", t_end="max").reset_index()
    m = m[~m["session_id"].isin(excluded)].merge(times, on="session_id", how="left")
    m["t"] = m["t_start"]                           # thời gian THẬT của phiên (đầu phiên); t_end = phút cuối của phiên
    return m, excluded


# ---------------------------------------------------------------- OLS + HAC
def _nw(sg: np.ndarray, lags: int) -> np.ndarray:
    """Newey-West (Bartlett) cho một chuỗi điểm số sg (T×k) đã sắp theo thời gian."""
    meat = sg.T @ sg
    for l in range(1, min(lags, len(sg) - 1) + 1):
        gam = sg[l:].T @ sg[:-l]
        meat += (1.0 - l / (lags + 1.0)) * (gam + gam.T)
    return meat


def ols_hac(y: np.ndarray, X: np.ndarray, groups: np.ndarray, lags: int, mode: str = "group", times: np.ndarray | None = None) -> dict:
    """OLS với ma trận phương sai bền, hiệu chỉnh n/(n-k).
    mode="group": Newey-West trong từng nhóm (dữ liệu PHẢI sắp theo nhóm rồi theo thời gian), cộng dồn giữa các nhóm.
                  Với lags=0 chỉ là HC1 theo từng dòng (KHÔNG phải sai số theo cụm; muốn theo cụm dùng mode="cluster").
    mode="dk":    Driscoll-Kraay: cộng điểm số của mọi venue có cùng `times`, rồi Newey-West theo thời gian.
    mode="cluster": sai số chuẩn theo cụm: meat = tổng theo cụm của (tổng điểm số trong cụm)(tổng điểm số trong cụm)',
                  hiệu chỉnh G/(G-1)·(n-1)/(n-k) (mặc định của statsmodels cov_cluster). Cần G >= 2."""
    n, k = X.shape
    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ X.T @ y
    u = y - X @ beta
    s = X * u[:, None]
    scale = n / (n - k)
    if mode == "cluster":
        codes = pd.factorize(groups)[0]
        n_groups = int(codes.max()) + 1
        if n_groups < 2:
            raise ValueError("cluster: cần ít nhất 2 cụm")
        sums = np.zeros((n_groups, k))
        np.add.at(sums, codes, s)
        meat = sums.T @ sums
        scale = n_groups / (n_groups - 1.0) * (n - 1.0) / (n - k)
    elif mode == "dk":
        agg = pd.DataFrame(s).groupby(pd.factorize(times, sort=True)[0]).sum().sort_index().to_numpy()
        meat = _nw(agg, lags)
        n_groups = len(agg)
    else:
        codes = pd.factorize(groups)[0]
        meat = np.zeros((k, k))
        for g in np.unique(codes):
            meat += _nw(s[codes == g], lags)
        n_groups = len(np.unique(codes))
    cov = xtx_inv @ meat @ xtx_inv * scale
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    tstat = np.divide(beta, se, out=np.full(k, np.nan), where=se > 0)
    p = np.array([math.erfc(abs(t) / math.sqrt(2)) if t == t else np.nan for t in tstat])
    n_groups = int(n_groups)
    ss_res, ss_tot = float(u @ u), float(((y - y.mean()) ** 2).sum())
    return {"beta": beta, "se": se, "t": tstat, "p": p, "r2": 1 - ss_res / ss_tot if ss_tot > 0 else np.nan,
            "n": n, "n_groups": n_groups, "cond": float(np.linalg.cond(X))}


def fit(df: pd.DataFrame, name: str, xcols: list[str], zcols: list[str], lags: int, cov: str = "dk",
        group_col: str | None = None, fe: str | None = None, y: str = "is_val", notes: str = "") -> pd.DataFrame:
    """cov: "dk" (nhóm = thời gian, cần cột t), "group" (HAC trong từng nhóm group_col), "cluster" (theo cụm group_col). fe: tên cột để thêm biến giả cố định (bỏ nhóm đầu)."""
    d = df.dropna(subset=[y] + xcols).copy()
    keep = [c for c in zcols if d[c].std(ddof=0) > 1e-12]
    dropped = [c for c in zcols if c not in keep]
    xcols = [c for c in xcols if c not in dropped]
    if dropped:
        notes = (notes + f" bỏ {dropped} vì không đổi trong mẫu").strip()
    if len(d) <= len(xcols) + 5:
        print(f"[{name}] chỉ có {len(d)} quan sát: bỏ qua")
        return pd.DataFrame()
    for c in keep:
        d[c + "_z"] = (d[c] - d[c].mean()) / d[c].std(ddof=0)
    terms = [(c + "_z" if c in keep else c) for c in xcols]
    if fe:
        dums = pd.get_dummies(d[fe], prefix=f"fe_{fe}", drop_first=True, dtype=float)
        d = pd.concat([d, dums], axis=1)
        terms += list(dums.columns)
    sort_cols = (["t"] if cov == "dk" else [group_col, "t"])
    d = d.sort_values(sort_cols)
    X = np.column_stack([np.ones(len(d))] + [d[c].to_numpy(float) for c in terms])
    grp = d[group_col].astype(str).to_numpy() if group_col else np.zeros(len(d))
    try:
        r = ols_hac(d[y].to_numpy(float), X, grp, lags, mode=cov, times=d["t"].to_numpy() if cov == "dk" else None)
    except ValueError as exc:
        print(f"[{name}] bỏ qua: {exc}")
        return pd.DataFrame()
    if cov == "cluster":                       # ít cụm: p-value và CI theo t(G-1), không dùng xấp xỉ chuẩn
        df_t = r["n_groups"] - 1
        p_values = 2 * student_t.sf(np.abs(r["t"]), df=df_t)
        t_crit = student_t.ppf(0.975, df=df_t)
    else:
        p_values, t_crit = r["p"], 1.96
    if r["cond"] > 30:
        notes = (notes + f" số điều kiện X = {r['cond']:.0f} (>30: cộng tuyến, đọc hệ số từng biến cẩn thận)").strip()
    rows = []
    for i, term in enumerate(["const"] + terms):
        rows.append({"model": name, "dep": y, "term": term.removesuffix("_z") + (" (z)" if term.endswith("_z") else ""),
                     "coef": r["beta"][i],"se_hac": r["se"][i],"t": r["t"][i],"p_value": p_values[i],
                     "ci95_low": r["beta"][i] - t_crit * r["se"][i],"ci95_high": r["beta"][i] + t_crit * r["se"][i],
                     "n_obs": r["n"], "n_groups": r["n_groups"], "hac_lags": lags, "cov": cov, "r2": r["r2"],
                     "sample_start": d["t"].min().isoformat(),
                     "sample_end": (d["t_end"] if "t_end" in d.columns else d["t"]).max().isoformat(), "notes": notes})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- chạy
def inspect(a):
    for label, path in (("rolling_is", a.rolling_is), ("features_1m", a.features), ("live_quotes_long", a.live)):
        print(f"\n=== {label}: {path}")
        if not Path(path).exists():
            print("  (không có file)")
            continue
        df = read_any(path)
        print(f"  {len(df):,} dòng; cột: {list(df.columns)}")
        for c in df.columns:
            if str(df[c].dtype).startswith("datetime"):
                print(f"  {c}: {df[c].min()} -> {df[c].max()}")
        for c in ("venue", "exchange", "market_type", "status", "session_id", "data_kind"):
            if c in df.columns:
                print(f"  {c}: {df[c].value_counts().head(15).to_dict()}")


def crosscheck(base: pd.DataFrame, lags: int) -> None:
    """So sai số HAC của một venue với statsmodels (nếu cài): phải khớp sau khi bỏ hiệu chỉnh n/(n-k)."""
    try:
        import statsmodels.api as sm
    except ImportError:
        print("crosscheck: chưa cài statsmodels, bỏ qua")
        return
    v = sorted(base["venue"].unique())[0]
    d = base[base["venue"] == v].dropna(subset=["is_val", "vol_share_w", "rv_60m"]).sort_values("t")
    X = sm.add_constant(d[["vol_share_w", "rv_60m"]].astype(float))
    ref = sm.OLS(d["is_val"].astype(float), X).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    mine = ols_hac(d["is_val"].to_numpy(float), X.to_numpy(float), np.zeros(len(d)), lags)
    k, n = X.shape[1], len(d)
    se = mine["se"] * math.sqrt((n - k) / n)
    print(f"crosscheck {v}: hệ số lệch tối đa {np.max(np.abs(mine['beta'] - ref.params.to_numpy())):.2e}; "
          f"SE HAC lệch tương đối tối đa {np.max(np.abs(se / ref.bse.to_numpy() - 1)):.2%}")


def run(a) -> pd.DataFrame:
    is_raw = read_any(a.rolling_is)
    guard_input(is_raw, "rolling_is", a.out)                       # nguồn sai -> dừng TRƯỚC khi làm gì và trước khi ghi
    is_df, win, step = tidy_is(is_raw, a)
    if win is None or step is None:
        raise SystemExit("Không đọc được độ dài cửa sổ/bước của rolling IS: truyền --window-min và --step-min (lấy từ config rolling).")
    lags = a.hac_lags if a.hac_lags is not None else int(math.ceil(win / step))
    print(f"rolling IS: cửa sổ {win:g} phút, bước {step:g} phút -> HAC lags = {lags}")
    if a.since:
        is_df = is_df[is_df["t"] >= pd.Timestamp(a.since, tz="UTC")]
    if a.until:
        is_df = is_df[is_df["t"] < pd.Timestamp(a.until, tz="UTC")]
    print(f"rolling IS dùng: {len(is_df):,} dòng, {is_df['venue'].nunique()} venue, {is_df['t'].min()} -> {is_df['t'].max()}")
    is_df["t_min"] = is_df["t"].dt.floor("min")
    is_df["is_perp"] = is_df["venue"].str.contains("perp").astype(float)
    width = f"; khoảng chặn IS rộng trung vị {is_df['is_width'].median():.2f}" if "is_width" in is_df.columns else ""

    feat_raw = read_any(a.features)
    guard_input(feat_raw, "features_1m", a.out, required=False)
    fw = feature_windows(tidy_features(feat_raw, a), win)
    miss = sorted(set(is_df["venue"]) - set(fw["venue"]))
    if miss:
        print(f"CẢNH BÁO: venue có IS nhưng không có trong features_1m (tên khác?): {miss}")
    if not set(is_df["venue"]) & set(fw["venue"]):
        raise SystemExit("Tên venue của rolling_is và features_1m không khớp (vd 'binance_spot' vs 'binance'). Chạy --inspect.")
    base = is_df.merge(fw.rename(columns={"t": "t_min"}), on=["venue", "t_min"], how="left")
    xs, zs = ["vol_share_w", "rv_60m"], ["vol_share_w", "rv_60m"]
    note = "mẫu dài, gắn theo window_end; IS = giữa khoảng chặn" + width

    out = [fit(base, "A", xs + ["is_perp"], zs, lags, "dk", notes=note),
           fit(base, "A_fe", xs, zs, lags, "dk", fe="venue", notes=note + "; hiệu ứng cố định venue thay is_perp")]
    if "ils" in base.columns:
        out.append(fit(base, "A_ils", xs + ["is_perp"], zs, lags, "dk", y="ils", notes="biến phụ thuộc: Information Leadership Share"))
    for v in sorted(base["venue"].unique()):
        out.append(fit(base[base["venue"] == v], f"A_{v}", xs, zs, lags, "group", group_col="venue",
                       notes="một venue, HAC Newey-West như mẫu TV4"))
    if a.crosscheck:
        crosscheck(base, lags)

    # ---- mô hình B: cần IS tính trên các phiên live
    if a.live_is:
        if not Path(a.live).exists():
            raise SystemExit(f"Có --live-is nhưng không thấy {a.live}")
        li = read_any(a.live_is)
        guard_input(li, "live_is", a.out)
        quotes = read_any(a.live)
        guard_input(quotes, "live", a.out)
        candles = None
        if a.live_candles:
            candles = read_any(a.live_candles)
            guard_input(candles, "live_candles", a.out)
        exclude = [x for x in a.exclude_sessions.split(",") if x]
        if "status" in li.columns:                      # run_live_is ghi cả phiên lỗi (kèm reason): chỉ giữ dòng ok
            print(f"live-is: giữ {int(li.status.eq('ok').sum())}/{len(li)} dòng status=ok")
            li = li[li["status"].astype(str).eq("ok")]
        li["venue"] = venue_name(li, a.venue_col)
        ycol = a.is_col if a.is_col and a.is_col in li.columns else None
        if ycol is None:
            ycol = "is_mid" if "is_mid" in li.columns else None
        if ycol is None and {"is_lower", "is_upper"} <= set(li.columns):
            li["is_mid"], ycol = (li["is_lower"] + li["is_upper"]) / 2, "is_mid"
        if ycol is None or "session_id" not in li.columns:
            raise SystemExit(f"--live-is cần cột session_id và IS (is_mid hoặc is_lower/is_upper); có: {list(li.columns)}")
        li = li.rename(columns={ycol: "is_val"})
        if exclude:
            li = li[~li["session_id"].isin(exclude)]
        lm = live_minutes(quotes, a.depth_band, exclude)
        m, excluded = model_b_frame(li, lm, a.depth_band)
        n_all = len(set(lm["session_id"]) | set(li["session_id"]))
        print(f"Mô hình B: {len(m)} quan sát (venue × phiên), {m['session_id'].nunique()}/{n_all} phiên")
        for sid, why in excluded.items():
            print(f"  loại phiên {sid}: {why}")
        why_txt = "; ".join(f"{k}: {v}" for k, v in excluded.items())
        nb = (f"mẫu live; mỗi quan sát = venue × phiên; sai số chuẩn theo cụm session_id, p/CI theo t(G-1); "
              f"dùng {m['session_id'].nunique()}/{n_all} phiên" + (f"; loại {why_txt}" if why_txt else "")
              + (f"; loại theo --exclude-sessions: {exclude}" if exclude else ""))
        base_x, base_z = ["rv_live", "spread_bps", "log_depth"], ["rv_live", "spread_bps", "log_depth"]
        b_models = [
            fit(m, "B", base_x + ["is_perp"], base_z, 0, "cluster", group_col="session_id",
                notes=nb + "; B = biến động (rv_live, tương ứng rv_60m của A) + spread + depth; chưa có vol_share (mẫu live không ghi volume)"),
            fit(m, "B_no_rv", ["spread_bps", "log_depth", "is_perp"], ["spread_bps", "log_depth"], 0, "cluster",
                group_col="session_id", notes=nb + "; độ nhạy: đặc tả B cũ, không có rv_live"),
            fit(m, "B_no_spread", ["rv_live", "log_depth", "is_perp"], ["rv_live", "log_depth"], 0, "cluster",
                group_col="session_id", notes=nb + "; độ nhạy: bỏ spread (bị giới hạn bởi tick size)")]
        if candles is not None:
            vs = session_vol_share(candles, lm)
            m = m.merge(vs, on=["venue", "session_id"], how="left")
            no_vs = m[m["vol_share"].isna()]
            if len(no_vs):
                print(f"  B_vol: bỏ {len(no_vs)}/{len(m)} dòng thiếu vol_share (nến thiếu hơn {1 - MIN_COVER:.0%} số phút phiên): "
                      f"{ {s: sorted(g['venue']) for s, g in no_vs.groupby('session_id')} }")
            b_models.append(fit(m, "B_vol", ["vol_share"] + base_x + ["is_perp"], ["vol_share"] + base_z, 0, "cluster",
                                group_col="session_id",
                                notes=nb + "; B + vol_share theo phiên (từ nến 1 phút, tỷ trọng volume_base trong tổng mọi venue)"))
        if not len(b_models[0]):                        # B chính phải có; thiếu thì dừng, không ghi T4 chỉ còn bản độ nhạy
            raise SystemExit("Có --live-is nhưng không ước lượng được mô hình B chính (quá ít phiên/quan sát sau khi loại phiên "
                             "thiếu depth/spread/rv_live). Không ghi gì; file cũ giữ nguyên.")
        out += b_models
    else:
        live0 = "17/10/2026"
        print(f"Mô hình B: bỏ qua (không có --live-is). rolling_is kết thúc {is_df['t'].max():%Y-%m-%d}, không phủ các phiên live "
              f"(từ {live0}); cần IS tính trên từng phiên live (python -m src.price_discovery.run_live_is).")
    parts = [o for o in out if len(o)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rolling-is", type=Path, default=DEF_IS, dest="rolling_is")
    ap.add_argument("--features", type=Path, default=DEF_FEAT)
    ap.add_argument("--live", type=Path, default=DEF_LIVE)
    ap.add_argument("--out", type=Path, default=DEF_OUT)
    ap.add_argument("--inspect", action="store_true", help="chỉ in cột và khoảng thời gian của các file đầu vào")
    ap.add_argument("--time-col", help="cột thời gian của rolling_is (mặc định: window_end)")
    ap.add_argument("--venue-col")
    ap.add_argument("--is-col", help="cột IS (mặc định: is_mid, hoặc trung bình is_lower/is_upper)")
    ap.add_argument("--feat-time-col")
    ap.add_argument("--window-min", type=float, help="độ dài cửa sổ rolling IS (phút), nếu rolling_is không có window_start/end")
    ap.add_argument("--step-min", type=float, help="bước cửa sổ (phút); mặc định suy ra từ khoảng cách các dòng")
    ap.add_argument("--hac-lags", type=int, help="số trễ HAC (đơn vị: số quan sát); mặc định ceil(cửa sổ / bước)")
    ap.add_argument("--depth-band", type=int, default=5, choices=[2, 5, 10], help="độ sâu trong bao nhiêu bps (mặc định 5: sổ lệnh live luôn phủ ≥5 bps)")
    ap.add_argument("--since", help="ISO UTC")
    ap.add_argument("--until", help="ISO UTC (không gồm until). Đừng đưa tập test cuối vào (C4.09)")
    ap.add_argument("--live-is", type=Path, dest="live_is",
                    help="IS tính trên từng phiên live (cột session_id, venue, is_mid hoặc is_lower/is_upper) cho mô hình B")
    ap.add_argument("--live-candles", type=Path, dest="live_candles",
                    help="nến 1 phút các venue trong thời gian live (ts_open_utc, venue, volume_base, data_kind=historical): thêm mô hình B_vol")
    ap.add_argument("--crosscheck", action="store_true", help="so HAC của một venue với statsmodels (nếu cài)")
    ap.add_argument("--exclude-sessions", default="", help="session_id loại khỏi mô hình B, cách nhau dấu phẩy (mặc định: không loại)")
    a = ap.parse_args(argv)
    if a.inspect:
        inspect(a)
        return 0
    res = run(a)
    if res.empty:
        print("Không có kết quả nào để ghi.")
        return 1
    write_csv_atomic(res, a.out)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(res[["model", "dep", "term", "coef", "se_hac", "t", "p_value", "n_obs", "n_groups", "r2"]].to_string(index=False))
    print(f"\nĐã ghi {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())