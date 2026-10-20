"""C3.04 (TV3) - Tai nen 1 phut BTC/USDT qua CCXT, phan trang theo `since`.

Dau ra moi venue: data/raw/ohlcv/{venue}_BTCUSDT_1m.parquet
(venue = {exchange}_{market_type}, vi du okx_spot, bybit_perp).

Quy uoc thoi gian (QUAN TRONG - xem DATA_CONTRACT v1.0)
-------------------------------------------------------
* Cot `ts_open_utc` la gio MO nen, UTC, kieu datetime64[ns, UTC].
* File tho nay CHUA co `ts_available`. TV2 (C2.03) tao:
  ts_available = ts_open_utc + 1 phut.
* Cua so mau trong config.yaml (`sample.start` / `sample.end`) tinh theo
  ts_available, start bao gom, end LOAI TRU. Gio mo nen can tai la:
  [start - 1 phut, end - 1 phut).
* Nen chua dong (gio mo + 1 phut > thoi diem tai) bi loai de tranh look-ahead.
* Cho thieu de nguyen (khong noi suy, khong lap). Bao cao trong fetch_log.

Don vi: gia = USDT; volume_base = BTC theo unified OHLCV cua CCXT cho cac
thi truong BTC/USDT spot/linear da chot; khong nhan contractSize lan hai.
CCXT OKX dung truong base volume (index 6) cho perp, khac order-book contracts.

Cach chay (tu thu muc goc repo):
    python -m src.collectors.historical.ccxt_ohlcv --smoke-hours 2      # chay thu
    python -m src.collectors.historical.ccxt_ohlcv                      # tai day du
    python -m src.collectors.historical.ccxt_ohlcv --venues okx_spot    # mot venue
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd
import yaml
from src.utils.io_utils import write_json
from src.utils.time_utils import require_utc
from src.utils.schema import VENUE_PATTERN

logger = logging.getLogger("collectors.historical.ccxt_ohlcv")

MINUTE_MS = 60_000
CANONICAL_SYMBOL = "BTC/USDT"
FILE_STEM = "BTCUSDT"
TIMEFRAME = "1m"
DATA_KIND = "historical"

# TV2 tai binance_spot bang Binance Vision (C2.01) -> bo qua mac dinh.
DEFAULT_SKIP_VENUES = ("binance_spot",)

# So nen moi lan goi. DOI CHIEU voi docs/EXCHANGE_FEASIBILITY.md (C3.01) va
# ghi de bang --limit neu khac. Vong lap khong phu thuoc gia tri nay: no di
# tiep tu nen cuoi cung thuc su nhan duoc.
DEFAULT_LIMIT_BY_CCXT_ID = {"okx": 300, "bybit": 1000, "binance": 1000, "binanceusdm": 1000}
FALLBACK_LIMIT = 100

RAW_COLUMNS = [
    "ts_open_utc", "venue", "exchange", "market_type", "symbol",
    "open", "high", "low", "close", "volume_base", "volume_quote", "data_kind",
]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Venue:
    exchange: str      # id viet thuong ghi vao du lieu
    market_type: str   # spot | perp
    ccxt_id: str       # id de ket noi (vd binanceusdm)
    symbol: str        # symbol CCXT (perp: BTC/USDT:USDT)

    def __post_init__(self):
        expected = CANONICAL_SYMBOL + (":USDT" if self.market_type == "perp" else "")
        if not VENUE_PATTERN.fullmatch(self.venue_id) or self.symbol != expected:
            raise ValueError("Venue must identify BTC/USDT spot or USDT-linear perpetual")

    @property
    def venue_id(self) -> str:
        return f"{self.exchange}_{self.market_type}"

    @property
    def file_name(self) -> str:
        return f"{self.venue_id}_{FILE_STEM}_{TIMEFRAME}.parquet"


def to_ms(ts) -> int:
    """Timestamp co timezone -> ms UTC. Cam timestamp khong timezone."""
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        raise ValueError(f"Timestamp phai co timezone (UTC), nhan duoc: {ts!r}")
    return int(round(t.tz_convert("UTC").timestamp() * 1000))


def candle_open_window(sample_start, sample_end) -> tuple[int, int]:
    """Cua so ts_available [start, end) -> cua so gio mo nen [since_ms, until_ms).

    ts_available = gio mo + 1 phut  =>  gio mo = ts_available - 1 phut.
    """
    return to_ms(sample_start) - MINUTE_MS, to_ms(sample_end) - MINUTE_MS


def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for key in ("sample", "venues"):
        if key not in cfg:
            raise KeyError(f"config.yaml thieu muc '{key}'")
    return cfg


def load_venues(exchanges_path: Path) -> list[Venue]:
    with open(exchanges_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    venues = []
    for item in raw["venues"]:
        if item["exchange"] != item["exchange"].lower():
            raise ValueError(f"exchange phai viet thuong: {item['exchange']}")
        venues.append(Venue(item["exchange"], item["market_type"], item["ccxt_id"], item["symbol"]))
    return venues


def month_chunks(since_ms: int, until_ms: int) -> list[tuple[int, int]]:
    """Cat [since, until) theo ranh gioi thang UTC (de checkpoint/resume)."""
    chunks, cur = [], since_ms
    while cur < until_ms:
        t = pd.Timestamp(cur, unit="ms", tz="UTC")
        first = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0, nanosecond=0)
        nxt = int(round((first + pd.DateOffset(months=1)).timestamp() * 1000))
        chunks.append((cur, min(nxt, until_ms)))
        cur = nxt
    return chunks


# --------------------------------------------------------------------------
# Fetch (phan trang + retry)
# --------------------------------------------------------------------------
def default_retry_exceptions() -> tuple:
    """Loi mang/tam thoi cua CCXT (RateLimitExceeded, RequestTimeout,
    ExchangeNotAvailable deu la con cua NetworkError)."""
    import ccxt
    return (ccxt.NetworkError,)


def _call_with_retry(fn: Callable, *, max_retries: int, base_delay_s: float,
                     retry_exceptions: tuple, sleep: Callable[[float], None]):
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except retry_exceptions as exc:
            if attempt == max_retries:
                raise
            delay = base_delay_s * (2 ** attempt)
            logger.warning("Loi tam thoi (%s). Thu lai sau %.1fs (lan %d/%d)",
                           exc, delay, attempt + 1, max_retries)
            sleep(delay)


def fetch_range(ex, symbol: str, since_ms: int, until_ms: int, limit: int, *,
                max_retries: int = 5, base_delay_s: float = 1.0,
                retry_exceptions: tuple = (), sleep: Callable[[float], None] = time.sleep,
                max_empty_pages: int = 100, stats: dict | None = None) -> list[list[float]]:
    """Tai nen 1m co gio mo trong [since_ms, until_ms). Don vi: ms UTC (gio MO nen).

    Tra ve list [ts_ms, o, h, l, c, v] da sap xep tang dan, da bo trung.
    Gia dinh thoi gian: chi lay nen co gio mo trong cua so; viec loai nen chua
    dong lam o `drop_unclosed`.
    * Con tro tien theo nen LON NHAT thuc su nhan duoc (khong theo `limit`),
      nen sang sau khi san tra it nen hon `limit`.
    * San bo qua `since` / tra nguoc thu tu: bi loc/sap xep lai, khong an nen.
    * Trang trong (khoang trong du lieu that, hoac con tro tre hon du lieu
      dang co): nhay `limit` phut; qua `max_empty_pages` trang trong lien tiep
      thi dung va canh bao (continuity_report se bao cao phan thieu).
    """
    if (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
            or since_ms >= until_ms or since_ms % MINUTE_MS or until_ms % MINUTE_MS):
        raise ValueError("Require positive limit and ordered minute-aligned candle bounds")
    if max_retries < 0 or max_empty_pages < 0:
        raise ValueError("Retry/empty-page counts must be nonnegative")
    stats = stats if stats is not None else {}
    stats.setdefault("duplicate_rows", 0)
    rows: dict[int, list[float]] = {}
    cursor, empty_pages = since_ms, 0
    while cursor < until_ms:
        batch = _call_with_retry(
            lambda: ex.fetch_ohlcv(symbol, TIMEFRAME, since=cursor, limit=limit),
            max_retries=max_retries, base_delay_s=base_delay_s,
            retry_exceptions=retry_exceptions, sleep=sleep)
        fresh = [c for c in (batch or []) if cursor <= c[0] < until_ms]
        if fresh:
            empty_pages = 0
            for c in fresh:
                stamp = int(c[0])
                if len(c) < 6 or stamp != c[0] or stamp % MINUTE_MS:
                    raise ValueError("Malformed or off-grid OHLCV candle")
                if stamp in rows:
                    stats["duplicate_rows"] += 1
                    if rows[stamp] != list(c[:6]):
                        raise ValueError("Conflicting duplicate OHLCV candle")
                rows[stamp] = list(c[:6])
            cursor = int(max(c[0] for c in fresh)) + MINUTE_MS
        else:
            empty_pages += 1
            if empty_pages > max_empty_pages:
                logger.warning("%s: %d trang trong lien tiep tai %s - dung tai day.",
                               symbol, empty_pages,
                               pd.Timestamp(cursor, unit="ms", tz="UTC"))
                break
            cursor += limit * MINUTE_MS
    return [rows[k] for k in sorted(rows)]


def drop_unclosed(rows: Sequence[Sequence[float]], now_ms: int) -> list:
    """Bo nen chua dong: gio mo + 1 phut > now. Giu nen chua dong = look-ahead."""
    return [r for r in rows if r[0] + MINUTE_MS <= now_ms]


# --------------------------------------------------------------------------
# Chuan hoa + kiem tra
# --------------------------------------------------------------------------
def to_frame(rows: Sequence[Sequence[float]], v: Venue) -> pd.DataFrame:
    """List nen CCXT -> DataFrame theo schema tho. Gia = USDT, float64."""
    df = pd.DataFrame([list(r[:6]) for r in rows],
                      columns=["ts_ms", "open", "high", "low", "close", "volume_base"])
    ts = pd.to_datetime(df.pop("ts_ms").astype("int64"), unit="ms", utc=True)
    out = pd.DataFrame({"ts_open_utc": ts.astype("datetime64[ns, UTC]")})
    out["venue"] = v.venue_id
    out["exchange"] = v.exchange
    out["market_type"] = v.market_type
    out["symbol"] = CANONICAL_SYMBOL
    for col in ("open", "high", "low", "close", "volume_base"):
        out[col] = df[col].astype("float64")
    # CCXT chi tra volume base. KHONG tu bia volume_quote: de NaN, TV2/TV4 quyet dinh.
    out["volume_quote"] = np.float64("nan")
    out["data_kind"] = DATA_KIND
    out = out[RAW_COLUMNS]
    validate_raw(out, v)
    return out


def validate_raw(df: pd.DataFrame, venue: Venue) -> None:
    """Validate cached/downloaded raw data without filling or relabelling it."""
    if set(RAW_COLUMNS)-set(df):
        raise ValueError("Raw OHLCV columns missing")
    require_utc(df.ts_open_utc, "ts_open_utc")
    if not df.ts_open_utc.is_unique or not df.ts_open_utc.is_monotonic_increasing:
        raise ValueError("Raw OHLCV timestamps must be unique and increasing")
    if ((df.ts_open_utc.astype('int64') % (MINUTE_MS*1_000_000)) != 0).any():
        raise ValueError("Raw OHLCV timestamps must be minute-aligned")
    for key, value in {"venue": venue.venue_id, "exchange": venue.exchange,
                       "market_type": venue.market_type, "symbol": CANONICAL_SYMBOL,
                       "data_kind": DATA_KIND}.items():
        if not df[key].eq(value).all():
            raise ValueError(f"Raw OHLCV {key} disagrees with requested venue/provenance")
    prices = df[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Raw OHLCV prices must be finite and positive")
    volume = df.volume_base.to_numpy(dtype=float)
    if not np.isfinite(volume).all() or (volume < 0).any():
        raise ValueError("Raw OHLCV volume_base must be finite nonnegative BTC")
    if ((df.high < df[["open", "close", "low"]].max(axis=1))
            | (df.low > df[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Raw OHLCV high/low do not enclose prices")


def continuity_report(df: pd.DataFrame, since_ms: int, until_ms: int) -> dict:
    """Dem nen thieu va liet ke khoang thieu. KHONG lap, KHONG noi suy."""
    if len(df):
        delta = pd.to_datetime(df["ts_open_utc"], utc=True) - pd.Timestamp("1970-01-01", tz="UTC")
        ts = np.sort((delta // pd.Timedelta(milliseconds=1)).to_numpy().astype("int64"))
    else:
        ts = np.array([], dtype="int64")
    bounds = np.concatenate([[since_ms - MINUTE_MS], ts, [until_ms]])
    gaps = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        missing = int((b - a) // MINUTE_MS) - 1
        if missing > 0:
            start = int(a) + MINUTE_MS
            gaps.append({
                "start_utc": pd.Timestamp(start, unit="ms", tz="UTC").isoformat(),
                "end_utc": pd.Timestamp(start + (missing - 1) * MINUTE_MS, unit="ms", tz="UTC").isoformat(),
                "minutes": missing,
            })
    expected = int((until_ms - since_ms) // MINUTE_MS)
    return {
        "expected": expected,
        "actual": int(len(ts)),
        "missing": int(sum(g["minutes"] for g in gaps)),
        "n_gaps": len(gaps),
        "max_gap_min": max((g["minutes"] for g in gaps), default=0),
        "gaps": gaps,
    }


def quick_sanity(df: pd.DataFrame) -> dict:
    """Kiem tra nhanh. Kiem tra day du la viec cua C2.02 (TV2)."""
    px = df[["open", "high", "low", "close"]]
    return {
        "rows_nonpositive_price": int((px <= 0).any(axis=1).sum()),
        "rows_high_lt_low": int((df["high"] < df["low"]).sum()),
        "rows_nan_price": int(px.isna().any(axis=1).sum()),
        "median_close": float(df["close"].median()) if len(df) else None,
        "median_volume_base": float(df["volume_base"].median()) if len(df) else None,
    }


def crosscheck_close(frames: dict[str, pd.DataFrame]) -> dict[str, dict]:
    """Chenh lech |close| (bps) giua moi venue va venue dau tien, tren cac phut chung.

    Perp vs spot co basis vai bps la binh thuong. Lech hang chuc bps o
    muc median nghia la nghi sai gio / lech 1 nen / sai don vi.
    """
    names = list(frames)
    if len(names) < 2:
        return {}
    ref_name = names[0]
    ref = frames[ref_name].set_index("ts_open_utc")["close"]
    out = {}
    for n in names[1:]:
        s = frames[n].set_index("ts_open_utc")["close"]
        j = pd.concat([ref, s], axis=1, join="inner", keys=["ref", "x"])
        bps = ((j["x"] / j["ref"]) - 1.0).abs() * 10_000
        out[n] = {"vs": ref_name, "common_minutes": int(len(j)),
                  "median_abs_bps": float(bps.median()) if len(j) else None,
                  "p99_abs_bps": float(bps.quantile(0.99)) if len(j) else None}
    return out


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------
def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_exchange(v: Venue, *, retry_exceptions: tuple | None = None, sleep=time.sleep):
    import ccxt
    ex = getattr(ccxt, v.ccxt_id)({"enableRateLimit": True})
    retry_exceptions = retry_exceptions if retry_exceptions is not None else default_retry_exceptions()
    _call_with_retry(ex.load_markets, max_retries=5, base_delay_s=1.0,
                     retry_exceptions=retry_exceptions, sleep=sleep)
    return ex


# --------------------------------------------------------------------------
# Thu thap mot venue
# --------------------------------------------------------------------------
def collect_venue(v: Venue, since_ms: int, until_ms: int, out_dir: Path, *,
                  limit: int | None = None, ex=None, now_ms: int | None = None,
                  retry_exceptions: tuple | None = None,
                  sleep: Callable[[float], None] = time.sleep,
                  max_retries: int = 5, max_empty_pages: int = 100) -> dict:
    """Tai [since_ms, until_ms) (gio mo nen) cho mot venue, ghi parquet + fetch_log.

    Resume: moi thang ghi mot file part (ten gom cua so ms). Part da co thi bo qua.
    Part chi duoc ghi khi thang do tai xong (ghi nguyen tu), nen chay lai an toan.
    """
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    retry_exceptions = retry_exceptions if retry_exceptions is not None else default_retry_exceptions()
    limit = DEFAULT_LIMIT_BY_CCXT_ID.get(v.ccxt_id, FALLBACK_LIMIT) if limit is None else limit
    if (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
            or since_ms >= until_ms or since_ms % MINUTE_MS or until_ms % MINUTE_MS):
        raise ValueError("Require positive limit and ordered minute-aligned candle bounds")
    parts_dir = out_dir / "_parts" / v.venue_id
    contract_size = None

    def get_ex():
        nonlocal ex, contract_size
        if ex is None:
            ex = make_exchange(v, retry_exceptions=retry_exceptions, sleep=sleep)
        markets = getattr(ex, "markets", None)
        if markets and v.symbol not in markets:
            raise ValueError(f"{v.ccxt_id} khong co symbol {v.symbol}")
        try:
            market = ex.market(v.symbol)
        except Exception:  # san gia/khong co market info
            market = {}
        if market.get("inverse") or (market.get("contract") and not market.get("linear")):
            raise ValueError("Historical D1 requires spot or linear USDT perpetual")
        if market.get("quote", "USDT") != "USDT" or market.get("base", "BTC") != "BTC":
            raise ValueError("Historical D1 requires BTC/USDT units")
        contract_size = market.get("contractSize")
        return ex

    parts = []
    fetch_stats = {"duplicate_rows": 0}
    for lo, requested_hi in month_chunks(since_ms, until_ms):
        hi = min(requested_hi, (now_ms // MINUTE_MS) * MINUTE_MS)
        if hi <= lo:
            continue
        part = parts_dir / f"{lo}_{hi}.parquet"
        receipt = part.with_suffix(".json")
        if part.exists():
            try:
                cached = pd.read_parquet(part)
                validate_raw(cached, v)
                proof = json.loads(receipt.read_text(encoding="utf-8"))
                bounded = ((cached.ts_open_utc >= pd.Timestamp(lo, unit="ms", tz="UTC"))
                           & (cached.ts_open_utc < pd.Timestamp(hi, unit="ms", tz="UTC"))).all()
                if (bounded and proof["sha256"] == sha256_file(part)
                        and proof["venue"] == v.venue_id and proof["bounds"] == [lo, hi]
                        and proof.get("volume_unit") == "BTC"
                        and continuity_report(cached, lo, hi)["missing"] == 0):
                    logger.info("[%s] resume verified complete part %s", v.venue_id, part.name)
                    parts.append(part)
                    continue
            except (ValueError, TypeError, KeyError, OSError):
                pass
            logger.warning("[%s] part incomplete/unverified; fetching again: %s", v.venue_id, part.name)
        rows = fetch_range(get_ex(), v.symbol, lo, hi, limit, max_retries=max_retries,
                           retry_exceptions=retry_exceptions, sleep=sleep,
                           max_empty_pages=max_empty_pages, stats=fetch_stats)
        n_all = len(rows)
        rows = drop_unclosed(rows, now_ms)
        if n_all - len(rows):
            logger.info("[%s] bo %d nen chua dong", v.venue_id, n_all - len(rows))
        if not rows:
            logger.warning("[%s] khong co nen nao cho %s..%s - khong ghi part, se thu lai o lan chay sau",
                           v.venue_id, pd.Timestamp(lo, unit="ms", tz="UTC"), pd.Timestamp(hi, unit="ms", tz="UTC"))
            continue
        write_parquet_atomic(to_frame(rows, v), part)
        write_json({"sha256": sha256_file(part), "venue": v.venue_id,
                    "bounds": [lo, hi], "volume_unit": "BTC"}, receipt)
        parts.append(part)
        logger.info("[%s] %s: %d nen", v.venue_id, pd.Timestamp(lo, unit="ms", tz="UTC").strftime("%Y-%m-%d"), len(rows))

    part_files = [p for p in parts if p.exists()]
    if not part_files:
        raise RuntimeError(f"[{v.venue_id}] khong tai duoc nen nao")
    df = pd.concat([pd.read_parquet(p) for p in part_files], ignore_index=True)
    df = df.sort_values("ts_open_utc").reset_index(drop=True)
    lo_ts = pd.Timestamp(since_ms, unit="ms", tz="UTC")
    hi_ts = pd.Timestamp(until_ms, unit="ms", tz="UTC")
    df = df[(df["ts_open_utc"] >= lo_ts) & (df["ts_open_utc"] < hi_ts)].reset_index(drop=True)
    df = df[df.ts_open_utc.le(pd.Timestamp(now_ms-MINUTE_MS, unit="ms", tz="UTC"))].reset_index(drop=True)
    df["ts_open_utc"] = df["ts_open_utc"].astype("datetime64[ns, UTC]")
    validate_raw(df, v)
    if df.empty:
        raise RuntimeError(f"[{v.venue_id}] no closed candles in the requested interval")

    report = continuity_report(df, since_ms, until_ms)
    final = (out_dir if report["missing"] == 0 else out_dir/"_incomplete") / v.file_name
    write_parquet_atomic(df, final)

    try:
        import ccxt
        ccxt_version = ccxt.__version__
    except Exception:
        ccxt_version = None
    log = {
        "venue": v.venue_id, "ccxt_id": v.ccxt_id, "symbol_ccxt": v.symbol,
        "ccxt_version": ccxt_version,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "candle_open_window_utc": [lo_ts.isoformat(), hi_ts.isoformat()],
        "limit_per_call": limit, "contract_size": contract_size,
        "volume_unit": "BTC", "duplicate_rows": fetch_stats["duplicate_rows"],
        "status": "complete" if report["missing"] == 0 else "incomplete",
        "first_open_utc": df["ts_open_utc"].iloc[0].isoformat() if len(df) else None,
        "last_open_utc": df["ts_open_utc"].iloc[-1].isoformat() if len(df) else None,
        **{k: v_ for k, v_ in report.items() if k != "gaps"},
        "gaps_first_200": report["gaps"][:200],
        **quick_sanity(df),
        "file": final.relative_to(out_dir).as_posix(), "sha256": sha256_file(final),
    }
    write_json(log, out_dir / f"fetch_log_{v.venue_id}.json")
    logger.info("[%s] xong: %d/%d nen, thieu %d (%d khoang), sha256=%s...",
                v.venue_id, report["actual"], report["expected"], report["missing"],
                report["n_gaps"], log["sha256"][:12])
    return {**log, "_frame": df}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[3]
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=repo / "config" / "config.yaml")
    p.add_argument("--exchanges", type=Path, default=repo / "config" / "exchanges.yaml")
    p.add_argument("--out-dir", type=Path, default=repo / "data" / "raw" / "ohlcv")
    p.add_argument("--venues", nargs="*", help="id venue, vd okx_spot bybit_perp (mac dinh: tat ca tru venue TV2 lo)")
    p.add_argument("--include-skipped", action="store_true",
                   help=f"gom ca {', '.join(DEFAULT_SKIP_VENUES)} (mac dinh bo vi TV2 tai bang Binance Vision)")
    p.add_argument("--limit", type=int, default=None, help="so nen moi lan goi (ghi de mac dinh theo san)")
    p.add_argument("--smoke-hours", type=float, default=None,
                   help="chay thu: chi tai N gio dau cua mau, ghi vao <out-dir>/_smoke")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config(args.config)
    venues = load_venues(args.exchanges)
    cfg_ids = list(cfg["venues"])
    if cfg_ids != [v.venue_id for v in venues]:
        p.error("venues trong config.yaml va exchanges.yaml phai khop nhau")

    if args.venues:
        wanted = set(args.venues)
        unknown = wanted - {v.venue_id for v in venues}
        if unknown:
            p.error(f"venue khong co trong exchanges.yaml: {sorted(unknown)}")
        venues = [v for v in venues if v.venue_id in wanted]
    elif not args.include_skipped:
        venues = [v for v in venues if v.venue_id not in DEFAULT_SKIP_VENUES]

    since_ms, until_ms = candle_open_window(cfg["sample"]["start"], cfg["sample"]["end"])
    out_dir = args.out_dir
    if args.smoke_hours:
        until_ms = since_ms + int(args.smoke_hours * 3600 * 1000)
        out_dir = out_dir / "_smoke"
    logger.info("Venues: %s | gio mo nen [%s, %s)", [v.venue_id for v in venues],
                pd.Timestamp(since_ms, unit="ms", tz="UTC"), pd.Timestamp(until_ms, unit="ms", tz="UTC"))

    frames, failed = {}, []
    for v in venues:
        try:
            res = collect_venue(v, since_ms, until_ms, out_dir, limit=args.limit)
            frames[v.venue_id] = res["_frame"]
            if res["status"] != "complete":
                failed.append(v.venue_id)
        except Exception:
            logger.exception("[%s] THAT BAI - cac venue khac van tiep tuc", v.venue_id)
            failed.append(v.venue_id)

    for name, stats in crosscheck_close(frames).items():
        logger.info("crosscheck %s vs %s: %d phut chung, median |diff|=%.2f bps, p99=%.2f bps",
                    name, stats["vs"], stats["common_minutes"], stats["median_abs_bps"], stats["p99_abs_bps"])
    if failed:
        logger.error("Venue that bai: %s", failed)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
