"""C3.02 — Collector live (bản tối thiểu) chạy THEO PHIÊN.


Mỗi `interval_s` giây (mặc định 5s), với mỗi sàn: lấy bid, ask, bid_size, ask_size của BTC/USDT qua CCXT
(mức 1 của order book) và ghi Parquet theo ngày + theo phiên:


    data/raw/live/date=YYYY-MM-DD/session=<session_id>/part-0001.parquet ...


Quy tắc phiên:
  * Mỗi phiên là một đoạn dữ liệu riêng (cột session_id). Giữa hai phiên collector chờ, không ghi gì.
  * Không nội suy / chèn dữ liệu giả vào chỗ thiếu: lỗi mạng -> bỏ lượt đó, ghi log.
  * Thời gian luôn UTC (có timezone). ts_recv_utc = giờ máy nhận; ts_event_utc = giờ sàn (có thể trống).




Mỗi venue trong YAML có thể khai báo thêm `params` (dict) để truyền cho fetch_order_book của CCXT,
ví dụ OKX perp dùng endpoint sâu hơn: params: {method: publicGetMarketBooksFull}.




Dùng:
    python collect_live.py                          # = run (nút Run của VS Code cũng được)
    python collect_live.py run                      # chạy theo lịch trong config/live_sessions.yaml
    python collect_live.py run --test-minutes 2     # chạy thử 1 phiên 2 phút ngay bây giờ
    python collect_live.py schedule                 # chỉ in danh sách phiên
    python collect_live.py coverage                 # thống kê số dòng, khoảng mất dữ liệu (cho C3.05 / T0b)
"""
from __future__ import annotations




import argparse
import logging
import random
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo




import yaml




HERE = Path(__file__).resolve().parent
def _find_project_root() -> Path:
    """Đi ngược từ thư mục file lên, lấy thư mục đầu tiên có .git / config / data / .venv (không phụ thuộc tên src hay scr).
    Không tìm ra thì dùng thư mục đang đứng (cwd)."""
    for p in [HERE, *HERE.parents]:
        if any((p / m).exists() for m in (".git", "config", "data", ".venv")):
            return p
    return Path.cwd()




PROJECT_ROOT = _find_project_root()
CONFIG_CANDIDATES = [PROJECT_ROOT / "config" / "live_sessions.yaml", HERE / "live_sessions.yaml",
                     Path.cwd() / "config" / "live_sessions.yaml", Path.cwd() / "live_sessions.yaml"]
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw" / "live"   # luôn là <gốc dự án>/data/raw/live (đổi bằng --out)




LOG = logging.getLogger("collect_live")
UTC = timezone.utc


COLUMNS = ["ts_grid_utc", "ts_recv_utc", "ts_event_utc", "exchange", "market_type", "symbol",
           "bid", "ask", "bid_size", "ask_size",
           "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_truncated", "book_levels_bid", "book_levels_ask",
           "depth_bid_5bps_usdt", "depth_ask_5bps_usdt", "depth_bid_2bps_usdt", "depth_ask_2bps_usdt", "depth_cover_bps",
           "latency_ms", "session_id", "data_kind"]
EXTRA_BANDS = (5, 2)   # vùng hẹp bổ sung: ít bị cắt hơn 10 bps trên sổ dày (Bybit perp, OKX)
DEPTH_BPS = 10          # tên cột depth_*_10bps_usdt cố định theo hợp đồng dữ liệu D2
SHALLOW_LIMIT = 5       # sổ nông dùng làm phương án dự phòng khi lấy sổ sâu bị lỗi
MAX_DEEP_FAILS = 3      # sổ sâu lỗi liên tiếp bấy nhiêu lần (mà sổ nông ổn) -> tắt sổ sâu cho phần còn lại của phiên
ERROR_FIELDS = ["ts_utc", "venue", "error_type", "message", "action"]




@dataclass(frozen=True)
class Session:
    session_id: str
    start: datetime  # aware, UTC
    end: datetime    # aware, UTC






# ----------------------------------------------------------------------------- lịch
def get_tz(name: str):
    """ZoneInfo, có dự phòng UTC+7 cho Windows chưa cài tzdata (pip install tzdata)."""
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        if name in ("Asia/Ho_Chi_Minh", "Asia/Saigon", "Asia/Bangkok"):
            return timezone(timedelta(hours=7))
        raise






