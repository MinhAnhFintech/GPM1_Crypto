"""C3.04 — Tải nến 1 phút BTC/USDT qua CCXT cho 5 venue: okx_spot, okx_perp, bybit_spot, bybit_perp, binance_perp.




Chỉ binance_spot do TV2 tải bằng Binance Vision (C2.01); script tự bỏ qua venue này trừ khi dùng --include-binance-spot
(chỉ để đối chiếu quy ước thời gian với file của TV2). binance_perp vẫn do script này tải.




Đọc config/config.yaml (venues, symbol, sample) và config/exchanges.yaml (ccxt_id, symbol kết nối) — không gõ cứng.
Mỗi venue cho ra:
    data/raw/ohlcv/{venue}_BTCUSDT_1m.parquet       dữ liệu thô: ts_open_utc, exchange, market_type, symbol, open, high,
                                                    low, close, volume_base, volume_quote, data_kind="historical"
    data/raw/ohlcv_meta/{venue}_BTCUSDT_1m_gaps.csv     các khoảng nến THIẾU (không nội suy, không điền giả) -> T0 của TV2
    data/raw/ohlcv_meta/{venue}_BTCUSDT_1m_report.json  số dòng, khoảng thời gian, thiếu, trùng, sha256 -> SOURCE_LOG
    data/raw/ohlcv_parts/{venue}/                       file tạm theo tháng, cho phép chạy tiếp khi bị ngắt
(file phụ để ngoài data/raw/ohlcv/ để run_all --mode raw chỉ thấy đúng các file {venue}_BTCUSDT_1m.parquet).




volume_quote (khối lượng theo USDT, cột bắt buộc của D1): hàm fetch_ohlcv thống nhất của CCXT KHÔNG trả cột này, nên
với okx / bybit / binance (perp) script gọi thẳng endpoint nến của sàn và lấy số sàn báo (OKX volCcyQuote, Bybit turnover,
Binance quoteVolume). Không ước lượng bằng volume x giá. Sàn chưa có bộ đọc riêng (hoặc --no-quote) -> volume_quote = NaN.
Với OKX: spot volume_base = vol (BTC); perp volume_base = volCcy (BTC, không phải số hợp đồng ở vol).
volCcy của spot là USDT (trùng volCcyQuote), nên không dùng làm base cho spot.




Quy ước thời gian (chống look-ahead):
  * ts_open_utc = giờ MỞ nến (UTC, có timezone). ts_available = ts_open_utc + 1 phút do TV2 tính ở C2.03; script KHÔNG tự tính.
  * `sample` trong config.yaml tính theo ts_available (start gồm, end KHÔNG gồm). Script đổi sang giờ mở nến:
    ts_open_start = sample.start - 1 phút, ts_open_end(không gồm) = sample.end - 1 phút.
    Ví dụ sample 2025-10-18T00:01Z .. 2026-10-01T00:00Z -> nến mở đầu 2025-10-18 00:00, nến mở cuối 2026-09-30 23:58.
  * Nến phút hiện tại (chưa đóng) bị loại: chỉ giữ nến có ts_open + 60s <= bây giờ.
  * --start/--end trên dòng lệnh là giờ MỞ nến (start gồm, end không gồm); ngày không kèm giờ = 00:00 UTC.
  * Symbol trong dữ liệu là BTC/USDT (chuẩn); symbol CCXT (vd BTC/USDT:USDT cho perp) chỉ dùng để kết nối.




Dùng:
    python ccxt_ohlcv.py                                   # 5 venue (mọi venue trừ binance_spot), khoảng mẫu theo config
    python ccxt_ohlcv.py --venues okx_spot --start 2026-10-01 --end 2026-10-02 --out data/raw/ohlcv_probe   # thử nhanh
"""
from __future__ import annotations




import argparse
import hashlib
import json
import logging
import random
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd




HERE = Path(__file__).resolve().parent








def _find_project_root() -> Path:
    for p in [HERE, *HERE.parents]:
        if any((p / m).exists() for m in (".git", "config", "data", ".venv")):
            return p
    return Path.cwd()








