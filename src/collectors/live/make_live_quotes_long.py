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
  * KIỂM NGUỒN: chỉ dòng có data_kind == "live" mới được đưa vào D2 và gán nhãn "live". Dòng data_kind khác (synthetic,
    historical, ...) hoặc trống trong một phiên đã có cột data_kind bị LOẠI và đếm trong manifest (non_live_removed). Phiên
    cũ không có cột data_kind (collector đời đầu chỉ thu live) được nhận và liệt kê trong manifest.json
    (legacy_sessions_assumed_live). Phiên có tên chứa TEST/SYNTH/FAKE/MOCK/DEMO/SIMUL bị loại. Không dòng nào được "đổi nhãn" thành live.
  * mid = (bid+ask)/2 ; spread_abs = ask-bid (USDT) ; spread_bps = spread_abs/mid*1e4 ; data_kind = "live".
  * symbol luôn là BTC/USDT (collector ghi BTC/USDT:USDT cho perp -> đổi về dạng chuẩn; perp nhận biết bằng market_type/venue).
  * depth_truncated luôn là bool, không null: phiên thô cũ không có cột này (không đo được độ sâu) -> True theo quy ước collector;
    số dòng được điền ghi trong manifest.json (depth_truncated_filled_true_rows). Các cột số khác thiếu thì để NaN.
  * KIỂU DỮ LIỆU CỐ ĐỊNH (enforce_schema): mọi cột số (kể cả book_levels_bid/ask) là float64 (thiếu = NaN, KHÔNG phải object/None);
    cột giờ là datetime64[ns, UTC]; depth_truncated là bool; cột chữ là str. Kiểu sai -> dừng với thông báo, không ghi file.
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
# book_levels_bid/ask là cột số: phiên cũ thiếu cột -> NaN float64 (trước đây rơi vào nhánh None -> object, D2 không qua validator)
NUM_COLS = ["bid", "ask", "bid_size", "ask_size", "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_bid_5bps_usdt",
            "depth_ask_5bps_usdt", "depth_bid_2bps_usdt", "depth_ask_2bps_usdt", "depth_cover_bps", "latency_ms",
            "book_levels_bid", "book_levels_ask"]
DERIVED_NUM_COLS = ["mid", "spread_abs", "spread_bps"]
STR_COLS = ["exchange", "symbol", "data_kind", "market_type", "venue", "session_id"]
TIME_COLS = ["ts_grid_utc", "ts_recv_utc", "ts_event_utc"]
CANONICAL_SYMBOL = "BTC/USDT"
NON_LIVE_SESSION_MARKERS = ("TEST", "SYNTH", "FAKE", "MOCK", "DEMO", "SIMUL")




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
        marker = next((m for m in NON_LIVE_SESSION_MARKERS if m in sid.upper()), None)
        if marker:
            skipped[sid] = f"phiên không phải dữ liệu live (tên chứa {marker})"
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
    sid = session_of(f)                            # phiên lấy từ tên thư mục nếu file thô không có/thiếu cột session_id
    if "session_id" not in df.columns:
        df["session_id"] = sid
    else:
        df["session_id"] = df["session_id"].fillna(sid)
    return df




def enforce_schema(out):
    """Ép và kiểm kiểu D2 v1.1. Cột số = float64 (thiếu -> NaN), giờ = datetime64[ns, UTC], depth_truncated = bool, chữ = str.
    Còn cột nào kiểu object ngoài nhóm chữ sau khi ép -> dừng, không ghi file hỏng xuống đĩa."""
    import pandas as pd
    out = out.copy()
    for c in NUM_COLS + DERIVED_NUM_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("float64")
    for c in TIME_COLS:
        out[c] = pd.to_datetime(out[c], utc=True).astype("datetime64[ns, UTC]")
    out["depth_truncated"] = out["depth_truncated"].astype("boolean").fillna(True).astype(bool)
    for c in STR_COLS:
        out[c] = out[c].astype(str)
    bad = {c: str(out[c].dtype) for c in D2_COLS + EXTRA_COLS
           if c not in STR_COLS and (out[c].dtype == object or str(out[c].dtype) == "string")}
    if bad:
        sys.exit(f"Kiểu dữ liệu sai sau khi ép (không ghi file): {bad}")
    return out




