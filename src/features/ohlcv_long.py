"""C2.03 - Chuan hoa ohlcv_1m_long (bang D1): gop cac venue thanh MOT bang dai.

Dat tai: src/features/ohlcv_long.py
Cach chay (tu thu muc goc repo):
    python src/features/ohlcv_long.py

Dau vao : data/raw/ohlcv/{venue}_BTCUSDT_1m.parquet cho moi venue trong config/config.yaml (venues)
Dau ra  : data/interim/ohlcv_1m_long.parquet

Cot (dung D1, thu tu nay): ts_available, exchange, market_type, symbol, open, high, low, close,
    volume_base, volume_quote, data_kind.
ts_available = gio MO nen + 1 phut (UTC) = thoi diem nen DA DONG va moi duoc biet. Khong dung close_time.
Khoang mau: giu nen co ts_available trong [sample.start, sample.end) (start gom, end loai tru, UTC).
Don vi: gia USDT; volume_base BTC; volume_quote USDT. data_kind = historical.
KHONG xoa dong trung, KHONG dien nen thieu: neu du lieu vi pham bat bien cua D1 thi bao loi.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RAW_COLS = [
    "ts_open_utc", "exchange", "market_type", "symbol", "open", "high", "low", "close",
    "volume_base", "volume_quote", "data_kind",
]
D1_COLS = [
    "ts_available", "exchange", "market_type", "symbol", "open", "high", "low", "close",
    "volume_base", "volume_quote", "data_kind",
]
NUM_COLS = ["open", "high", "low", "close", "volume_base", "volume_quote"]
KEY = ["ts_available", "exchange", "market_type", "symbol"]
DATA_KIND = "historical"


def raw_path(raw_dir: Path, venue: str, symbol: str) -> Path:
    """Duong dan file tho cua venue, vi du binance_spot + BTC/USDT -> binance_spot_BTCUSDT_1m.parquet."""
    return raw_dir / f"{venue}_{symbol.replace('/', '')}_1m.parquet"


def read_venue(raw_dir: Path, venue: str, symbol: str) -> pd.DataFrame:
    """Doc 1 venue va kiem tra no dung venue, symbol, data_kind; tra ve cot ts_available (UTC, ns)."""
    path = raw_path(raw_dir, venue, symbol)
    if not path.exists():
        raise FileNotFoundError(f"Thieu file tho cua {venue}: {path}")
    df = pd.read_parquet(path)
    missing = [c for c in RAW_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: thieu cot {missing}; co {list(df.columns)}")

    exchange, market_type = venue.split("_", 1)
    wrong = (df["exchange"] != exchange) | (df["market_type"] != market_type) \
        | (df["symbol"] != symbol) | (df["data_kind"] != DATA_KIND)
    if wrong.any():
        raise ValueError(f"{path.name}: {int(wrong.sum())} dong co exchange/market_type/symbol/data_kind "
                         f"khong khop ({exchange}, {market_type}, {symbol}, {DATA_KIND})")

    out = df[RAW_COLS].copy()
    ts_open = pd.to_datetime(out.pop("ts_open_utc"), utc=True)
    out.insert(0, "ts_available", (ts_open + pd.Timedelta(minutes=1)).dt.as_unit("ns"))
    out[NUM_COLS] = out[NUM_COLS].astype("float64")
    return out[D1_COLS]


def validate_long(df: pd.DataFrame) -> None:
    """Kiem tra bat bien cua D1; bao loi (khong tu sua) neu vi pham."""
    problems = []
    n_dup = int(df.duplicated(KEY).sum())
    if n_dup:
        problems.append(f"{n_dup} dong trung khoa {KEY}")
    n_na = int(df[D1_COLS].isna().any(axis=1).sum())
    if n_na:
        problems.append(f"{n_na} dong co gia tri thieu")
    prices = df[["open", "high", "low", "close"]]
    if int((prices <= 0).any(axis=1).sum()):
        problems.append("co gia <= 0")
    top, bottom = prices[["open", "close", "low"]].max(axis=1), prices[["open", "close", "high"]].min(axis=1)
    if int(((df["high"] < top) | (df["low"] > bottom)).sum()):
        problems.append("high/low khong bao het open, close")
    if int(((df["volume_base"] < 0) | (df["volume_quote"] < 0)).sum()):
        problems.append("volume am")
    if problems:
        raise ValueError("ohlcv_1m_long vi pham D1: " + "; ".join(problems))


def build_long(raw_dir: Path, venues: list, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Gop moi venue thanh bang dai D1, cat ts_available trong [start, end), sap theo (ts_available, venue)."""
    frames = []
    for venue in venues:
        df = read_venue(raw_dir, venue, symbol)
        kept = df[(df["ts_available"] >= start) & (df["ts_available"] < end)]
        print(f"{venue}: doc {len(df):,} dong, giu {len(kept):,} trong khoang mau", flush=True)
        frames.append(kept)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["ts_available", "exchange", "market_type"]).reset_index(drop=True)
    validate_long(out)
    return out[D1_COLS]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/ohlcv")
    ap.add_argument("--out", type=Path, default=ROOT / "data/interim/ohlcv_1m_long.parquet")
    ap.add_argument("--config", type=Path, default=ROOT / "config/config.yaml")
    args = ap.parse_args(argv)

    from src.utils.config import load_config
    cfg = load_config(args.config)
    sample = cfg.get("sample") or {}
    if not sample.get("start") or not sample.get("end"):
        ap.error("Config thieu sample.start / sample.end")
    start, end = pd.Timestamp(sample["start"]), pd.Timestamp(sample["end"])
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        ap.error("sample.start/end phai co timezone va end > start")
    start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
    if not cfg.get("venues"):
        ap.error("Config thieu danh sach venues")

    out = build_long(args.raw_dir, cfg["venues"], cfg["symbol"], start, end)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".parquet.tmp")
    out.to_parquet(tmp, index=False)
    tmp.replace(args.out)
    print(f"So dong: {len(out):,} | venue: {out.groupby(['exchange', 'market_type']).ngroups} | "
          f"ts_available tu {out['ts_available'].min()} den {out['ts_available'].max()}")
    print("Da ghi", args.out)


if __name__ == "__main__":
    main()