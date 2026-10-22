"""C3.02 — Collector live (bản tối thiểu) chạy THEO PHIÊN.

Mỗi `interval_s` giây (mặc định 5s), với mỗi sàn: lấy bid, ask, bid_size, ask_size của BTC/USDT qua CCXT
(mức 1 của order book) và ghi Parquet theo ngày + theo phiên:

    data/raw/live/date=YYYY-MM-DD/session=<session_id>/part-0001.parquet ...

Quy tắc phiên:
  * Mỗi phiên là một đoạn dữ liệu riêng (cột session_id). Giữa hai phiên collector chờ, không ghi gì.
  * Không nội suy / chèn dữ liệu giả vào chỗ thiếu: lỗi mạng -> bỏ lượt đó, ghi log.
  * Thời gian luôn UTC (có timezone). ts_recv_utc = giờ máy nhận; ts_event_utc = giờ sàn (có thể trống).
  * Config chính: config/live_sessions.yaml. Venue params chỉ áp dụng cho sổ sâu.
  * Sổ sâu lỗi không tạm thời: thử sổ nông, để depth missing + depth_censored=True.
    Network/rate-limit đi backoff, không gọi thêm sổ nông. Ba lỗi sâu liên tiếp tắt sổ sâu trong phiên.
  * Size BTC lấy từ contractSize của CCXT; size_multiplier trong config chỉ kiểm tra khớp metadata.

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
import math
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
           "bid", "ask", "bid_size", "ask_size", "mid", "spread_abs", "spread_bps",
           "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "depth_censored",
           "latency_ms", "session_id", "session_start_utc", "session_end_utc", "data_kind"]
COLUMNS += ["book_levels_bid", "book_levels_ask"]
EXTRA_BANDS = (5, 2)
COLUMNS += [f"depth_{side}_{band}bps_usdt" for band in EXTRA_BANDS for side in ("bid", "ask")]
COLUMNS += ["depth_cover_bps"]
DEPTH_BPS = 10
SHALLOW_LIMIT = 5
MAX_DEEP_FAILS = 3
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
    không có thì dùng cfg['exchanges'] cũ (chỉ spot). Mỗi venue: exchange, market_type, ccxt_id, symbol."""
    if cfg.get("venues"):
        out = []
        for v in cfg["venues"]:
            out.append({"exchange": v["exchange"], "market_type": v.get("market_type", "spot"),
                        "ccxt_id": v.get("ccxt_id", v["exchange"]), "symbol": v.get("symbol", cfg["symbol"]),
                        "book_limit": v.get("book_limit", cfg["book_limit"]),
                        "size_multiplier": v.get("size_multiplier"), "params": dict(v.get("params") or {})})
        return out
    ov = cfg.get("symbol_overrides", {})
    return [{"exchange": n, "market_type": "spot", "ccxt_id": n, "symbol": ov.get(n, cfg["symbol"])}
            for n in cfg["exchanges"]]


def venue_key(v: dict) -> str:
    return f"{v['exchange']}:{v['market_type']}"


def make_exchange(name: str):
    """Tạo đối tượng CCXT theo ccxt_id. Tách riêng để test có thể thay bằng sàn giả."""
    import ccxt  # import muộn: chỉ cần khi chạy thật
    return getattr(ccxt, name)({"enableRateLimit": True, "timeout": 4000})


