"""TV3 handoff: canonical D2, metadata sizing and deep-book fallback; offline."""
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys

import pandas as pd
import pytest

from src.collectors.live.collect_live import (
    BookSampler, Session, SessionWriter, coverage, fetch_book, get_venues,
)
from src.utils.io_utils import read_parquet
from src.utils.schema import D2_COLUMNS, validate_frame

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


class Exchange:
    def __init__(self, failures=(), contract_size=.01, inverse=False):
        self.failures = list(failures)
        self.calls = []
        self.contract_size, self.inverse = contract_size, inverse

    def market(self, symbol):
        return {"contract": ":" in symbol, "linear": not self.inverse,
                "inverse": self.inverse, "contractSize": self.contract_size}

    def fetch_order_book(self, symbol, limit, params=None):
        self.calls.append((limit, params))
        if limit > 5 and self.failures:
            error = self.failures.pop(0)
            if error:
                raise error
        return {"bids": [[99.99, 2], [99.8, 4]], "asks": [[100.01, 3], [100.2, 5]],
                "timestamp": 1767225600000}


class Writer:
    def __init__(self):
        self.errors = []

    def log_error(self, *args):
        self.errors.append(args)


def venue():
    return {"exchange": "okx", "market_type": "perp", "symbol": "BTC/USDT:USDT",
            "book_limit": 1000, "params": {"method": "publicGetMarketBooksFull"}, "size_multiplier": .01}


def test_deep_failure_preserves_quotes_and_disables_depth_after_three_failures():
    exchange, writer = Exchange([ValueError("unsupported limit")] * 3), Writer()
    sampler = BookSampler(exchange, venue(), writer)
    for _ in range(3):
        quote = sampler.fetch()
        assert quote["mid"] == 100 and quote["spread_bps"] > 0
        assert quote["bid_size"] == pytest.approx(.02)
        assert quote["depth_bid_10bps_usdt"] is None and quote["depth_censored"] is True
    assert not sampler.deep_enabled and sampler.deep_failures == 3
    sampler.fetch()
    assert [limit for limit, _ in exchange.calls] == [1000, 5, 1000, 5, 1000, 5, 5]
    assert all(params is None for limit, params in exchange.calls if limit == 5)
    assert all(params == venue()["params"] for limit, params in exchange.calls if limit == 1000)
    assert writer.errors[-1][-1] == "deep_disabled_for_session"


class RateLimitExceeded(Exception):
    pass


@pytest.mark.parametrize("error", [TimeoutError("timeout"), ConnectionError("network"), RateLimitExceeded("429")])
def test_transient_error_does_not_trigger_second_request_or_disable_depth(error):
    exchange = Exchange([error])
    sampler = BookSampler(exchange, venue(), Writer())
    with pytest.raises(type(error)):
        sampler.fetch()
    assert len(exchange.calls) == 1 and sampler.deep_enabled
    assert sampler.deep_failures == 0


def test_successful_deep_request_resets_consecutive_failure_count():
    exchange = Exchange([ValueError("limit"), None, ValueError("limit")])
    sampler = BookSampler(exchange, venue(), Writer())
    sampler.fetch()
    quote = sampler.fetch()
    assert sampler.deep_failures == 0 and quote["depth_bid_10bps_usdt"] == pytest.approx(99.99*.02)
    sampler.fetch()
    assert sampler.deep_failures == 1 and sampler.deep_enabled


def test_metadata_controls_contract_sizes_even_without_config_override():
    quote = fetch_book(Exchange(contract_size=.1), "BTC/USDT:USDT", 100)
    assert quote["bid_size"] == pytest.approx(.2)
    with pytest.raises(ValueError, match="disagrees"):
        fetch_book(Exchange(contract_size=.1), "BTC/USDT:USDT", 100, size_mult=.01)


@pytest.mark.parametrize("contract_size", [0, -1, float("inf"), float("nan")])
def test_invalid_contract_sizes_are_rejected(contract_size):
    with pytest.raises(ValueError, match="contractSize"):
        fetch_book(Exchange(contract_size=contract_size), "BTC/USDT:USDT", 100)


