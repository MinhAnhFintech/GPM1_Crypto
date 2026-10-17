"""TV4 read-only API smoke validation, isolated from the study dataset.

Fetch a small CLOSED minute-candle sample and one order book per selected venue.
Write evidence under tmp/real_review only; do not finalize the study sample,
update feasibility results, claim final test, or run the scheduled live collector.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from src.collectors.live.collect_live import fetch_top_of_book
from src.synchronization.align import align_previous_tick
from src.synchronization.normalize import normalize_raw_ohlcv
from src.synchronization.split import create_split_manifest
from src.utils.config import load_config
from src.utils.io_utils import write_json, write_parquet
from src.utils.schema import validate_frame


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchanges", type=Path, default=ROOT/"config/exchanges.example.yaml")
    parser.add_argument("--venues", default="binance_spot,okx_spot,binance_perp")
    parser.add_argument("--minutes", type=int, default=120)
    parser.add_argument("--out", type=Path, default=ROOT/"tmp/real_review")
    args = parser.parse_args(argv)
    import ccxt
    if not 10 <= args.minutes <= 200:
        parser.error("minutes must be between 10 and 200")
    end = pd.Timestamp.now(tz="UTC").floor("min").as_unit("ns")
    start = end-pd.Timedelta(minutes=args.minutes)
    requested = set(args.venues.split(","))
    report = {"purpose": "engineering_smoke_only", "sample_start": start.isoformat(), "sample_end_exclusive": end.isoformat(), "ccxt_version": ccxt.__version__, "venues": []}
    candles, quotes = [], []
    configured = load_config(args.exchanges)["venues"]
    unknown = requested-{f"{v['exchange']}_{v['market_type']}" for v in configured}
    if unknown:
        parser.error(f"Unknown configured venues: {sorted(unknown)}")
    for venue in configured:
        key = f"{venue['exchange']}_{venue['market_type']}"
        if key not in requested:
            continue
        ex = getattr(ccxt, venue["ccxt_id"])({"enableRateLimit": True, "timeout": 8000})
        result = {"venue": key}
        try:
            rows = ex.fetch_ohlcv(venue["symbol"], "1m", int(start.timestamp()*1000), args.minutes)
            raw = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
            d1 = normalize_raw_ohlcv(raw, exchange=venue["exchange"], market_type=venue["market_type"])
            d1 = d1[d1.ts_available.ge(start+pd.Timedelta(minutes=1)) & d1.ts_available.le(end)].copy()
            validate_frame(d1, "D1")
            candles.append(d1)
            result.update(ohlcv_status="passed", n_candles=len(d1))
            write_parquet(d1, args.out/key/"ohlcv_1m_long.parquet", "D1")
        except Exception as exc:
            result.update(ohlcv_status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            limit = 400 if venue["exchange"] == "okx" else 1000
            book = fetch_top_of_book(ex, venue["symbol"], limit)
            quote = pd.DataFrame([{**book, "ts_recv_utc": pd.Timestamp.now(tz="UTC").as_unit("ns"), "exchange": venue["exchange"], "market_type": venue["market_type"], "symbol": "BTC/USDT", "data_kind": "live"}])
            for column in ["ts_recv_utc", "ts_event_utc"]:
                quote[column] = pd.to_datetime(quote[column], utc=True).astype("datetime64[ns, UTC]")
            validate_frame(quote, "D2")
            quotes.append(quote)
            write_parquet(quote, args.out/key/"quote.parquet", "D2")
            result.update(orderbook_status="passed", depth_censored=bool(book["depth_censored"]))
        except Exception as exc:
            result.update(orderbook_status="failed", orderbook_error=f"{type(exc).__name__}: {str(exc)[:300]}")
        close = getattr(ex, "close", None)
        if close:
            close()
        report["venues"].append(result)
        print(result, flush=True)
    if candles:
        long = pd.concat(candles, ignore_index=True)
        write_parquet(long, args.out/"ohlcv_1m_long.parquet", "D1")
        panel = align_previous_tick(long)
        write_parquet(panel, args.out/"panel_1m_wide.parquet", "D3")
        # No production manifest is modified; never claim final test here.
        manifest = create_split_manifest(panel)
        write_json(manifest, args.out/"split_manifest.json")
        report.update(panel_rows=len(panel), panel_status="passed", test_consumed=manifest["test_consumed"])
    if quotes:
        write_parquet(pd.concat(quotes, ignore_index=True), args.out/"live_quotes_long.parquet", "D2")
    report["all_requested_venues_passed"] = len(report["venues"]) == len(requested) and all(r.get("ohlcv_status") == "passed" and r.get("orderbook_status") == "passed" for r in report["venues"])
    write_json(report, args.out/"review_report.json")
    return 0 if report["all_requested_venues_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
