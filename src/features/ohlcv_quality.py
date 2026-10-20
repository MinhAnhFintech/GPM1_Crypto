"""C2.02 - Kiem tra chat luong OHLCV 1 phut cho MOI venue (spot va perp).

Dat tai: src/features/ohlcv_quality.py
Cach chay (tu thu muc goc repo):
    python src/features/ohlcv_quality.py

Dau vao : data/raw/ohlcv/{venue}_BTCUSDT_1m.parquet   (venue = {exchange}_{market_type})
          Nhan cot kieu TV3 (ts_open_utc, volume_base) va kieu Binance tho (open_time_utc, volume).
Dau ra  : outputs/tables/T0_data_quality.csv          (1 dong / venue)

Chi DEM va BAO CAO. Khong xoa dong trung, khong dien nen thieu, khong sua gia.
Thoi gian: moi moc la gio MO nen, UTC. Gia: USDT. Volume: theo don vi cua san (xem C2.03).
Nguong nhay gia: doc quality.jump_bps trong config; neu thieu thi dung DEFAULT_JUMP_BPS va in canh bao.
coverage_status: observed_range / empty / no_valid_minute. Khi khong co moc phut
hop le, bien mau va cac thong ke coverage de trong (khong phai 0 nen thieu).
"""
import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FILE_RE = re.compile(r"^(?P<venue>(?P<exchange>[a-z0-9]+)_(?P<market_type>spot|perp))_BTCUSDT_1m\.parquet$")
TIME_COLS = ("open_time_utc", "ts_open_utc", "open_time", "timestamp", "ts", "datetime")
PRICE_COLS = ["open", "high", "low", "close"]
RENAME_TEAM_SCHEMA = {"ts_open_utc": "open_time_utc", "volume_base": "volume"}  # cot cua TV3 -> ten noi bo
DEFAULT_JUMP_BPS = 300.0  # chi la nguong de soi (du phong); dat quality.jump_bps trong config de doi


