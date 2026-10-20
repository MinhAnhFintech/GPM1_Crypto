"""TV4 review regressions for TV2/TV3; no network required."""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import zipfile
import numpy as np
import pandas as pd
import pytest
from src.collectors.historical import binance_vision as vision
from src.collectors.live.collect_live import Session, SessionWriter, fetch_top_of_book, coverage, COLUMNS


def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        output.writestr("BTCUSDT-1m-2026-09.csv", "1788220800000000,100,102,99,101,2,1788220859999999,201,5,1,100,0\n")
    return stream.getvalue()


def test_binance_cache_is_checked_on_every_read(tmp_path, monkeypatch):
    data = archive()
    checksum = (hashlib.sha256(data).hexdigest()+"  BTCUSDT-1m-2026-09.zip").encode()
    calls = []
    def fake_fetch(url):
        calls.append(url)
        return checksum if url.endswith("CHECKSUM") else data
    monkeypatch.setattr(vision, "fetch", fake_fetch)
    assert len(vision.download_month("BTCUSDT", "2026-09", tmp_path)) == 1
    assert len(calls) == 2
    vision.download_month("BTCUSDT", "2026-09", tmp_path)
    assert len(calls) == 2
    (tmp_path/"BTCUSDT-1m-2026-09.zip").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="Checksum"):
        vision.download_month("BTCUSDT", "2026-09", tmp_path)


def test_binance_ms_and_microseconds_are_same_instant():
    timestamps = pd.Series([1788220800000, 1788220800000000])
    converted = vision.to_utc(timestamps)
    assert converted.iloc[0] == converted.iloc[1]
    assert str(converted.dtype) == "datetime64[ns, UTC]"


def test_reversed_month_range_rejected():
    with pytest.raises(ValueError, match="ordered"):
        vision.months("2026-10", "2026-09")


class Book:
    def __init__(self, contract_size=1, shallow=False):
        self.contract_size, self.shallow = contract_size, shallow
    def market(self, symbol):
        return {"contract": ":" in symbol, "contractSize": self.contract_size, "linear": True}
    def fetch_order_book(self, symbol, limit):
        return {"bids": [[99.99, 2], [99.8 if not self.shallow else 99.98, 4]], "asks": [[100.01, 3], [100.2 if not self.shallow else 100.02, 5]], "timestamp": 1788220800000}


def test_linear_contract_sizes_are_converted_to_base_and_depth():
    book = fetch_top_of_book(Book(.01), "BTC/USDT:USDT", 100)
    assert book["bid_size"] == pytest.approx(.02)
    assert book["ask_size"] == pytest.approx(.03)
    assert book["depth_bid_10bps_usdt"] == pytest.approx(99.99*.02)
    assert not book["depth_censored"]
    assert fetch_top_of_book(Book(shallow=True), "BTC/USDT", 100)["depth_censored"]


def test_coverage_counts_session_edges_and_duplicate_rows(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = Session("test", start, start+timedelta(seconds=20))
    rows = []
    for seconds in [5, 5, 10]:
        rows.append({"ts_grid_utc": start+timedelta(seconds=seconds), "ts_recv_utc": start+timedelta(seconds=seconds, milliseconds=30), "ts_event_utc": None, "exchange": "binance", "market_type": "spot", "symbol": "BTC/USDT", "latency_ms": 30.0, "session_id": "test", "session_start_utc": session.start, "session_end_utc": session.end})
    pd.DataFrame(rows).to_parquet(tmp_path/"part-0001.parquet")
    result = coverage(tmp_path, 5).iloc[0]
    assert result.expected_rows == 4
    assert result.unique_rows == 2
    assert result.duplicate_rows == 1
    assert result.coverage_pct == 50


def test_writer_resumes_after_max_part_number(tmp_path):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = Session("test", start, start+timedelta(minutes=1))
    writer = SessionWriter(tmp_path, session, timezone.utc, "parquet")
    (writer.dir/"part-0005.parquet").write_bytes(b"existing")
    resumed = SessionWriter(tmp_path, session, timezone.utc, "parquet")
    assert resumed.n == 5


def test_coverage_reports_completely_missing_session_without_future_ticks(tmp_path):
    cfg = {"timezone": "UTC", "start_date": "2026-01-01", "end_date": "2026-01-01", "interval_s": 5, "book_limit": 100, "symbol": "BTC/USDT", "venues": [{"exchange": "binance", "market_type": "spot"}], "sessions_weekday": [{"name": "S1", "start": "00:00", "end": "01:00"}], "sessions_weekend": []}
    result = coverage(tmp_path, 5, cfg, as_of="2026-01-01T00:00:20Z").iloc[0]
    assert result.expected_rows == 4
    assert result.unique_rows == 0
    assert result.coverage_pct == 0


def test_failed_flush_retains_pending_rows(tmp_path, monkeypatch):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    writer = SessionWriter(tmp_path, Session("test", start, start+timedelta(minutes=1)), timezone.utc, "parquet")
    writer.add({**fetch_top_of_book(Book(), "BTC/USDT", 100),
                "ts_grid_utc": start, "ts_recv_utc": start, "session_start_utc": start,
                "session_end_utc": start+timedelta(minutes=1), "exchange": "binance",
                "market_type": "spot", "symbol": "BTC/USDT", "data_kind": "live"})
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    with pytest.raises(OSError, match="disk full"):
        writer.flush()
    assert len(writer.rows) == 1
