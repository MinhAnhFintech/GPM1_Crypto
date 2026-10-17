"""TV4 integration adapter from venue raw candles to canonical D1."""
from pathlib import Path
import re
import pandas as pd
from src.utils.schema import validate_frame
from src.utils.time_utils import utc_series


def normalize_raw_ohlcv(raw, *, exchange, market_type, symbol="BTC/USDT") -> pd.DataFrame:
    """Convert Binance/CCXT raw candles; availability = candle OPEN + one minute.

    Binance's close_time_utc (often open+59.999s) must never be used as availability.
    Missing volume_quote remains NaN; close*volume is not an observed quote volume.
    """
    df = raw.copy()
    if "open_time_utc" in df:
        opening = utc_series(df.open_time_utc)
    elif "timestamp" in df and pd.api.types.is_numeric_dtype(df.timestamp):
        opening = pd.to_datetime(df.timestamp, unit="ms", utc=True).astype("datetime64[ns, UTC]")
    elif "ts_open_utc" in df:
        opening = utc_series(df.ts_open_utc)
    else:
        raise ValueError("Raw candles need open_time_utc, ts_open_utc or CCXT timestamp (ms)")
    result = pd.DataFrame({"ts_available": opening+pd.Timedelta(minutes=1), "exchange": exchange, "market_type": market_type, "symbol": symbol})
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(df[column], errors="raise").astype("float64")
    for target, candidates in {"volume_base": ["volume_base", "volume"], "volume_quote": ["volume_quote", "quote_volume"]}.items():
        source = next((c for c in candidates if c in df), None)
        if source is None and target == "volume_base":
            raise ValueError("Raw candles need volume_base or volume")
        result[target] = pd.to_numeric(df[source], errors="raise").astype("float64") if source else float("nan")
    result["data_kind"] = "historical"
    return validate_frame(result, "D1")


def load_raw_directory(raw_dir, *, venues=None) -> pd.DataFrame:
    """Read explicit {exchange}_{spot|perp}_BTCUSDT_1m.parquet handoffs."""
    paths = sorted(Path(raw_dir).glob("*_BTCUSDT_1m.parquet"))
    frames, found = [], set()
    for path in paths:
        match = re.fullmatch(r"([a-z][a-z0-9]*)_(spot|perp)_BTCUSDT_1m\.parquet", path.name)
        if not match:
            continue
        exchange, market_type = match.groups()
        venue = f"{exchange}_{market_type}"
        if venues is not None and venue not in venues:
            continue
        frames.append(normalize_raw_ohlcv(pd.read_parquet(path), exchange=exchange, market_type=market_type))
        found.add(venue)
    if venues is not None and set(venues)-found:
        raise ValueError(f"Missing raw venue files: {sorted(set(venues)-found)}")
    if not frames:
        raise ValueError(f"No raw candle files in {raw_dir}")
    return validate_frame(pd.concat(frames, ignore_index=True), "D1")
