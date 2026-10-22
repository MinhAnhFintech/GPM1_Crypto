"""C3.06 — Chuẩn hóa dữ liệu live thô thành data/processed/live_quotes_long.parquet (file bàn giao D2).


Đầu vào : data/raw/live/date=*/session=*/part-*.parquet  (đầu ra của collect_live.py, C3.02/C3.03)
Đầu ra  : data/processed/live_quotes_long.parquet
          data/processed/live_quotes_long_manifest.csv   (từng phiên x venue: số dòng vào/ra, dòng bị loại, độ lệch giờ)
          data/processed/live_quotes_long_manifest.json  (phiên đã đưa vào / bị loại + lý do, thời điểm tạo)


Cột D2 bắt buộc (đúng thứ tự, đúng tên): ts_recv_utc, ts_event_utc, exchange, symbol, bid, ask, bid_size, ask_size,
mid, spread_abs, spread_bps, depth_bid_10bps_usdt, depth_ask_10bps_usdt, data_kind.
Cột thêm (đặt sau cột D2; cần TV4 ghi vào D2 v1.1): market_type, venue, session_id, ts_grid_utc, depth_truncated,
depth_cover_bps, depth_{bid,ask}_{5,2}bps_usdt, book_levels_bid/ask, latency_ms.


Quy tắc:
  * mid = (bid+ask)/2 ; spread_abs = ask-bid (USDT) ; spread_bps = spread_abs/mid*1e4 ; data_kind = "live".
  * symbol luôn là BTC/USDT (collector ghi BTC/USDT:USDT cho perp -> đổi về dạng chuẩn; perp nhận biết bằng market_type/venue).
  * depth_truncated luôn là bool, không null: phiên thô cũ không có cột này (không đo được độ sâu) -> True theo quy ước collector;
    số dòng được điền ghi trong manifest.json (depth_truncated_filled_true_rows). Các cột số khác thiếu thì để NaN.
  * File parquet mang out.attrs["contract_version"] = "1.1" (đọc lại bằng pd.read_parquet(...).attrs).
  * Không nội suy, không điền giá trị cho cột số. Dòng bid/ask <= 0 hoặc ask < bid bị LOẠI và đếm trong manifest (không sửa giá).
  * Loại phiên TEST; loại phiên đang chạy (file part mới sửa trong --idle-s giây) trừ khi --include-running.
  * Chạy lại bao nhiêu lần cũng được: luôn đọc lại toàn bộ data/raw/live và ghi lại cả bảng (kết quả xác định).


Dùng:
    python src\\collectors\\live\\make_live_quotes_long.py
    python src\\collectors\\live\\make_live_quotes_long.py --exchanges binance,okx,bybit      # chỉ 3 sàn nhóm chốt
    python src\\collectors\\live\\make_live_quotes_long.py --exclude-sessions 20261018_S1     # loại hẳn một phiên (có lý do ghi trong manifest)
"""
from __future__ import annotations


import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
UTC = timezone.utc




def _find_project_root() -> Path:
    for p in [HERE, *HERE.parents]:
        if any((p / m).exists() for m in (".git", "config", "data", ".venv")):
            return p
    return Path.cwd()




ROOT = _find_project_root()
D2_COLS = ["ts_recv_utc", "ts_event_utc", "exchange", "symbol", "bid", "ask", "bid_size", "ask_size", "mid", "spread_abs",
           "spread_bps", "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "data_kind"]
EXTRA_COLS = ["market_type", "venue", "session_id", "ts_grid_utc", "depth_truncated", "depth_cover_bps",
              "depth_bid_5bps_usdt", "depth_ask_5bps_usdt", "depth_bid_2bps_usdt", "depth_ask_2bps_usdt",
              "book_levels_bid", "book_levels_ask", "latency_ms"]
NUM_COLS = ["bid", "ask", "bid_size", "ask_size", "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_bid_5bps_usdt",
            "depth_ask_5bps_usdt", "depth_bid_2bps_usdt", "depth_ask_2bps_usdt", "depth_cover_bps", "latency_ms"]
TIME_COLS = ["ts_grid_utc", "ts_recv_utc", "ts_event_utc"]
CANONICAL_SYMBOL = "BTC/USDT"




def session_of(path: Path) -> str:
    return path.parent.name.replace("session=", "")




def discover(raw_dir: Path, idle_s: float, include_running: bool, exclude: set[str]):
    """Gom các file part theo phiên và quyết định phiên nào được đưa vào."""
    files = sorted(list(raw_dir.rglob("part-*.parquet")) + list(raw_dir.rglob("part-*.csv")))
    by_session: dict[str, list[Path]] = {}
    for f in files:
        by_session.setdefault(session_of(f), []).append(f)
    use, skipped, now = {}, {}, time.time()
    for sid, fl in sorted(by_session.items()):
        if "TEST" in sid.upper():
            skipped[sid] = "phiên thử (TEST)"
        elif sid in exclude:
            skipped[sid] = "bị loại theo --exclude-sessions"
        elif not include_running and now - max(f.stat().st_mtime for f in fl) < idle_s:
            skipped[sid] = f"đang chạy (file part sửa trong {idle_s:.0f}s gần nhất)"
        else:
            use[sid] = fl
    return use, skipped




def read_part(f: Path):
    import pandas as pd
    df = pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f)
    for c in TIME_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True)
    return df




