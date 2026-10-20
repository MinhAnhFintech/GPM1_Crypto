"""C2.01 - Tai klines 1 phut BTCUSDT (SPOT) tu data.binance.vision.

Dat tai: src/collectors/historical/binance_vision.py
Cach chay (tu thu muc goc repo):
    python src/collectors/historical/binance_vision.py
        -> doc khoang mau tu config/config.yaml (sample.start / sample.end), CAT dung khoang do.
    python src/collectors/historical/binance_vision.py --start 2026-09 --end 2026-09
        -> tai nguyen cac thang da chon, KHONG cat theo config.

Dau ra: data/raw/ohlcv/binance_spot_BTCUSDT_1m.parquet
Cot (giong file cua TV3): ts_open_utc, venue, exchange, market_type, symbol,
    open, high, low, close, volume_base, volume_quote, data_kind.
Don vi: gia USDT; volume_base BTC; volume_quote USDT. ts_open_utc la gio MO nen (UTC).
Khoang mau: ts_available = ts_open_utc + 1 phut nam trong [sample.start, sample.end)
    (start gom, end loai tru). Chi lay SPOT. Perpetual do TV3 lay qua CCXT (C3.04).
"""
import argparse
import hashlib
import io
import re
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
EXCHANGE = "binance"
MARKET_TYPE = "spot"


def months(start: str, end: str):
    """Inclusive monthly interval; reject malformed or reversed ranges."""
    if not all(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value) for value in (start, end)) or start > end:
        raise ValueError("Expected ordered YYYY-MM --start/--end")
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]


def fetch(url: str) -> bytes:
    """Retry transient public-data failures; never retry missing monthly files."""
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            return r.content
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise
        time.sleep(2**attempt)
    raise RuntimeError("unreachable")