def build_schedule(cfg: dict) -> list[Session]:
    tz = get_tz(cfg["timezone"])
    d, end_d = cfg["start_date"], cfg["end_date"]
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if isinstance(end_d, str):
        end_d = date.fromisoformat(end_d)
    out: list[Session] = []
    while d <= end_d:
        key = "sessions_weekend" if d.weekday() >= 5 else "sessions_weekday"
        for s in cfg[key]:
            sh, sm = map(int, s["start"].split(":"))
            eh, em = map(int, s["end"].split(":"))
            st = datetime(d.year, d.month, d.day, sh, sm, tzinfo=tz)
            en = datetime(d.year, d.month, d.day, eh, em, tzinfo=tz)
            if en <= st:
                en += timedelta(days=1)
            out.append(Session(f"{d:%Y%m%d}_{s['name']}", st.astimezone(UTC), en.astimezone(UTC)))
        d += timedelta(days=1)
    return out






# ----------------------------------------------------------------------------- sàn
def get_venues(cfg: dict) -> list[dict]:
    """Danh sách (sàn, loại thị trường) cần thu. Ưu tiên cfg['venues'] (spot + perp);
    không có thì dùng cfg['exchanges'] cũ (chỉ spot). Mỗi venue: exchange, market_type, ccxt_id, symbol,
    book_limit, size_multiplier, params (tham số thêm cho fetch_order_book, mặc định rỗng)."""
    if cfg.get("venues"):
        out = []
        for v in cfg["venues"]:
            out.append({"exchange": v["exchange"], "market_type": v.get("market_type", "spot"),
                        "ccxt_id": v.get("ccxt_id", v["exchange"]), "symbol": v.get("symbol", cfg["symbol"]),
                        "book_limit": int(v.get("book_limit", cfg.get("book_limit", SHALLOW_LIMIT))),
                        "size_multiplier": float(v.get("size_multiplier", 1.0)),
                        "params": dict(v.get("params") or {})})
        return out
    ov = cfg.get("symbol_overrides", {})
    return [{"exchange": n, "market_type": "spot", "ccxt_id": n, "symbol": ov.get(n, cfg["symbol"]),
             "book_limit": int(cfg.get("book_limit", SHALLOW_LIMIT)), "size_multiplier": 1.0, "params": {}}
            for n in cfg["exchanges"]]






def venue_key(v: dict) -> str:
    return f"{v['exchange']}:{v['market_type']}"






def make_exchange(name: str):
    """Tạo đối tượng CCXT theo ccxt_id. Tách riêng để test có thể thay bằng sàn giả."""
    import ccxt  # import muộn: chỉ cần khi chạy thật
    return getattr(ccxt, name)({"enableRateLimit": True, "timeout": 4000})






def compute_depth(bids, asks, bps: float = DEPTH_BPS, size_mult: float = 1.0) -> dict:
    """Độ sâu sổ lệnh trong vòng `bps` điểm cơ bản quanh giá giữa, đơn vị USDT.




    bids giảm dần, asks tăng dần: danh sách [giá, khối_lượng, ...]. mid = (bid1 + ask1) / 2 tại đúng thời điểm lấy sổ.
      depth_bid = tổng giá*khối_lượng của các mức có giá >= mid*(1 - bps/1e4)
      depth_ask = tổng giá*khối_lượng của các mức có giá <= mid*(1 + bps/1e4)
    depth_truncated = True nếu sổ lấy về hết ngay TRONG vùng bps (mức cuối vẫn nằm trong vùng) -> số đo bị đánh giá thấp.
    """
    mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0
    lo, hi = mid * (1 - bps / 1e4), mid * (1 + bps / 1e4)
    d_bid = sum(float(p) * float(q) * size_mult for p, q, *_ in bids if float(p) >= lo)
    d_ask = sum(float(p) * float(q) * size_mult for p, q, *_ in asks if float(p) <= hi)
    trunc = float(bids[-1][0]) >= lo or float(asks[-1][0]) <= hi
    out = {"depth_bid_10bps_usdt": d_bid, "depth_ask_10bps_usdt": d_ask, "depth_truncated": bool(trunc),
           "book_levels_bid": len(bids), "book_levels_ask": len(asks)}
    for b in EXTRA_BANDS:  # cùng công thức, vùng hẹp hơn
        l2, h2 = mid * (1 - b / 1e4), mid * (1 + b / 1e4)
        out[f"depth_bid_{b}bps_usdt"] = sum(float(p) * float(q) * size_mult for p, q, *_ in bids if float(p) >= l2)
        out[f"depth_ask_{b}bps_usdt"] = sum(float(p) * float(q) * size_mult for p, q, *_ in asks if float(p) <= h2)
    # sổ lấy về phủ được bao nhiêu bps mỗi phía (lấy phía hẹp hơn): vùng b bps chỉ đáng tin khi depth_cover_bps >= b
    out["depth_cover_bps"] = min((mid - float(bids[-1][0])) / mid, (float(asks[-1][0]) - mid) / mid) * 1e4
    return out