def to_utc(s: pd.Series) -> pd.Series:
    """Doi cot thoi gian ve UTC.

    So nguyen: >= 1e15 la micro giay, con lai la mili giay (xu ly tung dong).
    Datetime khong co timezone duoc coi la UTC.
    """
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.tz_localize("UTC") if s.dt.tz is None else s.dt.tz_convert("UTC")
    v = pd.to_numeric(s).astype("int64")
    ms = np.where(v >= 10**15, v // 1000, v)
    return pd.to_datetime(ms, unit="ms", utc=True).to_series(index=s.index)


def read_raw(path: Path) -> pd.DataFrame:
    """Doc file tho cua 1 venue, tra ve cot open_time_utc (UTC) + open/high/low/close/volume.

    Chap nhan ca cot kieu TV3 (ts_open_utc, volume_base) lan kieu Binance tho (open_time_utc, volume).
    """
    df = pd.read_parquet(path)
    time_col = next((c for c in TIME_COLS if c in df.columns), None)
    if time_col is None:
        raise ValueError(f"{path.name}: khong thay cot thoi gian trong {list(df.columns)}")
    volume_col = next((c for c in ("volume_base", "volume") if c in df.columns), None)
    missing = [c for c in PRICE_COLS if c not in df.columns]
    if volume_col is None:
        missing.append("volume_base or volume")
    if missing:
        raise ValueError(f"{path.name}: thieu cot {missing}")
    out = df[PRICE_COLS + [volume_col]].rename(columns={volume_col: "volume"}).apply(pd.to_numeric, errors="coerce")
    out.insert(0, "open_time_utc", to_utc(df[time_col]))
    return out


def check_venue(df: pd.DataFrame, venue: str, jump_bps: float = DEFAULT_JUMP_BPS) -> dict:
    """Dem cac loi du lieu cua 1 venue. Dau vao la ket qua cua read_raw().

    jump_bps: nguong |loi suat dong-dong giua 2 phut lien tiep| (bps) de danh dau 'nhay gia bat thuong'.
    """
    ts = df["open_time_utc"]
    minute = pd.Timedelta(minutes=1)

    on_grid = ts == ts.dt.floor("1min")
    u = ts[on_grid].drop_duplicates().sort_values()
    if len(u):
        coverage_status = "observed_range"
        first, last = u.iloc[0].isoformat(), u.iloc[-1].isoformat()
        expected = int((u.iloc[-1] - u.iloc[0]) / minute) + 1
        gaps = (u.diff().dropna() / minute).astype(int) - 1
        missing = expected - len(u)
        missing_pct = round(100 * missing / expected, 4)
        n_gaps = int((gaps > 0).sum())
        longest_gap = int(gaps.max()) if len(gaps) else 0
    else:
        coverage_status = "empty" if df.empty else "no_valid_minute"
        first = last = None
        expected = missing = missing_pct = n_gaps = longest_gap = None

    prices = df[PRICE_COLS]
    hi, lo = df["high"], df["low"]

    # nhay gia: chi tinh giua 2 phut LIEN TIEP de khoang thieu khong bi tinh la nhay
    d = df[on_grid].drop_duplicates("open_time_utc").sort_values("open_time_utc")
    d = d[d["close"] > 0]
    step = d["open_time_utc"].diff()
    ret_bps = (np.log(d["close"]).diff() * 1e4).where(step == minute)

    return {
        "venue": venue,
        "n_rows": len(df),
        "coverage_status": coverage_status,
        "first_open_utc": first,
        "last_open_utc": last,
        "expected_candles": expected,
        "missing_candles": missing,
        "missing_pct": missing_pct,
        "n_gaps": n_gaps,
        "longest_gap_min": longest_gap,
        "duplicate_candles": int(ts.duplicated().sum()),
        "misaligned_ts": int((~on_grid).sum()),
        "nan_price_rows": int(prices.isna().any(axis=1).sum()),
        "nonpositive_price_rows": int((prices <= 0).any(axis=1).sum()),
        "high_lt_low_rows": int((hi < lo).sum()),
        "ohlc_inconsistent_rows": int(((hi < df[["open", "close"]].max(axis=1)) | (lo > df[["open", "close"]].min(axis=1))).sum()),
        "negative_volume_rows": int((df["volume"] < 0).sum()),
        "zero_volume_rows": int((df["volume"] == 0).sum()),
        "jump_threshold_bps": jump_bps,
        "jump_rows": int((ret_bps.abs() > jump_bps).sum()),
        "max_abs_jump_bps": round(float(ret_bps.abs().max()), 2) if ret_bps.notna().any() else 0.0,
    }


def default_jump_bps(config_path: Path) -> float:
    """Doc quality.jump_bps tu config. Thieu config hoac thieu khoa thi dung DEFAULT_JUMP_BPS va canh bao."""
    try:
        from src.utils.config import load_config
        value = (load_config(config_path).get("quality") or {}).get("jump_bps")
    except (ImportError, FileNotFoundError):
        value = None
    if value is None:
        print(f"CANH BAO: config chua co quality.jump_bps, dung mac dinh {DEFAULT_JUMP_BPS} bps")
        return DEFAULT_JUMP_BPS
    return float(value)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw-dir", type=Path, default=ROOT / "data/raw/ohlcv")
    ap.add_argument("--out", type=Path, default=ROOT / "outputs/tables/T0_data_quality.csv")
    ap.add_argument("--config", type=Path, default=ROOT / "config/config.yaml")
    ap.add_argument("--jump-bps", type=float, help="nguong nhay gia (bps); mac dinh doc tu config")
    args = ap.parse_args(argv)
    jump_bps = args.jump_bps if args.jump_bps is not None else default_jump_bps(args.config)

    rows = []
    for path in sorted(args.raw_dir.glob("*.parquet")):
        m = FILE_RE.match(path.name)
        if not m:
            print("Bo qua (ten file khong dung {venue}_BTCUSDT_1m.parquet):", path.name)
            continue
        print("Kiem tra", m["venue"], flush=True)
        row = check_venue(read_raw(path), m["venue"], jump_bps)
        rows.append({"venue": row.pop("venue"), "exchange": m["exchange"], "market_type": m["market_type"], **row})

    if not rows:
        raise SystemExit(f"Khong co file *_BTCUSDT_1m.parquet nao trong {args.raw_dir}")

    out = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".csv.tmp")
    out.to_csv(tmp, index=False)
    tmp.replace(args.out)
    print(out.T.to_string(header=False))
    print("Da ghi", args.out)


if __name__ == "__main__":
    main()