def fetch_book(ex, symbol: str, limit: int, size_mult=None, with_depth=True, params=None) -> dict:
    """D2 values: prices USDT, sizes BTC, depth USDT within +/-10bps of mid.

    Linear perpetual order sizes can be contracts: multiply by contractSize.
    Truncated depth remains an observed lower bound, with depth_censored=True.
    Event time is metadata; downstream availability must use ts_recv_utc.
    """
    ob = ex.fetch_order_book(symbol, limit, params) if params else ex.fetch_order_book(symbol, limit)
    if not ob.get("bids") or not ob.get("asks"):
        raise ValueError("order book rỗng")
    market = ex.market(symbol)
    if market.get("inverse") or (market.get("contract") and not market.get("linear")):
        raise ValueError("inverse contracts unsupported by BTC/USDT D2 schema")
    multiplier = float(market.get("contractSize") or 0) if market.get("contract") else 1.0
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("contractSize must be positive and finite")
    if size_mult is not None and (not math.isfinite(float(size_mult)) or not math.isclose(float(size_mult), multiplier)):
        raise ValueError("size_multiplier disagrees with market contractSize")
    bids = [(float(row[0]), float(row[1])*multiplier) for row in ob["bids"]]
    asks = [(float(row[0]), float(row[1])*multiplier) for row in ob["asks"]]
    (bid, bid_sz), (ask, ask_sz) = bids[0], asks[0]
    if not all(math.isfinite(p) and math.isfinite(q) and p > 0 and q >= 0 for p, q in bids+asks) or ask < bid:
        raise ValueError("invalid or crossed order book")
    mid = (bid+ask)/2
    depth = compute_depth(bids, asks) if with_depth else {
        "depth_bid_10bps_usdt": None, "depth_ask_10bps_usdt": None, "depth_censored": True,
        "book_levels_bid": len(bids), "book_levels_ask": len(asks)}
    if not with_depth:
        depth.update({f"depth_{side}_{band}bps_usdt": None for band in EXTRA_BANDS for side in ("bid", "ask")})
        depth["depth_cover_bps"] = None
    ts_ev = ob.get("timestamp")
    return {"bid": float(bid), "ask": float(ask), "bid_size": float(bid_sz), "ask_size": float(ask_sz),
            "mid": mid, "spread_abs": ask-bid, "spread_bps": (ask-bid)/mid*10000,
            **depth,
            "ts_event_utc": datetime.fromtimestamp(ts_ev / 1000, UTC) if ts_ev else None}


def fetch_top_of_book(ex, symbol: str, limit: int) -> dict:
    """Compatible D2 entry point retained for existing callers and review tests."""
    return fetch_book(ex, symbol, limit)


def compute_depth(bids, asks, bps=DEPTH_BPS, size_mult=1.0) -> dict:
    """Observed USDT depth; incomplete books remain censored lower bounds."""
    if bps != DEPTH_BPS:
        raise ValueError("D2 depth columns require exactly 10 bps")
    if not math.isfinite(float(size_mult)) or size_mult <= 0:
        raise ValueError("size_multiplier must be positive and finite")
    if not bids or not asks:
        raise ValueError("order book rỗng")
    bids = [(float(row[0]), float(row[1])*size_mult) for row in bids]
    asks = [(float(row[0]), float(row[1])*size_mult) for row in asks]
    if not all(math.isfinite(p) and math.isfinite(q) and p > 0 and q >= 0 for p, q in bids+asks) or asks[0][0] < bids[0][0]:
        raise ValueError("invalid or crossed order book")
    mid = (bids[0][0]+asks[0][0])/2
    lower, upper = mid*(1-bps/10000), mid*(1+bps/10000)
    result = {"depth_bid_10bps_usdt": sum(p*q for p, q in bids if p >= lower),
            "depth_ask_10bps_usdt": sum(p*q for p, q in asks if p <= upper),
            "depth_censored": bids[-1][0] >= lower or asks[-1][0] <= upper,
            "book_levels_bid": len(bids), "book_levels_ask": len(asks)}
    for band in EXTRA_BANDS:
        lo, hi = mid*(1-band/10000), mid*(1+band/10000)
        result[f"depth_bid_{band}bps_usdt"] = sum(p*q for p, q in bids if p >= lo)
        result[f"depth_ask_{band}bps_usdt"] = sum(p*q for p, q in asks if p <= hi)
    result["depth_cover_bps"] = min((mid-bids[-1][0])/mid, (asks[-1][0]-mid)/mid)*10000
    return result


def is_rate_limit(error):
    name = type(error).__name__.lower()
    return "ratelimit" in name or "ddos" in name or "429" in str(error) or "too many" in str(error).lower()