PROJECT_ROOT = _find_project_root()
DEFAULT_OUT = PROJECT_ROOT / "data" / "raw" / "ohlcv"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "config.yaml"
DEFAULT_EXCHANGES = PROJECT_ROOT / "config" / "exchanges.yaml"




LOG = logging.getLogger("ccxt_ohlcv")
UTC = timezone.utc
MINUTE_MS = 60_000
COLUMNS = ["ts_open_utc", "exchange", "market_type", "symbol", "open", "high", "low", "close", "volume_base",
           "volume_quote", "data_kind"]
PRICE_COLS = ["open", "high", "low", "close"]
# Số nến tối đa mỗi lần gọi, theo ccxt_id (mặc định thận trọng). Ghi đè bằng --limit theo số đo ở C3.01.
DEFAULT_LIMIT = {"okx": 300, "bybit": 1000, "binance": 1000, "binanceusdm": 1000}
SKIP_BY_DEFAULT = {"binance_spot"}   # TV2 tải bằng Binance Vision (C2.01)
FALLBACK_LIMIT = 300
HISTORY_LATE_FLAG_MIN = 60   # nến đầu tiên muộn hơn --start quá bấy nhiêu phút -> cờ history_starts_later








# ----------------------------------------------------------------------------- thời gian
def floor_minute_ms(ts_ms: int) -> int:
    return (int(ts_ms) // MINUTE_MS) * MINUTE_MS








def ceil_minute_ms(ts_ms: int) -> int:
    return -((-int(ts_ms)) // MINUTE_MS) * MINUTE_MS








def parse_utc_ms(value) -> int:
    """'2026-09-01' / '2026-09-01T12:30:00Z' / datetime / số ms -> mili giây UTC. Không có timezone = UTC."""
    if isinstance(value, (int, float)):
        return int(value)
    ts = pd.Timestamp(value)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return int(ts.timestamp() * 1000)








def ms_to_dt(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, UTC)








def sample_to_open_range(sample_start, sample_end) -> tuple[int, int]:
    """sample (ts_available, start gồm / end không gồm) -> khoảng GIỜ MỞ NẾN [start_open, end_open) cần tải.
    ts_available = ts_open + 1 phút  =>  ts_open = ts_available - 1 phút."""
    return parse_utc_ms(sample_start) - MINUTE_MS, parse_utc_ms(sample_end) - MINUTE_MS








def month_windows(start_ms: int, end_ms: int) -> list[tuple[str, int, int]]:
    """Chia [start, end) thành các đoạn theo tháng lịch UTC: [(nhãn 'YYYY-MM', start_ms, end_ms), ...]."""
    out, cur, cur_ms = [], ms_to_dt(start_ms), start_ms
    while cur_ms < end_ms:
        nxt = datetime(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1, tzinfo=UTC)
        nxt_ms = int(nxt.timestamp() * 1000)
        out.append((f"{cur:%Y-%m}", cur_ms, min(nxt_ms, end_ms)))
        cur, cur_ms = nxt, nxt_ms
    return out








# ----------------------------------------------------------------------------- retry
def is_rate_limit(e: Exception) -> bool:
    n = type(e).__name__.lower()
    return "ratelimit" in n or "ddos" in n or "too many" in str(e).lower() or "429" in str(e)








def is_retryable(e: Exception) -> bool:
    """Lỗi tạm thời (mạng, timeout, sàn bận, giới hạn tốc độ) -> thử lại. Lỗi cấu hình (BadSymbol, Auth...) -> không."""
    n = type(e).__name__.lower()
    return (isinstance(e, (TimeoutError, ConnectionError)) or is_rate_limit(e)
            or any(k in n for k in ("timeout", "network", "notavailable", "connection", "unavailable")))








def with_retry(fn, *, max_retries: int = 6, base_s: float = 1.0, sleep=time.sleep):
    """Gọi fn(); lỗi tạm thời thì nghỉ theo cấp số nhân (có jitter) rồi thử lại; hết lượt hoặc lỗi cấu hình thì ném lỗi."""
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if attempt >= max_retries or not is_retryable(e):
                raise
            wait = min(60.0, max(10.0 if is_rate_limit(e) else base_s, base_s * 2 ** attempt))
            wait *= 0.8 + 0.4 * random.random()
            LOG.warning("lỗi tạm thời (%s), thử lại lần %d/%d sau %.1fs: %s", type(e).__name__, attempt + 1,
                        max_retries, wait, str(e)[:160])
            sleep(wait)
    raise RuntimeError("unreachable")








# ----------------------------------------------------------------------------- tải một đoạn
def fetch_range(ex, symbol: str, start_ms: int, end_ms: int, limit: int, *, use_until: bool = True,
                sleep=time.sleep, progress=None, page_fn=None) -> list[list]:
    """Phân trang theo `since` cho đoạn [start_ms, end_ms). Trả về list nến [ts, o, h, l, c, v(, quote)] (chưa lọc trùng).




    Mỗi trang xin đúng `limit` phút: since .. since+limit*60s-1, nên không phụ thuộc việc sàn trả nến cũ nhất hay mới
    nhất. Mỗi vòng since luôn tăng (hoặc nhảy sang cửa sổ kế tiếp) nên không thể lặp vô hạn. Nến ngoài [start, end) bị
    loại. Không nội suy.
      * Mặc định gọi ex.fetch_ohlcv (CCXT thống nhất; use_until gửi tham số `until`). Trang trống -> gọi thêm một lần
        KHÔNG có until để nhảy tới nến đầu tiên còn dữ liệu; vẫn trống thì dừng.
      * page_fn(ex, symbol, since, limit, until_inclusive) -> list nến 7 cột [ts,o,h,l,c,v_base,v_quote] (bộ đọc endpoint
        thô của sàn, có volume_quote). Trang trống -> nhảy sang cửa sổ kế tiếp (đã bị chặn bởi end_ms).
    """
    rows: list[list] = []
    since, window, pages = start_ms, limit * MINUTE_MS, 0
    while since < end_ms:
        pages += 1
        if page_fn is not None:
            batch = with_retry(lambda: page_fn(ex, symbol, since, limit, since + window - 1), sleep=sleep)
            new = [c for c in (batch or []) if c and since <= c[0] < end_ms]
            if not new:
                since += window
                continue
        else:
            params = {"until": since + window - 1} if use_until else {}
            batch = with_retry(lambda: ex.fetch_ohlcv(symbol, "1m", since, limit, params), sleep=sleep)
            new = [c for c in (batch or []) if c and since <= c[0] < end_ms]
            if not new and use_until:
                batch = with_retry(lambda: ex.fetch_ohlcv(symbol, "1m", since, limit, {}), sleep=sleep)
                new = [c for c in (batch or []) if c and since <= c[0] < end_ms]
                if new:
                    LOG.info("[%s] cửa sổ từ %s trống, nhảy tới nến %s", symbol, ms_to_dt(since),
                             ms_to_dt(min(c[0] for c in new)))
            if not new:
                break
        rows.extend(new)
        since = max(c[0] for c in new) + MINUTE_MS
        if progress and pages % 50 == 0:
            progress(pages, ms_to_dt(since), len(rows))
    return rows








# ----------------------------------------------------------------------------- bộ đọc endpoint thô (có volume_quote)
def _okx_page(ex, symbol, since, limit, until):
    """OKX history-candles: [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], MỚI nhất trước; `after` = lấy nến cũ hơn ts.
    Ý nghĩa cột khối lượng phụ thuộc loại sản phẩm: SPOT: vol = BTC, volCcy = USDT (trùng volCcyQuote);
    SWAP: vol = số HỢP ĐỒNG, volCcy = BTC. Nên volume_base = vol (spot) hoặc volCcy (hợp đồng); volume_quote = volCcyQuote.
    confirm=0: nến chưa đóng."""
    base_idx = 6 if ex.market(symbol).get("contract") else 5
    res = ex.publicGetMarketHistoryCandles({"instId": ex.market(symbol)["id"], "bar": "1m",
                                            "after": str(until + 1), "limit": str(limit)})
    out = [[int(r[0]), r[1], r[2], r[3], r[4], r[base_idx], r[7]] for r in (res.get("data") or []) if str(r[8]) != "0"]
    return sorted(out, key=lambda c: c[0])








def _bybit_page(ex, symbol, since, limit, until):
    """Bybit v5 kline: [start,o,h,l,c,volume,turnover], MỚI nhất trước; volume theo BTC, turnover theo USDT."""
    m = ex.market(symbol)
    res = ex.publicGetV5MarketKline({"category": "spot" if m.get("spot") else "linear", "symbol": m["id"],
                                     "interval": "1", "start": since, "end": until, "limit": limit})
    lst = (res.get("result") or {}).get("list") or []
    return sorted([[int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6]] for r in lst], key=lambda c: c[0])








def _binance_page(ex, symbol, since, limit, until):
    """Binance spot klines: [openTime,o,h,l,c,volume,closeTime,quoteVolume,...]."""
    res = ex.publicGetKlines({"symbol": ex.market(symbol)["id"], "interval": "1m", "startTime": since,
                              "endTime": until, "limit": min(limit, 1000)})
    return [[int(r[0]), r[1], r[2], r[3], r[4], r[5], r[7]] for r in res]








def _binanceusdm_page(ex, symbol, since, limit, until):
    """Binance USD-M futures klines (cùng bố cục với spot)."""
    res = ex.fapiPublicGetKlines({"symbol": ex.market(symbol)["id"], "interval": "1m", "startTime": since,
                                  "endTime": until, "limit": min(limit, 1500)})
    return [[int(r[0]), r[1], r[2], r[3], r[4], r[5], r[7]] for r in res]








RAW_PAGE = {"okx": _okx_page, "bybit": _bybit_page, "binance": _binance_page, "binanceusdm": _binanceusdm_page}








# ----------------------------------------------------------------------------- chuẩn hóa / kiểm tra
def empty_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_open_utc": pd.Series([], dtype="datetime64[ns, UTC]"), "exchange": pd.Series([], dtype="object"),
        "market_type": pd.Series([], dtype="object"), "symbol": pd.Series([], dtype="object"),
        **{c: pd.Series([], dtype="float64") for c in PRICE_COLS},
        "volume_base": pd.Series([], dtype="float64"), "volume_quote": pd.Series([], dtype="float64"),
        "data_kind": pd.Series([], dtype="object")})[COLUMNS]