def download_month(sym: str, ym: str, cache: Path) -> pd.DataFrame:
    """Verify SHA-256 for NEW and CACHED ZIPs before parsing any candle."""
    cache.mkdir(parents=True, exist_ok=True)
    url = BASE.format(sym=sym, ym=ym)
    zip_path = cache / f"{sym}-1m-{ym}.zip"
    checksum_path = zip_path.with_suffix(".zip.CHECKSUM")
    data = zip_path.read_bytes() if zip_path.exists() else fetch(url)
    checksum = checksum_path.read_bytes() if checksum_path.exists() else fetch(url+".CHECKSUM")
    expected = checksum.decode().split()[0].lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError(f"Invalid SHA-256 checksum for {ym}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"Checksum LECH cho {ym}: {actual} != {expected}")
    if not zip_path.exists():
        zip_path.write_bytes(data)
    if not checksum_path.exists():
        checksum_path.write_bytes(checksum)

    with zipfile.ZipFile(zip_path) as z:
        candidates = [name for name in z.namelist() if name.lower().endswith(".csv") and not name.endswith("/")]
        if len(candidates) != 1:
            raise ValueError("Expected exactly one candle CSV per monthly archive")
        name = candidates[0]
        with z.open(name) as f:
            raw = f.read().decode()

    # file thang co the co hoac khong co dong tieu de
    has_header = raw.lstrip().startswith("open_time")
    df = pd.read_csv(
        io.StringIO(raw),
        header=0 if has_header else None,
        names=None if has_header else COLS,
    )
    df.columns = COLS
    if df.empty:
        raise ValueError(f"Empty monthly candles: {ym}")
    return df


def to_utc(ts: pd.Series) -> pd.Series:
    """Binance: 13 chu so = mili giay, 16 chu so = micro giay. Doi het ve UTC."""
    ts = ts.astype("int64")
    unit = pd.Series("ms", index=ts.index)
    unit[ts >= 10**15] = "us"
    out = pd.Series(pd.NaT, index=ts.index, dtype="datetime64[ns, UTC]")
    for u in ("ms", "us"):
        m = unit == u
        out[m] = pd.to_datetime(ts[m], unit=u, utc=True)
    return out


def to_team_schema(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Doi bang tho cua Binance sang cot giong file cua TV3 (D1 truoc buoc ts_available).

    ts_open_utc: gio MO nen (UTC). Gia: USDT. volume_base: BTC. volume_quote: USDT.
    Gia dinh: symbol co dang <BASE>USDT, vi du BTCUSDT -> BTC/USDT.
    """
    if not symbol.endswith("USDT"):
        raise ValueError(f"Chi ho tro symbol ket thuc bang USDT, nhan duoc {symbol}")
    return pd.DataFrame({
        "ts_open_utc": df["open_time_utc"],
        "venue": f"{EXCHANGE}_{MARKET_TYPE}",
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": f"{symbol[:-4]}/{symbol[-4:]}",
        "open": df["open"].astype("float64"),
        "high": df["high"].astype("float64"),
        "low": df["low"].astype("float64"),
        "close": df["close"].astype("float64"),
        "volume_base": df["volume"].astype("float64"),
        "volume_quote": df["quote_volume"].astype("float64"),
        "data_kind": "historical",
    })


def clip_to_sample(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Giu cac nen co ts_available = ts_open_utc + 1 phut nam trong [start, end).

    start gom, end loai tru (UTC), dung quy uoc cua config.sample. Khong dien nen thieu.
    """
    ts_available = df["ts_open_utc"] + pd.Timedelta(minutes=1)
    return df[(ts_available >= start) & (ts_available < end)].reset_index(drop=True)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", help="YYYY-MM; overrides config")
    ap.add_argument("--end", help="YYYY-MM (chi thang da dong); overrides config")
    ap.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    ap.add_argument("--out", type=Path, default=ROOT/"data/raw/ohlcv")
    args = ap.parse_args(argv)

    window = None  # (start, end) theo config; chi cat khi khong go --start/--end
    if not args.start or not args.end:
        from src.utils.config import load_config
        cfg = load_config(args.config)
        sample = cfg.get("sample", {})
        if not sample.get("start") or not sample.get("end"):
            ap.error("Set --start/--end or sample.start/end (UTC, end exclusive) in config")
        start, end = pd.Timestamp(sample["start"]), pd.Timestamp(sample["end"])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            ap.error("Config sample dates must be aware, with end > start")
        start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
        if not args.start and not args.end:
            window = (start, end)
        args.start = args.start or start.strftime("%Y-%m")
        args.end = args.end or (end-pd.Timedelta(nanoseconds=1)).strftime("%Y-%m")

    out_dir = Path(args.out)
    cache = out_dir / "_zip_cache"
    cache.mkdir(parents=True, exist_ok=True)

    frames = []
    requested_months = months(args.start, args.end)
    current_month = pd.Timestamp.now(tz="UTC").strftime("%Y-%m")
    if any(ym >= current_month for ym in requested_months):
        ap.error("Monthly archive must be closed; choose months before the current UTC month")
    for ym in requested_months:
        print("Tai", ym, flush=True)
        frames.append(download_month(args.symbol, ym, cache))

    df = pd.concat(frames, ignore_index=True)
    df["open_time_utc"] = to_utc(df["open_time"])
    df = df.sort_values("open_time_utc").reset_index(drop=True)
    df = to_team_schema(df, args.symbol)

    n_downloaded = len(df)
    if window is not None:
        df = clip_to_sample(df, *window)
        print(f"Cat theo config: giu {len(df):,} / {n_downloaded:,} dong "
              f"(ts_available trong [{window[0]}, {window[1]}))")

    dup = int(df["ts_open_utc"].duplicated().sum())
    print(f"So dong: {len(df):,} | nen trung: {dup} | tu {df['ts_open_utc'].min()} den {df['ts_open_utc'].max()}")
    # KHONG dien nen thieu o day. C2.02 se dem va bao cao.

    out_file = out_dir / f"binance_spot_{args.symbol}_1m.parquet"
    tmp = out_file.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out_file)
    print("Da ghi", out_file)


if __name__ == "__main__":
    main()