def is_transient(error):
    name = type(error).__name__.lower()
    return (isinstance(error, (TimeoutError, ConnectionError)) or is_rate_limit(error)
            or any(word in name for word in ("timeout", "network", "notavailable", "connection", "unavailable")))


class BookSampler:
    """TV3 deep-book fallback, preserving canonical D2 values and units."""
    def __init__(self, exchange, venue, writer):
        self.exchange, self.venue, self.writer = exchange, venue, writer
        self.deep_enabled = venue["book_limit"] > SHALLOW_LIMIT
        self.deep_failures = self.shallow_calls = 0

    def fetch(self):
        venue = self.venue
        try:
            quote = fetch_book(self.exchange, venue["symbol"], venue["book_limit"] if self.deep_enabled else SHALLOW_LIMIT,
                               venue.get("size_multiplier"), with_depth=self.deep_enabled,
                               params=venue.get("params") if self.deep_enabled else None)
            if self.deep_enabled:
                self.deep_failures = 0
            else:
                self.shallow_calls += 1
            return quote
        except Exception as error:
            if not self.deep_enabled or is_transient(error):
                raise
            quote = fetch_book(self.exchange, venue["symbol"], SHALLOW_LIMIT,
                               venue.get("size_multiplier"), with_depth=False)
            self.shallow_calls += 1
            self.deep_failures += 1
            self.writer.log_error(venue_key(venue), type(error).__name__, str(error),
                                  f"deep_failed_used_shallow({self.deep_failures})")
            if self.deep_failures >= MAX_DEEP_FAILS:
                self.deep_enabled = False
                self.writer.log_error(venue_key(venue), "DeepBookDisabled", f"limit={venue['book_limit']}",
                                      "deep_disabled_for_session")
            return quote


