"""C3.04 offline acceptance: availability, pagination, cache and handoff status."""
import pandas as pd
import pytest

from src.collectors.historical import ccxt_ohlcv as H
from src.collectors.live.collect_live import fetch_book

START = H.to_ms("2026-01-01T00:00:00Z")
MIN = H.MINUTE_MS
VENUE = H.Venue("okx", "perp", "okx", "BTC/USDT:USDT")


def candle(offset, volume=2.5):
    return [START+offset*MIN, 100., 102., 99., 101., volume]


class Exchange:
    def __init__(self, rows, failures=()):
        self.rows, self.failures, self.calls = rows, list(failures), []
        self.markets = {VENUE.symbol: {}}

    def market(self, symbol):
        return {"contract": True, "linear": True, "inverse": False,
                "contractSize": .01, "base": "BTC", "quote": "USDT"}

    def fetch_ohlcv(self, symbol, timeframe, since, limit):
        self.calls.append(since)
        if self.failures:
            raise self.failures.pop(0)
        return [r for r in self.rows if r[0] >= since][:min(limit, 2)]


def collect(tmp_path, ex, **kwargs):
    return H.collect_venue(VENUE, START, START+5*MIN, tmp_path, ex=ex,
                           now_ms=START+6*MIN, retry_exceptions=(TimeoutError,),
                           max_empty_pages=0, sleep=lambda _: None, **kwargs)


def test_sample_window_uses_availability_with_exclusive_end():
    assert H.candle_open_window("2026-01-01T00:01Z", "2026-01-01T00:06Z") == (START, START+5*MIN)
    with pytest.raises(ValueError, match="timezone"):
        H.to_ms("2026-01-01")


def test_pagination_advances_by_last_received_candle_not_requested_limit():
    ex = Exchange([candle(i) for i in range(5)])
    rows = H.fetch_range(ex, VENUE.symbol, START, START+5*MIN, 1000)
    assert [r[0] for r in rows] == [START+i*MIN for i in range(5)]
    assert ex.calls == [START, START+2*MIN, START+4*MIN]


def test_retry_then_success_keeps_all_candles():
    ex = Exchange([candle(0), candle(1)], failures=[TimeoutError("offline")])
    sleeps = []
    out = H.fetch_range(ex, VENUE.symbol, START, START+2*MIN, 10,
                        retry_exceptions=(TimeoutError,), sleep=sleeps.append)
    assert len(out) == 2 and sleeps == [1.0] and ex.calls == [START, START]


def test_retry_exhaustion_does_not_publish_data(tmp_path):
    ex = Exchange([], failures=[TimeoutError("offline")])
    with pytest.raises(TimeoutError):
        collect(tmp_path, ex, max_retries=0)
    assert not list(tmp_path.rglob("*.parquet"))


@pytest.mark.parametrize("limit", [0, -1, 1.5, True])
def test_invalid_limits_fail_before_fetch(limit):
    ex = Exchange([])
    with pytest.raises(ValueError, match="positive limit"):
        H.fetch_range(ex, VENUE.symbol, START, START+MIN, limit)
    assert not ex.calls


@pytest.mark.parametrize("bounds", [(START, START), (START+MIN, START), (START+1, START+MIN)])
def test_invalid_time_windows_fail_before_fetch(bounds):
    ex = Exchange([])
    with pytest.raises(ValueError, match="candle bounds"):
        H.fetch_range(ex, VENUE.symbol, *bounds, 100)
    assert not ex.calls


@pytest.mark.parametrize("conflict", [False, True])
def test_duplicate_payloads_are_counted_or_rejected(conflict):
    class Duplicates:
        def fetch_ohlcv(self, *args, **kwargs):
            return [candle(0), candle(0, volume=3 if conflict else 2.5)]
    stats = {}
    if conflict:
        with pytest.raises(ValueError, match="Conflicting duplicate"):
            H.fetch_range(Duplicates(), VENUE.symbol, START, START+MIN, 100, stats=stats)
    else:
        assert len(H.fetch_range(Duplicates(), VENUE.symbol, START, START+MIN, 100, stats=stats)) == 1
    assert stats["duplicate_rows"] == 1


def test_partial_part_is_retried_and_only_complete_verified_part_is_reused(tmp_path):
    partial = collect(tmp_path, Exchange([candle(0), candle(4)]))
    assert partial["status"] == "incomplete" and partial["missing"] == 3
    assert not (tmp_path/VENUE.file_name).exists()
    assert (tmp_path/"_incomplete"/VENUE.file_name).exists()
    ex = Exchange([candle(i) for i in range(5)])
    complete = collect(tmp_path, ex)
    assert complete["status"] == "complete" and complete["actual"] == 5 and ex.calls
    no_network = Exchange([])
    again = collect(tmp_path, no_network)
    assert not no_network.calls and again["sha256"] == complete["sha256"]