def build(raw_dir: Path, exchanges: list[str] | None, idle_s: float, include_running: bool, exclude: set[str]):
    import numpy as np
    import pandas as pd
    use, skipped = discover(raw_dir, idle_s, include_running, exclude)
    if not use:
        sys.exit(f"Không có phiên nào để xử lý trong {raw_dir} (bị bỏ qua: {skipped})")
    frames = [read_part(f) for fl in use.values() for f in fl]
    df = pd.concat(frames, ignore_index=True)

    # ---- KIỂM NGUỒN (trước khi điền cột thiếu): chỉ data_kind == "live" được vào D2 ----
    kind = (df["data_kind"].astype("string").str.strip().str.lower() if "data_kind" in df.columns
            else pd.Series(pd.NA, index=df.index, dtype="string"))
    has_kind = kind.notna().groupby(df["session_id"]).transform("any").astype(bool)   # phiên có ghi nhãn nguồn
    is_live = (kind == "live").fillna(False).astype(bool)
    df["_is_live"] = (~has_kind) | is_live          # phiên cũ không có nhãn: coi là live (collector đời đầu), có ghi vào manifest
    legacy = {s: int(n) for s, n in df.loc[~has_kind].groupby("session_id").size().items()}

    if "market_type" not in df.columns:           # file thô đời đầu (trước khi thu perp) chỉ có spot
        df["market_type"] = "spot"
    df["market_type"] = df["market_type"].fillna("spot")   # phiên cũ thiếu cột trong khi phiên mới có
    for c in EXTRA_COLS + D2_COLS:                 # phiên cũ thiếu cột -> NaN, không bịa giá trị
        if c in df.columns or c in ("mid", "spread_abs", "spread_bps", "venue"):
            continue
        if c in NUM_COLS or c == "depth_truncated":
            df[c] = np.nan
        elif c in TIME_COLS:
            df[c] = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
        else:
            df[c] = None
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
    grp = ["session_id", "exchange", "market_type"]
    rows_in = df.groupby(grp).size().rename("rows_in")
    # loại dòng không phải live (đếm theo phiên x venue); phiên bị loại sạch được ghi vào sessions_skipped
    sessions_before = set(df["session_id"])
    non_live = df[~df["_is_live"]].groupby(grp).size().rename("non_live_removed")
    n_non_live = int((~df["_is_live"]).sum())
    df = df[df["_is_live"]].drop(columns="_is_live")
    if df.empty:
        sys.exit(f"Không còn dòng live nào sau khi kiểm nguồn (đã loại {n_non_live:,} dòng không phải live; "
                 f"phiên bị bỏ qua: {skipped})")
    gone = sorted(sessions_before - set(df["session_id"]))
    for sid in gone:
        skipped[sid] = "toàn bộ dòng không phải live (data_kind != live)"
    df = df.sort_values(["ts_recv_utc"]).reset_index(drop=True)
    dup_mask = df.duplicated(key, keep="first")
    dup = df[dup_mask].groupby(grp).size().rename("dup_removed")
    df = df[~dup_mask]
    bad = ~((df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"]) & np.isfinite(df["bid"]) & np.isfinite(df["ask"]))
    invalid = df[bad].groupby(grp).size().rename("invalid_removed")
    df = df[~bad].copy()
    df["mid"] = (df["bid"] + df["ask"]) / 2.0
    df["spread_abs"] = df["ask"] - df["bid"]
    df["spread_bps"] = df["spread_abs"] / df["mid"] * 1e4
    df["symbol"] = CANONICAL_SYMBOL
    df["venue"] = df["exchange"] + "_" + df["market_type"]
    df["data_kind"] = "live"                        # chỉ tới được đây nếu dòng đã qua kiểm nguồn ở trên
    for c in TIME_COLS:
        df[c] = df[c].astype("datetime64[ns, UTC]")
    df = df.sort_values(["ts_recv_utc", "venue"]).reset_index(drop=True)
    # Phiên thô cũ không đo depth -> cờ cắt = True theo quy ước collector; hợp đồng D2 v1.1 cần bool không null
    n_trunc_filled = int(df["depth_truncated"].isna().sum())
    df["depth_truncated"] = df["depth_truncated"].astype("boolean").fillna(True).astype(bool)
    out = enforce_schema(df[D2_COLS + EXTRA_COLS])
    # manifest theo phiên x venue
    g = df.assign(lag_ms=(df["ts_recv_utc"] - df["ts_event_utc"]).dt.total_seconds() * 1000)
    man = g.groupby(grp).agg(
        rows_out=("mid", "size"), first_recv_utc=("ts_recv_utc", "min"), last_recv_utc=("ts_recv_utc", "max"),
        median_spread_bps=("spread_bps", "median"), pct_event_ts_missing=("ts_event_utc", lambda s: 100 * s.isna().mean()),
        median_recv_minus_event_ms=("lag_ms", "median"),
        pct_depth_10bps_missing=("depth_bid_10bps_usdt", lambda s: 100 * s.isna().mean())).reset_index()
    man = man.merge(rows_in.reset_index(), how="outer", on=grp)
    man = man.merge(non_live.reset_index(), how="left", on=grp)
    man = man.merge(dup.reset_index(), how="left", on=grp)
    man = man.merge(invalid.reset_index(), how="left", on=grp)
    for c in ("non_live_removed", "dup_removed", "invalid_removed", "rows_out"):
        man[c] = man[c].fillna(0).astype(int)
    man = man.sort_values(grp)[
        ["session_id", "exchange", "market_type", "rows_in", "non_live_removed", "dup_removed", "invalid_removed", "rows_out",
         "first_recv_utc", "last_recv_utc", "median_spread_bps", "pct_event_ts_missing", "median_recv_minus_event_ms",
         "pct_depth_10bps_missing"]]
    info = {"created_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "sessions_included": sorted(set(use) - set(gone)),
            "sessions_skipped": skipped, "size_fixes": size_fixes, "depth_truncated_filled_true_rows": n_trunc_filled,
            "non_live_removed_rows": n_non_live, "legacy_sessions_assumed_live": legacy,
            "exchanges_filtered_out": skipped_ex, "rows_total": int(len(out)),
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
    if info["non_live_removed_rows"]:
        print(f"Đã LOẠI {info['non_live_removed_rows']:,} dòng không phải live (data_kind != live).")
    if info["legacy_sessions_assumed_live"]:
        print("Phiên cũ không có cột data_kind, được coi là live:", info["legacy_sessions_assumed_live"])
    if info["sessions_skipped"]:
        print("Phiên bị bỏ qua:", info["sessions_skipped"])




if __name__ == "__main__":
    sys.exit(main())