def fetch_book(ex, symbol: str, limit: int, size_mult: float = 1.0, with_depth: bool = True,
               params: dict | None = None) -> dict:
    """Lấy sổ lệnh một lần: bid/ask/size mức 1 + (nếu with_depth) depth 10 bps. Không nội suy, không điền thiếu.
    `params` được truyền thẳng cho ex.fetch_order_book (ví dụ OKX perp: {"method": "publicGetMarketBooksFull"})."""
    ob = ex.fetch_order_book(symbol, limit, params or {})
    if not ob.get("bids") or not ob.get("asks"):
        raise ValueError("order book rỗng")
    (bid, bid_sz), (ask, ask_sz) = ob["bids"][0][:2], ob["asks"][0][:2]
    ts_ev = ob.get("timestamp")
    out = {"bid": float(bid), "ask": float(ask), "bid_size": float(bid_sz) * size_mult,
           "ask_size": float(ask_sz) * size_mult,
           "ts_event_utc": datetime.fromtimestamp(ts_ev / 1000, UTC) if ts_ev else None}
    if with_depth:
        out.update(compute_depth(ob["bids"], ob["asks"], DEPTH_BPS, size_mult))
    else:  # sổ nông: KHÔNG tính depth (sẽ đánh giá thấp) -> để trống và đánh dấu truncated
        out.update({"depth_bid_10bps_usdt": None, "depth_ask_10bps_usdt": None, "depth_truncated": True,
                    "depth_bid_5bps_usdt": None, "depth_ask_5bps_usdt": None, "depth_bid_2bps_usdt": None,
                    "depth_ask_2bps_usdt": None, "depth_cover_bps": None,
                    "book_levels_bid": len(ob["bids"]), "book_levels_ask": len(ob["asks"])})
    return out




def is_transient(e: Exception) -> bool:
    """Lỗi tạm thời (mạng, timeout, sàn bận, bị giới hạn tốc độ): KHÔNG phải lỗi do limit sổ sâu -> không dùng sổ nông, đi backoff."""
    n = type(e).__name__.lower()
    return (isinstance(e, (TimeoutError, ConnectionError)) or is_rate_limit(e)
            or any(k in n for k in ("timeout", "network", "notavailable", "connection", "unavailable")))




def is_rate_limit(e: Exception) -> bool:
    n = type(e).__name__.lower()
    return "ratelimit" in n or "ddos" in n or "too many" in str(e).lower() or "429" in str(e)




