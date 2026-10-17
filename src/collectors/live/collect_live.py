"""C3.02 — Collector live (bản tối thiểu) chạy THEO PHIÊN.

Mỗi `interval_s` giây (mặc định 5s), với mỗi sàn: lấy bid, ask, bid_size, ask_size của BTC/USDT qua CCXT
(mức 1 của order book) và ghi Parquet theo ngày + theo phiên:

    data/raw/live/date=YYYY-MM-DD/session=<session_id>/part-0001.parquet ...

Quy tắc phiên:
  * Mỗi phiên là một đoạn dữ liệu riêng (cột session_id). Giữa hai phiên collector chờ, không ghi gì.
  * Không nội suy / chèn dữ liệu giả vào chỗ thiếu: lỗi mạng -> bỏ lượt đó, ghi log.
  * Thời gian luôn UTC (có timezone). ts_recv_utc = giờ máy nhận; ts_event_utc = giờ sàn (có thể trống).

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

COLUMNS = ["ts_grid_utc", "ts_recv_utc", "ts_event_utc", "exchange", "symbol",
           "bid", "ask", "bid_size", "ask_size", "latency_ms", "session_id", "data_kind"]


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
def make_exchange(name: str):
    """Tạo đối tượng CCXT. Tách riêng để test có thể thay bằng sàn giả."""
    import ccxt  # import muộn: chỉ cần khi chạy thật
    return getattr(ccxt, name)({"enableRateLimit": True, "timeout": 4000})


def fetch_top_of_book(ex, symbol: str, limit: int) -> dict:
    ob = ex.fetch_order_book(symbol, limit)
    if not ob.get("bids") or not ob.get("asks"):
        raise ValueError("order book rỗng")
    (bid, bid_sz), (ask, ask_sz) = ob["bids"][0][:2], ob["asks"][0][:2]
    ts_ev = ob.get("timestamp")
    return {"bid": float(bid), "ask": float(ask), "bid_size": float(bid_sz), "ask_size": float(ask_sz),
            "ts_event_utc": datetime.fromtimestamp(ts_ev / 1000, UTC) if ts_ev else None}


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
def _worker(name, cfg, session, writer, stop_at, stats):
    symbol = cfg.get("symbol_overrides", {}).get(name, cfg["symbol"])
    interval, limit = cfg["interval_s"], cfg["book_limit"]
    try:
        ex = make_exchange(name)
    except Exception as e:  # noqa: BLE001
        LOG.error("[%s] không tạo được sàn: %s", name, e)
        return
    backoff, skip_until = 0.0, 0.0
    # lưới thời gian căn theo bội số của interval (UTC epoch) để các sàn cùng nhịp
    nxt = (int(time.time() // interval) + 1) * interval
    while nxt < stop_at.timestamp():
        time.sleep(max(0.0, nxt - time.time()))
        grid = datetime.fromtimestamp(nxt, UTC)
        nxt += interval
        if time.time() < skip_until:
            stats[name]["skipped"] += 1
            continue
        t0 = time.time()
        try:
            r = fetch_top_of_book(ex, symbol, limit)
            recv = datetime.now(UTC)
            writer.add({"ts_grid_utc": grid, "ts_recv_utc": recv, "exchange": name, "symbol": symbol,
                        "latency_ms": round((time.time() - t0) * 1000, 1),
                        "session_id": session.session_id, "data_kind": "live", **r})
            stats[name]["ok"] += 1
            backoff = 0.0
        except Exception as e:  # noqa: BLE001 — mọi lỗi mạng/sàn: bỏ lượt, KHÔNG chèn dữ liệu giả
            stats[name]["err"] += 1
            backoff = min(30.0, max(1.0, backoff * 2))
            skip_until = time.time() + backoff
            LOG.warning("[%s] lỗi (%s), bỏ lượt, nghỉ %.0fs: %s", name, type(e).__name__, backoff, str(e)[:160])


def run_session(cfg, session: Session, out_dir: Path, fmt: str):
    tz = get_tz(cfg["timezone"])
    stop_at = session.end
    LOG.info("=== BẮT ĐẦU phiên %s, kết thúc %s ===", session.session_id, stop_at.astimezone(tz))
    writer = SessionWriter(out_dir, session, tz, fmt)
    stats = {n: {"ok": 0, "err": 0, "skipped": 0} for n in cfg["exchanges"]}
    threads = [threading.Thread(target=_worker, args=(n, cfg, session, writer, stop_at, stats), daemon=True)
               for n in cfg["exchanges"]]
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
    for (sid, ex), g in df.groupby(["session_id", "exchange"]):
        g = g.sort_values("ts_grid_utc")
        span = (g.ts_grid_utc.iloc[-1] - g.ts_grid_utc.iloc[0]).total_seconds()
        expected = int(span // interval_s) + 1
        gaps = g.ts_grid_utc.diff().dt.total_seconds().dropna()
        big = gaps[gaps > 3 * interval_s]
        rows.append({"session_id": sid, "exchange": ex, "rows": len(g), "expected_rows": expected,
                     "coverage_pct": round(100 * len(g) / expected, 2), "n_gaps_gt_3_ticks": len(big),
                     "max_gap_s": float(gaps.max()) if len(gaps) else 0.0,
                     "first_utc": g.ts_grid_utc.iloc[0], "last_utc": g.ts_grid_utc.iloc[-1],
                     "median_latency_ms": float(g.latency_ms.median())})
    res = pd.DataFrame(rows)
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