# ----------------------------------------------------------------------------- ghi file
class SessionWriter:
    def __init__(self, out_dir: Path, session: Session, tz: ZoneInfo, fmt: str):
        d = session.start.astimezone(tz).date()
        self.dir = out_dir / f"date={d:%Y-%m-%d}" / f"session={session.session_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = [int(p.stem.split("-")[1]) for p in self.dir.glob("part-*") if p.suffix in {".parquet", ".csv"} and p.stem.split("-")[1].isdigit()]
        self.fmt, self.n, self.rows, self.lock = fmt, max(existing, default=0), [], threading.Lock()
        self.total = 0

    def add(self, row: dict):
        with self.lock:
            self.rows.append(row)

    def log_error(self, venue, error_type, message, action):
        """Best-effort diagnostic log; failure never replaces a market-data error."""
        import csv
        try:
            path = self.dir / "errors.csv"
            with self.lock:
                new = not path.exists()
                with path.open("a", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    if new:
                        writer.writerow(ERROR_FIELDS)
                    writer.writerow([datetime.now(UTC).isoformat(), venue, error_type,
                                     str(message)[:300].replace("\n", " "), action])
        except OSError as error:
            LOG.warning("Cannot write collector error log: %s", error)

    def flush(self):
        with self.lock:
            rows, self.rows = self.rows, []
        if not rows:
            return
        tmp = None
        try:
            import pandas as pd
            from src.utils.schema import upgrade_d2_frame
            normalized = []
            for row in rows:
                row = dict(row)
                row.setdefault("venue", f"{row.get('exchange')}_{row.get('market_type')}")
                if "depth_censored" not in row and "depth_truncated" in row:
                    row["depth_censored"] = row["depth_truncated"]
                row.setdefault("depth_truncated", row.get("depth_censored"))
                normalized.append(row)
            df = pd.DataFrame(normalized).reindex(columns=list(dict.fromkeys(COLUMNS + [c for r in normalized for c in r])))
            for c in ("bid", "ask", "bid_size", "ask_size", "mid", "spread_abs", "spread_bps",
                      "depth_bid_10bps_usdt", "depth_ask_10bps_usdt", "latency_ms",
                      "depth_bid_5bps_usdt", "depth_ask_5bps_usdt", "depth_bid_2bps_usdt", "depth_ask_2bps_usdt", "depth_cover_bps"):
                df[c] = pd.to_numeric(df[c], errors="raise").astype("float64")
            for c in ("ts_grid_utc", "ts_recv_utc", "ts_event_utc", "session_start_utc", "session_end_utc"):
                df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[ns, UTC]")
            df = upgrade_d2_frame(df)
            self.n += 1
            path = self.dir / f"part-{self.n:04d}.{'parquet' if self.fmt == 'parquet' else 'csv'}"
            tmp = path.with_suffix(path.suffix + ".tmp")
            (df.to_parquet(tmp, index=False) if self.fmt == "parquet" else df.to_csv(tmp, index=False))
            tmp.rename(path)
        except Exception:
            with self.lock:
                self.rows = rows+self.rows
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            raise
        self.total += len(df)
        LOG.info("flush %s: +%d dòng (tổng phiên %d)", path.name, len(df), self.total)


# ----------------------------------------------------------------------------- một phiên
def _worker(venue, cfg, session, writer, stop_at, stats, stop_event=None):
    name, symbol, key = venue["exchange"], venue["symbol"], venue_key(venue)
    interval, limit = cfg["interval_s"], venue.get("book_limit", cfg["book_limit"])
    stop_event = stop_event or threading.Event()
    try:
        ex = make_exchange(venue["ccxt_id"])
    except Exception as e:  # noqa: BLE001
        LOG.error("[%s] không tạo được sàn: %s", key, e)
        writer.log_error(key, type(e).__name__, str(e), "worker_stopped")
        return
    sampler = BookSampler(ex, {**venue, "book_limit": limit}, writer)
    jitter = random.Random(f"{cfg.get('seed', '')}:{key}")
    backoff, skip_until = 0.0, 0.0
    # lưới thời gian căn theo bội số của interval (UTC epoch) để các sàn cùng nhịp
    nxt = (int(time.time() // interval) + 1) * interval
    try:
        while nxt < stop_at.timestamp() and not stop_event.is_set():
            if stop_event.wait(max(0.0, nxt - time.time())):
                break
            if time.time() >= stop_at.timestamp():
                break
            # Requests slower than a tick do not create backdated catch-up quotes.
            if time.time()-nxt >= interval:
                missed = int((time.time()-nxt)//interval)
                stats[key]["skipped"] += missed
                nxt += missed*interval
            grid = datetime.fromtimestamp(nxt, UTC)
            nxt += interval
            if time.time() < skip_until:
                stats[key]["skipped"] += 1
                continue
            t0 = time.monotonic()
            try:
                r = sampler.fetch()
                recv = datetime.now(UTC)
                writer.add({"ts_grid_utc": grid, "ts_recv_utc": recv, "exchange": name, "market_type": venue["market_type"], "symbol": symbol.split(":")[0],
                            "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                            "session_id": session.session_id, "session_start_utc": session.start,
                            "session_end_utc": session.end, "data_kind": "live", **r})
                stats[key]["ok"] += 1
                stats[key]["shallow"] = sampler.shallow_calls
                backoff = 0.0
            except Exception as e:  # noqa: BLE001
                stats[key]["err"] += 1
                rate_limited = is_rate_limit(e)
                backoff = min(60.0 if rate_limited else 30.0, max(10.0 if rate_limited else 1.0, backoff * 2))
                wait = backoff*jitter.uniform(.8, 1.2)
                skip_until = time.time() + wait
                writer.log_error(key, type(e).__name__, str(e), f"skip_tick_backoff_{wait:.0f}s")
                LOG.warning("[%s] lỗi (%s), bỏ lượt, nghỉ %.0fs: %s", key, type(e).__name__, backoff, str(e)[:160])
    finally:
        close = getattr(ex, "close", None)
        if close:
            close()


def run_session(cfg, session: Session, out_dir: Path, fmt: str):
    tz = get_tz(cfg["timezone"])
    stop_at = session.end
    LOG.info("=== BẮT ĐẦU phiên %s, kết thúc %s ===", session.session_id, stop_at.astimezone(tz))
    writer = SessionWriter(out_dir, session, tz, fmt)
    venues = get_venues(cfg)
    stats = {venue_key(v): {"ok": 0, "err": 0, "skipped": 0, "shallow": 0} for v in venues}
    stop_event = threading.Event()
    threads = [threading.Thread(target=_worker, args=(v, cfg, session, writer, stop_at, stats, stop_event), daemon=True)
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
        stop_event.set()
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


def _cover_stats(group) -> dict:
    """Độ phủ của sổ lấy về (depth_cover_bps = phía hẹp hơn, tính bps từ mid). Vùng b bps chỉ đáng tin khi cover >= b.
    Phiên cũ không có cột này -> không thêm khóa (NaN sau khi gộp), không bịa giá trị."""
    if "depth_cover_bps" not in group or not group.depth_cover_bps.notna().any():
        return {}
    cover = group.depth_cover_bps
    return {"median_cover_bps": round(float(cover.median()), 2), "pct_cover_ge_5bps": round(100 * float((cover >= 5).mean()), 1)}


# ----------------------------------------------------------------------------- coverage (C3.05 / T0b)
def coverage(out_dir: Path, interval_s: int, cfg=None, as_of=None):
    """Coverage against elapsed scheduled ticks, including zero-observation venues.

    Session end is exclusive; future ticks do not count as missing. For legacy
    files without schedule metadata or config, label the weaker observed span.
    Duplicate/off-grid rows never inflate the number of successful ticks.
    """
    import pandas as pd
    out_dir = Path(out_dir)
    as_of = pd.Timestamp(as_of or datetime.now(UTC))
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of = as_of.tz_convert("UTC")
    files = sorted(list(out_dir.rglob("part-*.parquet")) + list(out_dir.rglob("part-*.csv")))
    if not files and cfg is None:
        print("Chưa có dữ liệu trong", out_dir)
        return None
    if interval_s <= 0:
        raise ValueError("interval_s must be positive")
    df = pd.concat([pd.read_parquet(f) if f.suffix == ".parquet" else pd.read_csv(f) for f in files], ignore_index=True) if files else pd.DataFrame(columns=COLUMNS)
    for c in ("ts_grid_utc", "ts_recv_utc"):
        df[c] = pd.to_datetime(df[c], utc=True).astype("datetime64[ns, UTC]")
    rows = []
    if "market_type" not in df.columns:
        df["market_type"] = "spot"
    schedules = {s.session_id: s for s in build_schedule(cfg)} if cfg else {}
    observed = set()
    for (sid, ex, mt), g in df.groupby(["session_id", "exchange", "market_type"]):
        observed.add((sid, ex, mt))
        g = g.sort_values("ts_grid_utc")
        duplicate_rows = int(g.ts_grid_utc.duplicated().sum())
        unique = g.drop_duplicates("ts_grid_utc")
        if "session_start_utc" in g and g.session_start_utc.notna().any():
            start = pd.to_datetime(g.session_start_utc, utc=True).min()
            end = pd.to_datetime(g.session_end_utc, utc=True).max()
            basis = "scheduled_session"
        elif sid in schedules:
            start, end = pd.Timestamp(schedules[sid].start), pd.Timestamp(schedules[sid].end)
            basis = "scheduled_session"
        else:
            start, end = unique.ts_grid_utc.iloc[0], unique.ts_grid_utc.iloc[-1]+pd.Timedelta(seconds=interval_s)
            basis = "observed_span_only"
        end = min(end, as_of)
        first_tick = math.ceil(start.timestamp()/interval_s)*interval_s
        expected = max(0, math.ceil((end.timestamp()-first_tick)/interval_s))
        on_grid = unique.ts_grid_utc.astype("int64") % (interval_s*1_000_000_000) == 0
        in_session = unique[(unique.ts_grid_utc >= start) & (unique.ts_grid_utc < end) & on_grid]
        gaps = in_session.ts_grid_utc.diff().dt.total_seconds().dropna()
        big = gaps[gaps > 3 * interval_s]
        rows.append({"session_id": sid, "exchange": ex, "market_type": mt, "rows": len(g), "expected_rows": expected,
                     "unique_rows": len(in_session), "duplicate_rows": duplicate_rows, "coverage_basis": basis,
                     "off_grid_rows": int((~on_grid).sum()),
                     "coverage_pct": round(100 * len(in_session) / expected, 2) if expected else 0, "n_gaps_gt_3_ticks": len(big),
                     "max_gap_s": float(gaps.max()) if len(gaps) else 0.0,
                     "first_utc": g.ts_grid_utc.iloc[0], "last_utc": g.ts_grid_utc.iloc[-1],
                     "median_latency_ms": float(g.latency_ms.median())})
    # Include configured sessions/venues with NO observations, too.
    if cfg:
        elapsed = {sid: s for sid, s in schedules.items() if s.start < as_of.to_pydatetime()}
        for sid in set(df.session_id.unique()) | set(elapsed):
            template = next((r for r in rows if r["session_id"] == sid), None)
            if template is None:
                session = elapsed[sid]
                first_tick = math.ceil(session.start.timestamp()/interval_s)*interval_s
                expected = max(0, math.ceil((min(pd.Timestamp(session.end), as_of).timestamp()-first_tick)/interval_s))
                template = {"session_id": sid, "expected_rows": expected, "coverage_basis": "scheduled_session"}
            for venue in get_venues(cfg):
                key = (sid, venue["exchange"], venue["market_type"])
                if key not in observed:
                    rows.append({**template, "exchange": key[1], "market_type": key[2], "rows": 0, "unique_rows": 0, "duplicate_rows": 0, "off_grid_rows": 0, "coverage_pct": 0.0, "n_gaps_gt_3_ticks": int(template["expected_rows"] > 3), "max_gap_s": template["expected_rows"]*interval_s, "first_utc": None, "last_utc": None, "median_latency_ms": None})
    res = pd.DataFrame(rows)
    if not df.empty and "depth_bid_10bps_usdt" in df:
        depth_rows = []
        for (sid, ex, mt), group in df.groupby(["session_id", "exchange", "market_type"]):
            flag = "depth_censored" if "depth_censored" in group else "depth_truncated"
            depth_rows.append({"session_id": sid, "exchange": ex, "market_type": mt,
                               "depth_censored_pct": 100*group[flag].mean() if flag in group else float("nan"),
                               "median_depth_bid_usdt": group.depth_bid_10bps_usdt.median() if group.depth_bid_10bps_usdt.notna().any() else float("nan"),
                               "median_depth_ask_usdt": group.depth_ask_10bps_usdt.median() if group.depth_ask_10bps_usdt.notna().any() else float("nan")}
                              | _cover_stats(group))
        res = res.merge(pd.DataFrame(depth_rows), how="left", on=["session_id", "exchange", "market_type"])
    error_frames = [pd.read_csv(path).assign(session_id=path.parent.name.removeprefix("session="))
                    for path in out_dir.rglob("errors.csv")]
    if error_frames and not res.empty:
        errors = pd.concat(error_frames, ignore_index=True)
        if not errors.empty:
            counts = errors.groupby(["session_id", "venue"]).size().rename("n_errors").reset_index()
            counts[["exchange", "market_type"]] = counts.venue.str.split(":", n=1, expand=True)
            res = res.merge(counts[["session_id", "exchange", "market_type", "n_errors"]],
                            how="left", on=["session_id", "exchange", "market_type"])
    if "n_errors" not in res:
        res["n_errors"] = 0
    res["n_errors"] = res.n_errors.fillna(0).astype(int)
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
            print(s.session_id, s.start.astimezone(tz).strftime("%a %d/%m %H:%M"), "->",
                  s.end.astimezone(tz).strftime("%H:%M"), cfg["timezone"])
    elif a.cmd == "coverage":
        res = coverage(a.out, cfg["interval_s"], cfg)
        if res is not None and a.csv_report:
            res.to_csv(a.csv_report, index=False)
    else:
        a.out.mkdir(parents=True, exist_ok=True)
        run(cfg, a.out, a.format, a.test_minutes, a.min_remaining)


if __name__ == "__main__":
    sys.exit(main())