# ----------------------------------------------------------------------------- ghi file
class SessionWriter:
    def __init__(self, out_dir: Path, session: Session, tz: ZoneInfo, fmt: str):
        d = session.start.astimezone(tz).date()
        self.dir = out_dir / f"date={d:%Y-%m-%d}" / f"session={session.session_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fmt, self.n, self.rows, self.lock = fmt, len(list(self.dir.glob("part-*"))), [], threading.Lock()
        self.total = 0








    def add(self, row: dict):
        with self.lock:
            self.rows.append(row)








    def log_error(self, venue: str, error_type: str, message: str, action: str):
        """Ghi lỗi vào errors.csv của phiên (đầu vào cho bảng độ phủ T0b). Không bao giờ ném lỗi ra ngoài."""
        try:
            import csv
            path = self.dir / "errors.csv"
            with self.lock:
                new = not path.exists()
                with open(path, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    if new:
                        w.writerow(ERROR_FIELDS)
                    w.writerow([datetime.now(UTC).isoformat(), venue, error_type, message[:300].replace("\n", " "), action])
        except Exception:  # noqa: BLE001
            pass








    def flush(self):
        with self.lock:
            rows, self.rows = self.rows, []
        if not rows:
            return
        import pandas as pd
        df = pd.DataFrame(rows, columns=COLUMNS)
        for c in ("ts_grid_utc", "ts_recv_utc", "ts_event_utc"):
            df[c] = pd.to_datetime(df[c], utc=True)
        self.n += 1
        path = self.dir / f"part-{self.n:04d}.{'parquet' if self.fmt == 'parquet' else 'csv'}"
        tmp = path.with_suffix(path.suffix + ".tmp")
        (df.to_parquet(tmp, index=False) if self.fmt == "parquet" else df.to_csv(tmp, index=False))
        tmp.rename(path)  # ghi nguyên tử: file hỏng giữa chừng không bao giờ mang tên part-*
        self.total += len(df)
        LOG.info("flush %s: +%d dòng (tổng phiên %d)", path.name, len(df), self.total)
















# ----------------------------------------------------------------------------- một phiên
def _worker(venue, cfg, session, writer, stop_at, stats):
    name, symbol, key = venue["exchange"], venue["symbol"], venue_key(venue)
    interval, deep_limit, mult = cfg["interval_s"], venue["book_limit"], venue["size_multiplier"]
    params = venue.get("params") or {}   # chỉ áp dụng cho lần lấy sổ sâu (vd OKX books-full)
    try:
        ex = make_exchange(venue["ccxt_id"])
    except Exception as e:  # noqa: BLE001
        LOG.error("[%s] không tạo được sàn: %s", key, e)
        writer.log_error(key, type(e).__name__, str(e), "worker_stopped")
        return
    backoff, skip_until = 0.0, 0.0
    deep_fails, deep_on = 0, deep_limit > SHALLOW_LIMIT
    # lưới thời gian căn theo bội số của interval (UTC epoch) để các sàn cùng nhịp
    nxt = (int(time.time() // interval) + 1) * interval
    while nxt < stop_at.timestamp():
        time.sleep(max(0.0, nxt - time.time()))
        grid = datetime.fromtimestamp(nxt, UTC)
        nxt += interval
        if time.time() < skip_until:
            stats[key]["skipped"] += 1
            continue
        t0 = time.time()
        try:
            try:
                r = fetch_book(ex, symbol, deep_limit if deep_on else SHALLOW_LIMIT, mult, with_depth=deep_on,
                               params=params if deep_on else None)
                deep_fails = 0
            except Exception as e_deep:  # noqa: BLE001
                if not deep_on or is_transient(e_deep):
                    raise
                # thử ngay sổ nông (không dùng params) để không mất bid/ask; nếu sổ nông cũng lỗi thì lỗi này là lỗi thật
                r = fetch_book(ex, symbol, SHALLOW_LIMIT, mult, with_depth=False)
                deep_fails += 1
                stats[key]["shallow"] += 1
                writer.log_error(key, type(e_deep).__name__, str(e_deep), f"deep_failed_used_shallow({deep_fails})")
                LOG.warning("[%s] sổ sâu (limit=%d) lỗi %s -> dùng sổ nông, depth để trống: %s", key, deep_limit,
                            type(e_deep).__name__, str(e_deep)[:120])
                if deep_fails >= MAX_DEEP_FAILS:
                    deep_on = False
                    LOG.error("[%s] sổ sâu lỗi %d lần liên tiếp -> TẮT sổ sâu trong phần còn lại của phiên "
                              "(chỉ còn bid/ask mức 1). Hãy sửa book_limit/params của venue này trong config.",
                              key, deep_fails)
                    writer.log_error(key, "DeepBookDisabled", f"limit={deep_limit}", "deep_disabled_for_session")
            recv = datetime.now(UTC)
            writer.add({"ts_grid_utc": grid, "ts_recv_utc": recv, "exchange": name,
                        "market_type": venue["market_type"], "symbol": symbol,
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                        "session_id": session.session_id, "data_kind": "live", **r})
            stats[key]["ok"] += 1
            backoff = 0.0
        except Exception as e:  # noqa: BLE001 — mọi lỗi mạng/sàn: bỏ lượt, KHÔNG chèn dữ liệu giả
            stats[key]["err"] += 1
            base = max(10.0, backoff * 2) if is_rate_limit(e) else max(1.0, backoff * 2)  # bị giới hạn tốc độ -> nghỉ lâu hơn
            backoff = min(60.0 if is_rate_limit(e) else 30.0, base)
            wait = backoff * (0.8 + 0.4 * random.random())   # jitter, tránh các luồng cùng thử lại một lúc
            skip_until = time.time() + wait
            writer.log_error(key, type(e).__name__, str(e), f"skip_tick_backoff_{wait:.0f}s")
            LOG.warning("[%s] lỗi (%s), bỏ lượt, nghỉ %.0fs: %s", key, type(e).__name__, wait, str(e)[:160])
















def run_session(cfg, session: Session, out_dir: Path, fmt: str):
    tz = get_tz(cfg["timezone"])
    stop_at = session.end
    LOG.info("=== BẮT ĐẦU phiên %s, kết thúc %s ===", session.session_id, stop_at.astimezone(tz))
    writer = SessionWriter(out_dir, session, tz, fmt)
    venues = get_venues(cfg)
    stats = {venue_key(v): {"ok": 0, "err": 0, "skipped": 0, "shallow": 0} for v in venues}
    threads = [threading.Thread(target=_worker, args=(v, cfg, session, writer, stop_at, stats), daemon=True)
               for v in venues]
    for t in threads:
        t.start()
    try:
        last = time.time()
        while any(t.is_alive() for t in threads):
            time.sleep(1.0)
            if time.time() - last >= cfg["flush_every_s"]:
                writer.flush()
                last = time.time()
    finally:
        for t in threads:
            t.join(timeout=10)
        writer.flush()  # luôn ghi nốt phần còn lại, kể cả khi Ctrl+C
    LOG.info("=== KẾT THÚC phiên %s: %s ===", session.session_id, stats)
















def run(cfg, out_dir: Path, fmt: str, test_minutes: float | None, min_remaining: float = 10):
    if test_minutes:
        now = datetime.now(UTC)
        sessions = [Session(f"{now:%Y%m%d}_TEST{now:%H%M}", now, now + timedelta(minutes=test_minutes))]
    else:
        sessions = build_schedule(cfg)
    tz = get_tz(cfg["timezone"])
    for s in sessions:
        now = datetime.now(UTC)
        if s.end <= now:
            continue
        if s.start > now:
            LOG.info("Chờ phiên %s bắt đầu lúc %s (%.0f phút nữa)", s.session_id, s.start.astimezone(tz),
                     (s.start - now).total_seconds() / 60)
            while (w := (s.start - datetime.now(UTC)).total_seconds()) > 0:
                time.sleep(min(30, w))
        elif not test_minutes and (s.end - now).total_seconds() < min_remaining * 60:
            LOG.info("Bỏ phiên %s: chỉ còn %.0f phút (< %.0f) — không thu đoạn quá ngắn, chờ phiên sau",
                     s.session_id, (s.end - now).total_seconds() / 60, min_remaining)
            continue
        else:
            LOG.warning("Phiên %s đã chạy từ %s — vào giữa phiên, dữ liệu phiên này ngắn hơn khung dự kiến",
                        s.session_id, s.start.astimezone(tz))
        run_session(cfg, s, out_dir, fmt)
    LOG.info("Hết lịch thu thập.")
















# ----------------------------------------------------------------------------- coverage (C3.05 / T0b)
def coverage(out_dir: Path, interval_s: int):
    import pandas as pd
    files = sorted(list(out_dir.rglob("part-*.parquet")) + list(out_dir.rglob("part-*.csv")))
    if not files:
        print("Chưa có dữ liệu trong", out_dir)
        return None
    df = pd.concat([pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f) for f in files])
    for c in ("ts_grid_utc", "ts_recv_utc"):
        df[c] = pd.to_datetime(df[c], utc=True)
    rows = []
    if "market_type" not in df.columns:
        df["market_type"] = "spot"
    for (sid, ex, mt), g in df.groupby(["session_id", "exchange", "market_type"]):
        g = g.sort_values("ts_grid_utc")
        span = (g.ts_grid_utc.iloc[-1] - g.ts_grid_utc.iloc[0]).total_seconds()
        expected = int(span // interval_s) + 1
        gaps = g.ts_grid_utc.diff().dt.total_seconds().dropna()
        big = gaps[gaps > 3 * interval_s]
        rows.append({"session_id": sid, "exchange": ex, "market_type": mt, "rows": len(g), "expected_rows": expected,
                     "coverage_pct": round(100 * len(g) / expected, 2), "n_gaps_gt_3_ticks": len(big),
                     "max_gap_s": float(gaps.max()) if len(gaps) else 0.0,
                     "first_utc": g.ts_grid_utc.iloc[0], "last_utc": g.ts_grid_utc.iloc[-1],
                     "median_latency_ms": float(g.latency_ms.median())}
                    | ({"depth_truncated_pct": round(100 * g.depth_truncated.astype(bool).mean(), 1),
                        "median_depth_bid_usdt": float(g.depth_bid_10bps_usdt.median()),
                        "median_depth_ask_usdt": float(g.depth_ask_10bps_usdt.median())}
                       | ({"median_cover_bps": round(float(g.depth_cover_bps.median()), 2),
                           "pct_cover_ge_5bps": round(100 * (g.depth_cover_bps >= 5).mean(), 1)}
                          if "depth_cover_bps" in g.columns and g.depth_cover_bps.notna().any() else {})
                       if "depth_bid_10bps_usdt" in g.columns and g.depth_bid_10bps_usdt.notna().any() else {}))
    res = pd.DataFrame(rows)
    # số lỗi từng phiên/luồng từ errors.csv (nếu có)
    errs = [pd.read_csv(f).assign(session_id=f.parent.name.replace("session=", "")) for f in out_dir.rglob("errors.csv")]
    if errs:
        e = pd.concat(errs)
        cnt = e.groupby(["session_id", "venue"]).size().rename("n_errors").reset_index()
        cnt[["exchange", "market_type"]] = cnt["venue"].str.split(":", n=1, expand=True)
        res = res.merge(cnt[["session_id", "exchange", "market_type", "n_errors"]], how="left",
                        on=["session_id", "exchange", "market_type"])
        res["n_errors"] = res["n_errors"].fillna(0).astype(int)
    print(res.to_string(index=False))
    return res
















def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="run", choices=["run", "schedule", "coverage"],
                    help="mặc định: run")
    ap.add_argument("--config", type=Path, default=None)
    ap.add_argument("--min-remaining", type=float, default=10,
                    help="vào giữa phiên chỉ khi còn >= N phút (mặc định 10)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--format", choices=["parquet", "csv"], default="parquet")
    ap.add_argument("--test-minutes", type=float, default=None, help="chạy thử 1 phiên N phút ngay bây giờ")
    ap.add_argument("--csv-report", type=Path, help="(coverage) xuất T0b_live_coverage.csv")
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(a.out / "collector.log", encoding="utf-8")])
    cfg_path = a.config or next((c for c in CONFIG_CANDIDATES if c.exists()), None)
    if cfg_path is None:
        sys.exit("Không thấy live_sessions.yaml. Đặt nó cạnh file này hoặc dùng --config <đường dẫn>.")
    LOG.info("Config: %s | Dữ liệu: %s", cfg_path, a.out)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if a.cmd == "schedule":
        tz = get_tz(cfg["timezone"])
        for s in build_schedule(cfg):
            print(s.session_id, s.start.astimezone(tz).strftime("%a %d/%m %H:%M"), "→",
                  s.end.astimezone(tz).strftime("%H:%M"), cfg["timezone"])
    elif a.cmd == "coverage":
        res = coverage(a.out, cfg["interval_s"])
        if res is not None and a.csv_report:
            res.to_csv(a.csv_report, index=False)
    else:
        a.out.mkdir(parents=True, exist_ok=True)
        run(cfg, a.out, a.format, a.test_minutes, a.min_remaining)




if __name__ == "__main__":
    sys.exit(main())





