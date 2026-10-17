"""C2.01 - Tai klines 1 phut BTCUSDT (SPOT) tu data.binance.vision.

Dat tai: src/collectors/historical/binance_vision.py
Cach chay (tu thu muc goc repo):
    python src/collectors/historical/binance_vision.py --start 2026-09 --end 2026-09

Dau ra: data/raw/ohlcv/binance_spot_BTCUSDT_1m.parquet  (schema tho cua Binance)
Chi lay SPOT. Perpetual do TV3 lay qua CCXT (C3.04).
"""
import argparse
import hashlib
import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "n_trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def months(start: str, end: str):
    return [p.strftime("%Y-%m") for p in pd.period_range(start, end, freq="M")]


def fetch(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def download_month(sym: str, ym: str, cache: Path) -> pd.DataFrame:
    url = BASE.format(sym=sym, ym=ym)
    zip_path = cache / f"{sym}-1m-{ym}.zip"

    if not zip_path.exists():
        data = fetch(url)
        expected = fetch(url + ".CHECKSUM").decode().split()[0].lower()
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:  # lech checksum = loi, khong giu file
            raise ValueError(f"Checksum LECH cho {ym}: {actual} != {expected}")
        zip_path.write_bytes(data)

    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", required=True, help="YYYY-MM")
    ap.add_argument("--end", required=True, help="YYYY-MM (chi thang da dong)")
    ap.add_argument("--out", default="data/raw/ohlcv")
    args = ap.parse_args()

    out_dir = Path(args.out)
    cache = out_dir / "_zip_cache"
    cache.mkdir(parents=True, exist_ok=True)

    frames = []
    for ym in months(args.start, args.end):
        print("Tai", ym, flush=True)
        frames.append(download_month(args.symbol, ym, cache))

    df = pd.concat(frames, ignore_index=True)
    df["open_time_utc"] = to_utc(df["open_time"])
    df["close_time_utc"] = to_utc(df["close_time"])
    df = df.drop(columns=["open_time", "close_time", "ignore"])
    df = df.sort_values("open_time_utc").reset_index(drop=True)

    dup = int(df["open_time_utc"].duplicated().sum())
    print(f"So dong: {len(df):,} | nen trung: {dup} | tu {df['open_time_utc'].min()} den {df['open_time_utc'].max()}")
    # KHONG dien nen thieu o day. C2.02 se dem va bao cao.

    out_file = out_dir / f"binance_spot_{args.symbol}_1m.parquet"
    df.to_parquet(out_file, index=False)
    print("Da ghi", out_file)


if __name__ == "__main__":
    main()