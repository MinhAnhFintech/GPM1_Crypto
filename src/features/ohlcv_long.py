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

from src.synchronization.normalize import normalize_raw_ohlcv
from src.utils.config import configured_venue_ids
from src.utils.io_utils import write_parquet
from src.utils.schema import CONTRACT_VERSION, validate_frame
from src.utils.time_utils import utc_timestamp

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
    configured_venue_ids({"venues": [venue]})
    if symbol != "BTC/USDT":
        raise ValueError("D1 symbol must be BTC/USDT")
    return Path(raw_dir) / f"{venue}_{symbol.replace('/', '')}_1m.parquet"


def read_venue(raw_dir: Path, venue: str, symbol: str) -> pd.DataFrame:
    """Doc 1 venue va kiem tra no dung venue, symbol, data_kind; tra ve cot ts_available (UTC, ns)."""
    path = raw_path(raw_dir, venue, symbol)
    if not path.exists():
        raise FileNotFoundError(f"Thieu file tho cua {venue}: {path}")
    df = pd.read_parquet(path)
    if not df.columns.is_unique:
        raise ValueError(f"{path.name}: duplicate raw columns")
    exchange, market_type = venue.rsplit("_", 1)
    expected = {"exchange": exchange, "market_type": market_type, "symbol": symbol,
                "data_kind": DATA_KIND, "venue": venue}
    for column, value in expected.items():
        if column in df and not (df[column].notna() & df[column].eq(value)).all():
            raise ValueError(f"{path.name}: {column} khong khop {value}")
        if column in df.attrs and df.attrs[column] != value:
            raise ValueError(f"{path.name}: {column} metadata khong khop {value}")
    # The first Binance Vision handoff has open_time_utc/volume/quote_volume,
    # without identity columns; its explicit filename supplies the identity.
    # Never overwrite a supplied provenance or fill unobserved quote volume.
    try:
        out = normalize_raw_ohlcv(df, exchange=exchange, market_type=market_type, symbol=symbol)
    except ValueError as exc:
        detail = "trung khoa D1: " + str(exc) if "duplicate availability" in str(exc) else str(exc)
        raise ValueError(f"{path.name}: vi pham D1: {detail}") from exc
    validate_long(out)
    return out[D1_COLS]


def validate_long(df: pd.DataFrame) -> None:
    """Kiem tra bat bien cua D1; bao loi (khong tu sua) neu vi pham."""
    try:
        if df.duplicated(KEY).any():
            raise ValueError("trung khoa D1")
        validate_frame(df, "D1")
        if not df.data_kind.eq(DATA_KIND).all():
            raise ValueError("D1 must contain historical data")
        if df.volume_base.isna().any():
            raise ValueError("missing volume_base")
        if not df.ts_available.eq(df.ts_available.dt.floor("1min")).all():
            raise ValueError("timestamps must be on the one-minute grid")
    except ValueError as exc:
        raise ValueError("ohlcv_1m_long vi pham D1: " + str(exc)) from exc


def build_long(raw_dir: Path, venues: list, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Gop moi venue thanh bang dai D1, cat ts_available trong [start, end), sap theo (ts_available, venue)."""
    configured_venue_ids({"venues": venues})
    start, end = utc_timestamp(start), utc_timestamp(end)
    if end <= start:
        raise ValueError("sample.end must be after sample.start")
    frames = []
    for venue in venues:
        df = read_venue(raw_dir, venue, symbol)
        kept = df[(df["ts_available"] >= start) & (df["ts_available"] < end)]
        if kept.empty:
            raise ValueError(f"{venue}: no candles inside the selected sample")
        print(f"{venue}: doc {len(df):,} dong, giu {len(kept):,} trong khoang mau", flush=True)
        frames.append(kept)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["ts_available", "exchange", "market_type"]).reset_index(drop=True)
    validate_long(out)
    out.attrs = {"contract_version": CONTRACT_VERSION, "data_kind": DATA_KIND}
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

    write_parquet(out, args.out, "D1")
    print(f"So dong: {len(out):,} | venue: {out.groupby(['exchange', 'market_type']).ngroups} | "
          f"ts_available tu {out['ts_available'].min()} den {out['ts_available'].max()}")
    print("Da ghi", args.out)


if __name__ == "__main__":
    main()
