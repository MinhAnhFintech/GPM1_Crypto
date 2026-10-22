"""C3.06: raw live parts -> D2 v1.1. Hand-made raw sessions; checks D2 structure and that no raw data is lost or changed."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.collectors.live import make_live_quotes_long as c306
from src.utils import schema as sch
from src.utils.schema import validate_frame

T0 = pd.Timestamp("2026-10-18T07:00:00Z")


def raw_rows(exchange="binance", market_type="spot", n=4, bid=100.0, ask=100.1, symbol="BTC/USDT", with_kind=True,
             with_market=True, with_depth=True, session_id="20261018_S1"):
    grid = [T0 + pd.Timedelta(seconds=5*i) for i in range(n)]
    df = pd.DataFrame({"ts_grid_utc": grid, "ts_recv_utc": [g + pd.Timedelta(milliseconds=150) for g in grid],
                       "ts_event_utc": [g + pd.Timedelta(milliseconds=100) for g in grid], "exchange": exchange,
                       "symbol": symbol, "bid": bid, "ask": ask, "bid_size": 1.5, "ask_size": 2.5, "session_id": session_id})
    if with_market:
        df["market_type"] = market_type
    if with_depth:
        df["depth_bid_10bps_usdt"], df["depth_ask_10bps_usdt"], df["depth_truncated"] = 1e6, 1.1e6, False
        df["latency_ms"] = 120.0
    if with_kind:
        df["data_kind"] = "live"
    return df


def write_part(raw_dir: Path, df: pd.DataFrame, session_id="20261018_S1", part=1):
    folder = raw_dir/"date=2026-10-18"/f"session={session_id}"
    folder.mkdir(parents=True, exist_ok=True)
    df.to_parquet(folder/f"part-{part:04d}.parquet", index=False)


def raw_hashes(raw_dir: Path) -> dict:
    return {str(p.relative_to(raw_dir)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw_dir.rglob("*")) if p.is_file()}


def run(raw_dir: Path, out_dir: Path, *extra):
    out = out_dir/"live_quotes_long.parquet"
    c306.main(["--raw", str(raw_dir), "--out", str(out), "--idle-s", "0", *extra])
    return (pd.read_parquet(out), pd.read_csv(out_dir/"live_quotes_long_manifest.csv"),
            json.loads((out_dir/"live_quotes_long_manifest.json").read_text(encoding="utf-8")))


@pytest.fixture
def dirs(tmp_path):
    raw, out = tmp_path/"raw", tmp_path/"out"
    raw.mkdir()
    return raw, out


def test_d2_v11_structure_and_values(dirs):
    raw, out = dirs
    write_part(raw, pd.concat([raw_rows("binance", "spot"), raw_rows("okx", "perp", symbol="BTC/USDT:USDT", bid=200.0, ask=200.4)]))
    d, _, _ = run(raw, out)
    validate_frame(d, "D2")
    assert list(d.columns)[:len(sch.D2_COLUMNS)] == list(sch.D2_COLUMNS)
    assert d.attrs["contract_version"] == "1.1"
    assert set(d.data_kind) == {"live"} and set(d.symbol) == {"BTC/USDT"}
    assert set(d.venue) == {"binance_spot", "okx_perp"} and set(d.market_type) == {"spot", "perp"}
    assert str(d.ts_recv_utc.dtype) == "datetime64[ns, UTC]"
    perp = d[d.venue == "okx_perp"].iloc[0]
    assert perp.mid == pytest.approx(200.2) and perp.spread_abs == pytest.approx(0.4)
    assert perp.spread_bps == pytest.approx(0.4/200.2*1e4)


def test_raw_unchanged_and_rows_conserved(dirs):
    raw, out = dirs
    bad = raw_rows("bybit", "spot", n=6)
    bad.loc[1, ["bid", "ask"]] = [101.0, 100.0]          # crossed book
    bad.loc[2, "bid"] = 0.0                              # nonpositive price
    bad = pd.concat([bad, bad.iloc[[3]]], ignore_index=True)   # exact duplicate grid key
    write_part(raw, pd.concat([raw_rows("binance", "spot", n=5), bad]), part=1)
    before = raw_hashes(raw)
    d, man, info = run(raw, out)
    run(raw, out)                                         # rerun is allowed and must not touch raw either
    assert raw_hashes(raw) == before
    removed = int(man[["non_live_removed", "dup_removed", "invalid_removed"]].sum().sum())
    assert int(man.rows_in.sum()) == len(d) + removed == 12
    assert removed == 3 and len(d) == 9 and info["rows_total"] == 9
    row = man[man.exchange == "bybit"].iloc[0]
    assert (row.dup_removed, row.invalid_removed, row.rows_out) == (1, 2, 4)
    assert not ((d.bid <= 0) | (d.ask < d.bid)).any()   # bad rows are removed, never repaired


def test_non_live_rows_and_test_sessions_never_enter_d2(dirs):
    raw, out = dirs
    mixed = raw_rows("binance", "spot", n=4)
    mixed.loc[0, "data_kind"] = "synthetic"
    write_part(raw, mixed, "20261018_S1")
    write_part(raw, raw_rows("okx", "spot", session_id="20261018_TEST0638"), "20261018_TEST0638")
    d, man, info = run(raw, out)
    assert len(d) == 3 and set(d.session_id) == {"20261018_S1"}
    assert int(man.non_live_removed.sum()) == 1 and info["non_live_removed_rows"] == 1
    assert "20261018_TEST0638" in info["sessions_skipped"]
    assert (d.data_kind == "live").all()


def test_legacy_session_without_columns_is_kept_and_flagged(dirs):
    raw, out = dirs
    write_part(raw, raw_rows("binance", n=3, with_kind=False, with_market=False, with_depth=False, session_id="20261017_S2"), "20261017_S2")
    d, man, info = run(raw, out)
    validate_frame(d, "D2")
    assert len(d) == 3 and set(d.market_type) == {"spot"} and set(d.data_kind) == {"live"}
    assert d.depth_truncated.all() and d.depth_bid_10bps_usdt.isna().all()      # unmeasured depth is NaN, never invented
    assert info["legacy_sessions_assumed_live"] == {"20261017_S2": 3}
    assert info["depth_truncated_filled_true_rows"] == 3


def test_missing_event_time_is_kept_as_nat(dirs):
    raw, out = dirs
    rows = raw_rows("kraken", "spot")
    rows["ts_event_utc"] = pd.NaT
    write_part(raw, rows)
    d, man, _ = run(raw, out)
    validate_frame(d, "D2")
    assert len(d) == 4 and d.ts_event_utc.isna().all() and man.pct_event_ts_missing.iloc[0] == 100.0


def test_rerun_is_deterministic(dirs):
    raw, out = dirs
    write_part(raw, pd.concat([raw_rows("binance", "spot"), raw_rows("okx", "perp", symbol="BTC/USDT:USDT")]))
    first, _, _ = run(raw, out)
    second, _, _ = run(raw, out)
    pd.testing.assert_frame_equal(first, second)


def test_running_session_is_skipped_unless_included(dirs):
    raw, _ = dirs
    write_part(raw, raw_rows())
    use, skipped = c306.discover(raw, idle_s=10**6, include_running=False, exclude=set())
    assert use == {} and "20261018_S1" in skipped
    use, skipped = c306.discover(raw, idle_s=10**6, include_running=True, exclude=set())
    assert list(use) == ["20261018_S1"] and skipped == {}
    use, _ = c306.discover(raw, idle_s=0, include_running=False, exclude={"20261018_S1"})
    assert use == {}