def to_frame(rows: list[list], exchange: str, market_type: str, symbol: str) -> tuple[pd.DataFrame, int]:
    """list nến -> DataFrame đúng schema thô. Mỗi nến 6 cột (CCXT) hoặc 7 cột (có volume_quote từ endpoint thô).
    Loại nến trùng (giữ bản tải sau), sắp xếp tăng dần. Trả về (df, số_dòng_trùng_đã_loại).
    ts_open_utc = giờ MỞ nến, datetime64[ns, UTC]. Thiếu volume_quote thì để NaN, không ước lượng."""
    if not rows:
        return empty_frame(), 0
    recs = [list(r[:7]) + [None] * (7 - len(r[:7])) for r in rows]
    raw = pd.DataFrame(recs, columns=["ts", "open", "high", "low", "close", "volume_base", "volume_quote"])
    n0 = len(raw)
    raw["ts"] = pd.to_numeric(raw["ts"]).astype("int64")
    raw = raw.sort_values("ts", kind="stable").drop_duplicates("ts", keep="last")
    for c in PRICE_COLS + ["volume_base", "volume_quote"]:
        raw[c] = pd.to_numeric(raw[c], errors="coerce").astype("float64")
    df = pd.DataFrame({"ts_open_utc": pd.to_datetime(raw["ts"], unit="ms", utc=True).astype("datetime64[ns, UTC]"),
                       "exchange": exchange, "market_type": market_type, "symbol": symbol,
                       **{c: raw[c].to_numpy() for c in PRICE_COLS},
                       "volume_base": raw["volume_base"].to_numpy(), "volume_quote": raw["volume_quote"].to_numpy(),
                       "data_kind": "historical"})
    return df[COLUMNS].reset_index(drop=True), n0 - len(raw)