def test_tampered_cache_is_not_trusted_when_refetch_returns_empty(tmp_path):
    complete = collect(tmp_path, Exchange([candle(i) for i in range(5)]))
    final = tmp_path/VENUE.file_name
    before = final.read_bytes()
    part = next((tmp_path/"_parts").rglob("*.parquet"))
    modified = pd.read_parquet(part)
    modified.loc[0, "volume_base"] += 100
    modified.to_parquet(part, index=False)
    with pytest.raises(RuntimeError, match="khong tai duoc"):
        collect(tmp_path, Exchange([]))
    assert final.read_bytes() == before and H.sha256_file(final) == complete["sha256"]


def test_unrelated_cached_window_cannot_contaminate_requested_data(tmp_path):
    stale = tmp_path/"_parts"/VENUE.venue_id/"old_window.parquet"
    H.write_parquet_atomic(H.to_frame([candle(0, 999)], VENUE), stale)
    report = collect(tmp_path, Exchange([candle(i) for i in range(5)]))
    assert report["actual"] == 5 and report["_frame"].volume_base.eq(2.5).all()


def test_unclosed_candles_stay_out_and_cannot_mark_future_window_complete(tmp_path):
    report = H.collect_venue(VENUE, START, START+5*MIN, tmp_path,
        ex=Exchange([candle(i) for i in range(5)]), now_ms=START+3*MIN+30_000,
        max_empty_pages=0, retry_exceptions=(), sleep=lambda _: None)
    assert report["status"] == "incomplete" and report["actual"] == 3
    assert report["_frame"].ts_open_utc.max() == pd.Timestamp(START+2*MIN, unit="ms", tz="UTC")
    assert len(H.drop_unclosed([candle(2), candle(3)], START+3*MIN)) == 1


@pytest.mark.parametrize("field,value", [("close", 0), ("volume_base", -1), ("exchange", "bybit"), ("data_kind", "synthetic")])
def test_invalid_raw_data_cannot_enter_cache(field, value):
    raw = H.to_frame([candle(0)], VENUE)
    raw.loc[0, field] = value
    with pytest.raises(ValueError):
        H.validate_raw(raw, VENUE)


def test_unified_okx_perp_ohlcv_is_btc_not_orderbook_contracts():
    import ccxt
    market = {"type": "swap", "spot": False, "swap": True, "inverse": False,
              "linear": True, "contract": True, "contractSize": .01}
    row = ccxt.okx().parse_ohlcv([str(START), "100", "102", "99", "101", "250", "2.5", "250", "1"], market)
    raw = H.to_frame([row], VENUE)
    assert raw.volume_base.iloc[0] == 2.5
    assert pd.isna(raw.volume_quote.iloc[0])


def test_extra_depth_bands_preserve_canonical_d2_and_btc_conversion():
    class Book(Exchange):
        def fetch_order_book(self, *args, **kwargs):
            return {"bids": [[99.99, 2], [99.96, 3], [99.92, 4], [99.8, 5]],
                    "asks": [[100.01, 2], [100.04, 3], [100.08, 4], [100.2, 5]]}
    quote = fetch_book(Book([]), VENUE.symbol, 1000)
    assert quote["bid_size"] == pytest.approx(.02)
    assert quote["depth_bid_2bps_usdt"] < quote["depth_bid_5bps_usdt"] < quote["depth_bid_10bps_usdt"]
    assert quote["depth_cover_bps"] == pytest.approx(20)
    assert quote["mid"] == 100 and not quote["depth_censored"]


def test_cli_reports_incomplete_collection_as_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "load_config", lambda _: {"venues": [VENUE.venue_id],
        "sample": {"start": "2026-01-01T00:01Z", "end": "2026-01-01T00:06Z"}})
    monkeypatch.setattr(H, "load_venues", lambda _: [VENUE])
    monkeypatch.setattr(H, "collect_venue", lambda *a, **kw: {"status": "incomplete", "_frame": H.to_frame([candle(0)], VENUE)})
    assert H.main(["--out-dir", str(tmp_path)]) == 1


def test_tv2_quality_accepts_tv3_raw_schema_without_shifting_candle_time(tmp_path):
    from src.features.ohlcv_quality import check_venue, read_raw
    raw = H.to_frame([candle(i) for i in range(5)], VENUE)
    path = tmp_path/VENUE.file_name
    H.write_parquet_atomic(raw, path)
    before = path.read_bytes()
    report = check_venue(read_raw(path), VENUE.venue_id)
    assert report["n_rows"] == 5 and report["missing_candles"] == 0
    assert report["first_open_utc"] == "2026-01-01T00:00:00+00:00"
    assert path.read_bytes() == before