def test_inverse_contracts_are_rejected():
    with pytest.raises(ValueError, match="inverse"):
        fetch_book(Exchange(inverse=True), "BTC/USDT:USDT", 100)


def test_connection_parameters_survive_config_loading():
    cfg = {"symbol": "BTC/USDT", "book_limit": 5, "venues": [venue()]}
    configured = get_venues(cfg)[0]
    assert configured["size_multiplier"] == .01 and configured["params"] == venue()["params"]


def test_shallow_quote_parquet_remains_valid_d2_and_coverage_keeps_session_edges(tmp_path):
    session = Session("test", START, START+timedelta(seconds=20))
    writer = SessionWriter(tmp_path, session, timezone.utc, "parquet")
    quote = fetch_book(Exchange(), "BTC/USDT:USDT", 5, with_depth=False)
    writer.add({**quote, "ts_grid_utc": START+timedelta(seconds=5),
                "ts_recv_utc": START+timedelta(seconds=5, milliseconds=10),
                "exchange": "okx", "market_type": "perp", "symbol": "BTC/USDT", "latency_ms": 10.,
                "session_id": "test", "session_start_utc": START, "session_end_utc": session.end, "data_kind": "live"})
    writer.log_error("okx:perp", "BadRequest", "deep book failed", "used_shallow")
    writer.flush()
    frame = read_parquet(writer.dir/"part-0001.parquet", "D2")
    validate_frame(frame, "D2")
    assert frame.depth_bid_10bps_usdt.isna().all()
    assert frame.depth_bid_10bps_usdt.dtype == "float64"
    assert frame.depth_censored.all() and frame.symbol.eq("BTC/USDT").all()
    assert frame.depth_truncated.all() and frame.venue.eq("okx_perp").all()
    assert list(frame.columns[:len(D2_COLUMNS)]) == D2_COLUMNS
    assert frame.attrs["contract_version"] == "1.1"
    report = coverage(tmp_path, 5, as_of=START+timedelta(seconds=20)).iloc[0]
    assert report.expected_rows == 4 and report.unique_rows == 1 and report.coverage_pct == 25
    assert report.n_errors == 1 and report.depth_censored_pct == 100
    assert pd.isna(report.median_depth_bid_usdt)


def test_writer_accepts_new_flag_and_restores_rows_on_alias_conflict(tmp_path):
    session = Session("test", START, START+timedelta(seconds=20))
    writer = SessionWriter(tmp_path, session, timezone.utc, "parquet")
    quote = fetch_book(Exchange(), "BTC/USDT:USDT", 5, with_depth=False)
    quote["depth_truncated"] = quote.pop("depth_censored")
    row = {**quote, "ts_grid_utc": START, "ts_recv_utc": START,
           "exchange": "okx", "market_type": "perp", "symbol": "BTC/USDT",
           "latency_ms": 10., "session_id": "test", "session_start_utc": START,
           "session_end_utc": session.end, "data_kind": "live"}
    writer.add(row)
    writer.flush()
    frame = read_parquet(writer.dir/"part-0001.parquet", "D2")
    assert frame.depth_truncated.all() and frame.depth_censored.all()
    writer.add({**row, "depth_censored": False})
    with pytest.raises(ValueError, match="disagree"):
        writer.flush()
    assert len(writer.rows) == 1
    assert not (writer.dir/"part-0002.parquet").exists()


def test_schedule_cli_supports_ascii_windows_console_without_starting_collector(tmp_path):
    config = tmp_path/"sessions.yaml"
    config.write_text("timezone: UTC\nstart_date: 2026-01-01\nend_date: 2026-01-01\n"
                      "sessions_weekday: [{name: S1, start: '14:00', end: '17:00'}]\n"
                      "sessions_weekend: []\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-m", "src.collectors.live.collect_live", "schedule",
                             "--config", str(config), "--out", str(tmp_path/"out")],
                            env=dict(os.environ, PYTHONIOENCODING="ascii"), capture_output=True,
                            text=True, encoding="ascii", timeout=20)
    assert result.returncode == 0, result.stderr
    assert "20260101_S1" in result.stdout and "14:00 -> 17:00 UTC" in result.stdout
    assert not list(tmp_path.rglob("part-*.parquet"))