def _ts_ms(series: pd.Series):
    return series.astype("datetime64[ns, UTC]").astype("int64") // 1_000_000








def find_gaps(df: pd.DataFrame, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Các khoảng nến thiếu trong [start, end): đầu (leading), giữa (interior), cuối (trailing).
    Cột: kind, gap_start_utc (phút thiếu đầu tiên), gap_end_utc (phút thiếu cuối cùng), n_missing. Không điền gì vào đó."""
    cols = ["kind", "gap_start_utc", "gap_end_utc", "n_missing"]
    recs = []
    if df.empty:
        n = max(0, (end_ms - start_ms) // MINUTE_MS)
        if n:
            recs.append(("all_missing", start_ms, end_ms - MINUTE_MS, n))
    else:
        ts = _ts_ms(df["ts_open_utc"]).to_numpy()
        if ts[0] > start_ms:
            recs.append(("leading", start_ms, ts[0] - MINUTE_MS, (ts[0] - start_ms) // MINUTE_MS))
        for a, b in zip(ts[:-1], ts[1:]):
            if b - a > MINUTE_MS:
                recs.append(("interior", a + MINUTE_MS, b - MINUTE_MS, (b - a) // MINUTE_MS - 1))
        if ts[-1] < end_ms - MINUTE_MS:
            recs.append(("trailing", ts[-1] + MINUTE_MS, end_ms - MINUTE_MS, (end_ms - MINUTE_MS - ts[-1]) // MINUTE_MS))
    out = pd.DataFrame(recs, columns=["kind", "s", "e", "n_missing"])
    if out.empty:
        return pd.DataFrame(columns=cols)
    out["gap_start_utc"] = pd.to_datetime(out["s"].astype("int64"), unit="ms", utc=True)
    out["gap_end_utc"] = pd.to_datetime(out["e"].astype("int64"), unit="ms", utc=True)
    out["n_missing"] = out["n_missing"].astype("int64")
    return out[cols]








# ----------------------------------------------------------------------------- ghi / đọc file
def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    """Ghi nguyên tử: ghi .tmp rồi đổi tên, file hỏng giữa chừng không bao giờ mang tên chính thức."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        df.to_parquet(tmp, index=False)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise








def _read_parquet(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path)








def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()








def compact_symbol(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", symbol.split(":")[0])








def venue_name(v: dict) -> str:
    return f"{v['exchange']}_{v['market_type']}"








def make_exchange(ccxt_id: str):
    import ccxt  # import muộn: chỉ cần khi chạy thật
    return getattr(ccxt, ccxt_id)({"enableRateLimit": True, "timeout": 20000})








def _py(v):
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat()
    if hasattr(v, "item"):
        return v.item()
    return v








def ensure_markets(ex, sleep=time.sleep) -> None:
    """Tải danh sách thị trường của sàn một lần (CCXT cần để tra id, spot/perp, contractSize). fetch_ohlcv tự làm việc này,
    nhưng các hàm đọc endpoint thô gọi ex.market() trực tiếp nên phải gọi trước, nếu không báo 'markets not loaded'."""
    if getattr(ex, "markets", None) or not hasattr(ex, "load_markets"):
        return
    with_retry(lambda: ex.load_markets(), sleep=sleep)








def _contract_size(ex, symbol: str):
    """contractSize nếu là hợp đồng (perp), None nếu là spot hoặc không tra được."""
    try:
        m = ex.market(symbol)
        return float(m["contractSize"]) if m.get("contract") and m.get("contractSize") else None
    except Exception:  # noqa: BLE001
        return None








# ----------------------------------------------------------------------------- một venue
def download_venue(venue: dict, symbol: str, start_ms: int, end_ms: int, out_dir: Path, limit: int, *,
                   ex=None, use_until: bool = True, fresh: bool = False, now_ms: int | None = None,
                   sleep=time.sleep, parts_dir: Path | None = None, meta_dir: Path | None = None,
                   with_quote: bool = True, page_fn=None) -> dict:
    """Tải [start, end) (giờ MỞ nến) cho một venue, ghi file thô + gaps + report; trả về dict report.
    venue = {exchange, market_type, ccxt_id, symbol(CCXT)}; `symbol` là symbol chuẩn ghi vào dữ liệu (BTC/USDT)."""
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    cutoff = floor_minute_ms(now_ms)             # nến mở tại cutoff là phút đang chạy, chưa đóng -> loại
    start_ms, end_ms = ceil_minute_ms(start_ms), min(floor_minute_ms(end_ms), cutoff)
    if end_ms <= start_ms:
        raise ValueError(f"khoảng thời gian rỗng sau khi bỏ nến chưa đóng: {ms_to_dt(start_ms)} .. {ms_to_dt(end_ms)}")
    vname, out_dir = venue_name(venue), Path(out_dir)
    parts_dir = (Path(parts_dir) if parts_dir else out_dir.parent / "ohlcv_parts") / vname
    meta_dir = Path(meta_dir) if meta_dir else out_dir.parent / "ohlcv_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    if fresh:
        shutil.rmtree(parts_dir, ignore_errors=True)
    ex = ex if ex is not None else make_exchange(venue["ccxt_id"])
    ensure_markets(ex, sleep)
    ccxt_symbol = venue["symbol"]
    page_fn = page_fn if page_fn is not None else (RAW_PAGE.get(venue["ccxt_id"]) if with_quote else None)
    if page_fn is None and with_quote:
        LOG.warning("[%s] chưa có bộ đọc endpoint thô cho ccxt_id=%s: volume_quote sẽ là NaN", vname, venue["ccxt_id"])




    frames, dups, reused = [], 0, 0
    for label, s, e in month_windows(start_ms, end_ms):
        part = parts_dir / f"{label}__{s}_{e}.parquet"
        if part.exists():
            df = _read_parquet(part)
            reused += 1
            LOG.info("[%s] %s: dùng lại file tạm (%d dòng)", vname, label, len(df))
        else:
            LOG.info("[%s] %s: tải %s -> %s", vname, label, ms_to_dt(s), ms_to_dt(e))
            rows = fetch_range(ex, ccxt_symbol, s, e, limit, use_until=use_until, sleep=sleep, page_fn=page_fn,
                               progress=lambda p, t, n: LOG.info("[%s] %d trang, tới %s, %d nến", vname, p, t, n))
            df, d = to_frame(rows, venue["exchange"], venue["market_type"], symbol)
            dups += d
            if len(df):
                _write_parquet(df, part)
        frames.append(df)




    frames = [f for f in frames if len(f)]
    if not frames:
        raise RuntimeError(f"{vname}: không tải được nến nào trong {ms_to_dt(start_ms)} .. {ms_to_dt(end_ms)}")
    full = pd.concat(frames, ignore_index=True).sort_values("ts_open_utc", kind="stable")
    n_before = len(full)
    full = full.drop_duplicates("ts_open_utc", keep="last")
    dups += n_before - len(full)
    t_ms = _ts_ms(full["ts_open_utc"])
    full = full[(t_ms >= start_ms) & (t_ms < end_ms)].reset_index(drop=True)
    full["data_kind"] = "historical"
    full = full[COLUMNS]




    base = f"{vname}_{compact_symbol(symbol)}_1m"
    final = out_dir / f"{base}.parquet"
    _write_parquet(full, final)
    gaps = find_gaps(full, start_ms, end_ms)
    gaps.to_csv(meta_dir / f"{base}_gaps.csv", index=False)




    expected = (end_ms - start_ms) // MINUTE_MS
    first_ms = int(_ts_ms(full["ts_open_utc"]).iloc[0])
    delay_min = (first_ms - start_ms) / MINUTE_MS
    try:
        import ccxt
        ccxt_version = getattr(ccxt, "__version__", None)
    except Exception:  # noqa: BLE001
        ccxt_version = None
    px, csize = full[PRICE_COLS], _contract_size(ex, ccxt_symbol)
    # Kiểm tra chéo: volume_quote / volume_base là giá bình quân của phút đó nên phải nằm trong [low, high].
    # Nếu volume_base đang là số hợp đồng (perp) hoặc sai đơn vị thì tỷ lệ này lệch hẳn.
    ratio = full["volume_quote"] / full["volume_base"].where(full["volume_base"] > 0)
    outside = ratio.notna() & ((ratio < full["low"] * (1 - 1e-3)) | (ratio > full["high"] * (1 + 1e-3)))
    report = {
        "venue": vname, "exchange": venue["exchange"], "market_type": venue["market_type"], "symbol": symbol,
        "ccxt_symbol": ccxt_symbol, "timeframe": "1m", "ts_convention": "ts_open_utc = giờ MỞ nến (UTC)",
        "requested_open_start_utc": ms_to_dt(start_ms), "requested_open_end_utc_exclusive": ms_to_dt(end_ms),
        "limit_per_call": limit, "use_until": use_until, "ccxt_version": ccxt_version,
        "fetched_at_utc": datetime.now(UTC), "n_rows": len(full), "first_utc": full["ts_open_utc"].iloc[0],
        "last_utc": full["ts_open_utc"].iloc[-1], "expected_rows": expected, "n_missing": expected - len(full),
        "coverage_pct": round(100 * len(full) / expected, 4), "n_gap_events": len(gaps),
        "max_gap_min": int(gaps["n_missing"].max()) if len(gaps) else 0, "n_duplicates_removed": dups,
        "n_nan_price": int(px.isna().any(axis=1).sum()), "n_nonpositive_price": int((px <= 0).any(axis=1).sum()),
        "n_high_lt_low": int((full["high"] < full["low"]).sum()), "first_candle_delay_min": delay_min,
        "history_starts_later": bool(delay_min > HISTORY_LATE_FLAG_MIN), "month_parts_reused": reused,
        "contract_size": csize,
        "quote_source": "endpoint thô của sàn" if page_fn is not None else "không có (NaN)",
        "n_quote_missing": int(full["volume_quote"].isna().sum()), "n_quote_vwap_outside_hl": int(outside.sum()),
        "volume_note": ("hợp đồng perp: kiểm tra n_quote_vwap_outside_hl ~ 0 để chắc volume_base đang tính theo BTC "
                        "(không phải số hợp đồng)") if csize else None,
        "file": str(final), "sha256": sha256_file(final) if final.exists() else None,
    }
    (meta_dir / f"{base}_report.json").write_text(
        json.dumps({k: _py(v) for k, v in report.items()}, ensure_ascii=False, indent=2), encoding="utf-8")
    if page_fn is not None and (report["n_quote_missing"] or report["n_quote_vwap_outside_hl"] > 0.001 * len(full)):
        LOG.warning("[%s] volume_quote: %d dòng thiếu, %d dòng có quote/base ngoài [low, high] (>0,1%%) -> kiểm tra "
                    "đơn vị volume trước khi bàn giao.", vname, report["n_quote_missing"], report["n_quote_vwap_outside_hl"])
    if report["history_starts_later"]:
        LOG.warning("[%s] nến đầu tiên muộn %.0f phút so với đầu khoảng mẫu: lịch sử sàn KHÔNG phủ đủ. "
                    "Báo nhóm ngay (ảnh hưởng mốc M1 và R2.02).", vname, delay_min)
    return report








# ----------------------------------------------------------------------------- cấu hình / CLI
def load_yaml(path: Path) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}








def resolve_venues(requested: list[str], exchanges_cfg: dict) -> list[dict]:
    """Tên venue (vd 'okx_perp') -> dict kết nối trong exchanges.yaml. Venue không có trong file -> báo lỗi rõ ràng."""
    table = {venue_name(v): v for v in exchanges_cfg.get("venues", [])}
    unknown = [r for r in requested if r not in table]
    if unknown:
        raise ValueError(f"venue không có trong exchanges.yaml: {unknown}; có: {sorted(table)}")
    return [table[r] for r in requested]








def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--venues", nargs="+", help="vd: okx_spot okx_perp bybit_spot bybit_perp binance_perp (mặc định: "
                                                "venues trong config.yaml trừ binance_spot)")
    ap.add_argument("--start", help="giờ MỞ nến, gồm (mặc định: sample.start - 1 phút)")
    ap.add_argument("--end", help="giờ MỞ nến, KHÔNG gồm (mặc định: sample.end - 1 phút)")
    ap.add_argument("--limit", type=int, default=None, help="số nến tối đa mỗi lần gọi (theo C3.01)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--exchanges-config", type=Path, default=DEFAULT_EXCHANGES)
    ap.add_argument("--no-until", action="store_true", help="không gửi tham số until (nếu CCXT/sàn không hỗ trợ)")
    ap.add_argument("--fresh", action="store_true", help="xóa file tạm theo tháng, tải lại từ đầu")
    ap.add_argument("--no-quote", action="store_true", help="chỉ dùng fetch_ohlcv của CCXT (volume_quote = NaN)")
    ap.add_argument("--include-binance-spot", action="store_true",
                    help="tải cả binance_spot qua CCXT (chỉ để đối chiếu với file Binance Vision của TV2)")
    a = ap.parse_args(argv)




    a.out.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(a.out / "ccxt_ohlcv.log", encoding="utf-8")])
    cfg, ex_cfg = load_yaml(a.config), load_yaml(a.exchanges_config)
    symbol = cfg.get("symbol", "BTC/USDT")
    requested = a.venues or [str(v) for v in cfg.get("venues", [])]
    if not a.include_binance_spot:
        skipped = [v for v in requested if v in SKIP_BY_DEFAULT]
        if skipped:
            LOG.info("Bỏ qua %s: TV2 tải bằng Binance Vision (C2.01). Dùng --include-binance-spot nếu muốn đối chiếu.",
                     skipped)
        requested = [v for v in requested if v not in SKIP_BY_DEFAULT]
    if not requested:
        sys.exit("Không có venue nào để tải (kiểm tra config/config.yaml: venues).")
    sample = cfg.get("sample") or {}
    cfg_start = cfg_end = None
    if sample.get("start") and sample.get("end"):
        cfg_start, cfg_end = sample_to_open_range(sample["start"], sample["end"])
    start_ms = parse_utc_ms(a.start) if a.start else cfg_start
    end_ms = parse_utc_ms(a.end) if a.end else (cfg_end if cfg_end is not None else int(time.time() * 1000))
    if start_ms is None:
        sys.exit("Thiếu khoảng mẫu: truyền --start/--end hoặc điền sample (start, end) trong config/config.yaml.")
    LOG.info("Khoảng nến cần tải (giờ mở nến, UTC): %s .. %s (không gồm)", ms_to_dt(start_ms), ms_to_dt(end_ms))




    venues = resolve_venues(requested, ex_cfg)
    failed, reports = [], []
    for v in venues:
        limit = a.limit or DEFAULT_LIMIT.get(v["ccxt_id"], FALLBACK_LIMIT)
        try:
            reports.append(download_venue(v, symbol, start_ms, end_ms, a.out, limit,
                                          use_until=not a.no_until, fresh=a.fresh,
                                          with_quote=not a.no_quote))
        except Exception as e:  # noqa: BLE001 — một venue lỗi không chặn venue khác
            LOG.error("[%s] THẤT BẠI: %s: %s", venue_name(v), type(e).__name__, e)
            failed.append(venue_name(v))
    if reports:
        keep = ["venue", "n_rows", "first_utc", "last_utc", "expected_rows", "n_missing", "coverage_pct",
                "n_gap_events", "max_gap_min", "n_duplicates_removed", "history_starts_later"]
        print(pd.DataFrame(reports)[keep].to_string(index=False))
    if failed:
        print("Thất bại:", ", ".join(failed))
    return 1 if failed else 0








if __name__ == "__main__":
    sys.exit(main())