def build(raw_dir: Path, exchanges: list[str] | None, idle_s: float, include_running: bool, exclude: set[str]):
    import numpy as np
    import pandas as pd
    use, skipped = discover(raw_dir, idle_s, include_running, exclude)
    if not use:
        sys.exit(f"Không có phiên nào để xử lý trong {raw_dir} (bị bỏ qua: {skipped})")
    frames = [read_part(f) for fl in use.values() for f in fl]
    df = pd.concat(frames, ignore_index=True)
    if "market_type" not in df.columns:           # file thô đời đầu (trước khi thu perp) chỉ có spot
        df["market_type"] = "spot"
    df["market_type"] = df["market_type"].fillna("spot")   # phiên cũ thiếu cột trong khi phiên mới có
    for c in EXTRA_COLS + D2_COLS:                 # phiên cũ thiếu cột -> NaN, không bịa giá trị
        if c not in df.columns and c not in ("mid", "spread_abs", "spread_bps", "venue"):
            df[c] = np.nan if c in NUM_COLS or c == "depth_truncated" else pd.NaT if c in TIME_COLS else None
    df["exchange"] = df["exchange"].astype(str).str.lower()
    df["market_type"] = df["market_type"].astype(str).str.lower()
    if exchanges:
        keep = {e.lower() for e in exchanges}
        skipped_ex = sorted(set(df["exchange"]) - keep)
        df = df[df["exchange"].isin(keep)]
    else:
        skipped_ex = []
    for c in NUM_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    # Phiên chạy trước khi collector có size_multiplier: size của okx perp đang tính theo HỢP ĐỒNG (1 hợp đồng = 0.01 BTC).
    SIZE_FIXES = {("20261017_S2", "okx", "perp"): 0.01}
    size_fixes = []
    for (sid, exch, mt), k in SIZE_FIXES.items():
        m = (df["session_id"] == sid) & (df["exchange"] == exch) & (df["market_type"] == mt)
        df.loc[m, ["bid_size", "ask_size"]] = df.loc[m, ["bid_size", "ask_size"]] * k
        size_fixes.append({"session_id": sid, "exchange": exch, "market_type": mt, "rows": int(m.sum()),
                           "bid_ask_size_factor": k, "reason": "size tính theo hợp đồng (1 hợp đồng = 0.01 BTC)"})
    key = ["session_id", "exchange", "market_type", "ts_grid_utc"]
    rows_in = df.groupby(["session_id", "exchange", "market_type"]).size().rename("rows_in")
    df = df.sort_values(["ts_recv_utc"]).reset_index(drop=True)
    dup_mask = df.duplicated(key, keep="first")
    dup = df[dup_mask].groupby(["session_id", "exchange", "market_type"]).size().rename("dup_removed")
    df = df[~dup_mask]
    bad = ~((df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"]) & np.isfinite(df["bid"]) & np.isfinite(df["ask"]))
    invalid = df[bad].groupby(["session_id", "exchange", "market_type"]).size().rename("invalid_removed")
    df = df[~bad].copy()
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_abs"] = df["ask"] - df["bid"]
    df["spread_bps"] = df["spread_abs"] / df["mid"] * 1e4
    df["symbol"] = CANONICAL_SYMBOL
    df["venue"] = df["exchange"] + "_" + df["market_type"]
    df["data_kind"] = "live"
    for c in TIME_COLS:
        df[c] = df[c].astype("datetime64[ns, UTC]")
    df = df.sort_values(["ts_recv_utc", "venue"]).reset_index(drop=True)
    # Phiên thô cũ không đo depth -> cờ cắt = True theo quy ước collector; hợp đồng D2 v1.1 cần bool không null
    n_trunc_filled = int(df["depth_truncated"].isna().sum())
    df["depth_truncated"] = df["depth_truncated"].fillna(True).astype(bool)
    out = df[D2_COLS + EXTRA_COLS]
    # manifest theo phiên x venue
    g = df.assign(lag_ms=(df["ts_recv_utc"] - df["ts_event_utc"]).dt.total_seconds() * 1000)
    man = g.groupby(["session_id", "exchange", "market_type"]).agg(
        rows_out=("mid", "size"), first_recv_utc=("ts_recv_utc", "min"), last_recv_utc=("ts_recv_utc", "max"),
        median_spread_bps=("spread_bps", "median"), pct_event_ts_missing=("ts_event_utc", lambda s: 100 * s.isna().mean()),
        median_recv_minus_event_ms=("lag_ms", "median"),
        pct_depth_10bps_missing=("depth_bid_10bps_usdt", lambda s: 100 * s.isna().mean())).reset_index()
    man = man.merge(rows_in.reset_index(), how="outer", on=["session_id", "exchange", "market_type"])
    man = man.merge(dup.reset_index(), how="left", on=["session_id", "exchange", "market_type"])
    man = man.merge(invalid.reset_index(), how="left", on=["session_id", "exchange", "market_type"])
    for c in ("dup_removed", "invalid_removed", "rows_out"):
        man[c] = man[c].fillna(0).astype(int)
    man = man.sort_values(["session_id", "exchange", "market_type"])[
        ["session_id", "exchange", "market_type", "rows_in", "dup_removed", "invalid_removed", "rows_out", "first_recv_utc",
         "last_recv_utc", "median_spread_bps", "pct_event_ts_missing", "median_recv_minus_event_ms", "pct_depth_10bps_missing"]]
    info = {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"), "sessions_included": sorted(use),
            "sessions_skipped": skipped, "size_fixes": size_fixes, "depth_truncated_filled_true_rows": n_trunc_filled, "exchanges_filtered_out": skipped_ex, "rows_total": int(len(out)),
            "rows_by_venue": out.groupby("venue").size().astype(int).to_dict(),
            "first_ts_recv_utc": str(out["ts_recv_utc"].min()), "last_ts_recv_utc": str(out["ts_recv_utc"].max())}
    return out, man, info




def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw" / "live")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "processed" / "live_quotes_long.parquet")
    ap.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    ap.add_argument("--exchanges", help="chỉ giữ các sàn này (ví dụ binance,okx,bybit); mặc định giữ tất cả")
    ap.add_argument("--exclude-sessions", default="", help="danh sách session_id cần loại, cách nhau dấu phẩy")
    ap.add_argument("--idle-s", type=float, default=180, help="phiên có file part sửa trong N giây gần nhất coi là đang chạy")
    ap.add_argument("--include-running", action="store_true")
    a = ap.parse_args(argv)
    exclude = {x.strip() for x in a.exclude_sessions.split(",") if x.strip()}
    exchanges = [x.strip() for x in a.exchanges.split(",")] if a.exchanges else None
    out, man, info = build(a.raw, exchanges, a.idle_s, a.include_running, exclude)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    out_path = a.out if a.format == "parquet" else a.out.with_suffix(".csv")
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    out.attrs["contract_version"] = "1.1"          # parquet lưu kèm phiên bản hợp đồng dữ liệu (D2 v1.1)
    (out.to_parquet(tmp, index=False) if a.format == "parquet" else out.to_csv(tmp, index=False))
    tmp.replace(out_path)
    man.to_csv(a.out.with_name(a.out.stem + "_manifest.csv"), index=False)
    a.out.with_name(a.out.stem + "_manifest.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    import pandas as pd
    pd.set_option("display.width", 250)
    print(man.to_string(index=False))
    print(f"\nĐã ghi {out_path} ({info['rows_total']:,} dòng, {len(info['sessions_included'])} phiên: "
          f"{info['first_ts_recv_utc']} -> {info['last_ts_recv_utc']})")
    if info["sessions_skipped"]:
        print("Phiên bị bỏ qua:", info["sessions_skipped"])




if __name__ == "__main__":
    sys.exit(main())