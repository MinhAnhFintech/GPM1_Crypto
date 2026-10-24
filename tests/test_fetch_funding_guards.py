"""Guards của C3.09 (fetch_funding): nguồn dữ liệu (cột + metadata) và báo cáo thiếu đầu/cuối cửa sổ. Không gọi mạng."""
import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "collectors" / "live"))
import fetch_funding as ff  # noqa: E402

UTC = timezone.utc
LIVE0 = datetime(2026, 10, 16, 17, 0, tzinfo=UTC)
OKX = {"venue": "okx_perp", "exchange": "okx", "market_type": "perp", "ccxt_id": "okx", "symbol": "BTC/USDT:USDT"}
STATS = {"windows": 1, "failed": 0}


def ms(dt):
    return int(dt.timestamp() * 1000)


def funding_rec(first, n, step_h=8):
    return {ms(first + timedelta(hours=step_h * i)): 0.0001 for i in range(n)}


def oi_rec(first, n):
    return {ms(first + timedelta(hours=i)): (5000.0, 5000.0 * 100_000.0) for i in range(n)}


def args(tmp_path):
    return argparse.Namespace(out=tmp_path / "funding", format="parquet", hourly=False, problems=[], no_oi=True,
                              processed=tmp_path / "funding_oi.parquet")


# ===================================================================== 1) nguồn: cột historical + metadata synthetic
def write_with_attrs(df, path, attrs):
    path.parent.mkdir(parents=True, exist_ok=True)
    df = df.copy()
    df.attrs = attrs
    df.to_parquet(path, index=False)


@pytest.mark.parametrize("attrs", [{"data_kind": "synthetic"},
                                   {"data_kind": "historical", "source": "synthetic_generator"},
                                   {"data_kind": "historical", "synthetic": True}])
def test_column_historical_but_metadata_synthetic_is_rejected(tmp_path, attrs):
    a = args(tmp_path)
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    funding = ff.to_frame(funding_rec(base, 6), OKX, LIVE0)
    assert (funding["data_kind"] == "historical").all()                   # cột nói historical...
    write_with_attrs(funding, a.out / "okx_perp_funding.parquet", attrs)  # ...metadata nói synthetic
    kept, why = ff.check_source(funding, "okx_perp_funding", a.out / "okx_perp_funding.parquet")
    assert kept is None and "metadata" in why                             # check_source tự nó phải bắt được

    out = ff.build_processed(a, [OKX], LIVE0)
    assert out is None                                                    # không dựng bảng, không dòng nào vào D9
    assert any("bỏ file funding" in m and "metadata" in m for m in a.problems)
    assert not a.processed.exists()


def test_main_exits_1_and_keeps_synthetic_out_of_d9(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "config.yaml").write_text("sample: {start: '2025-10-18T00:01:00Z', end: '2026-10-01T00:00:00Z'}\n"
                                     "venues: [okx_perp]\n", encoding="utf-8")
    (cfg / "exchanges.yaml").write_text("venues:\n  - {exchange: okx, market_type: perp, ccxt_id: okx, "
                                        "symbol: 'BTC/USDT:USDT'}\n", encoding="utf-8")
    out = tmp_path / "funding"
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    write_with_attrs(ff.to_frame(funding_rec(base, 6), OKX, LIVE0), out / "okx_perp_funding.parquet",
                     {"data_kind": "synthetic"})
    code = ff.main(["--build-only", "--config-dir", str(cfg), "--out", str(out),
                    "--processed", str(tmp_path / "funding_oi.parquet")])
    assert code == 1


def test_genuine_file_written_by_script_is_accepted_and_labelled(tmp_path):
    a = args(tmp_path)
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    path = a.out / "okx_perp_funding.parquet"
    ff.write_frame(ff.to_frame(funding_rec(base, 6), OKX, LIVE0), path, "parquet")
    assert ff.read_file_meta(path)["data_kind"] == "historical"
    kept, why = ff.check_source(pd.read_parquet(path), "okx_perp_funding", path)
    assert why is None and len(kept) == 6
    out = ff.build_processed(a, [OKX], LIVE0)
    assert len(out) == 6 and not a.problems
    assert ff.read_file_meta(a.processed)["data_kind"] == "historical"


def test_old_file_without_metadata_still_accepted(tmp_path):
    path = tmp_path / "okx_perp_funding.parquet"
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    ff.to_frame(funding_rec(base, 3), OKX, LIVE0).to_parquet(path, index=False)   # file đời cũ: không metadata
    kept, why = ff.check_source(pd.read_parquet(path), "okx_perp_funding", path)
    assert why is None and len(kept) == 3


def test_column_only_checks_still_work(tmp_path):
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    df = ff.to_frame(funding_rec(base, 3), OKX, LIVE0)
    synthetic = df.assign(data_kind="synthetic")
    assert ff.check_source(synthetic, "x")[0] is None
    legacy = df.assign(data_kind="live")                                   # nhãn của bản script cũ, source=ccxt
    kept, why = ff.check_source(legacy, "x")
    assert why is None and (kept["data_kind"] == "historical").all()
    assert ff.check_source(legacy.assign(source="other"), "x")[0] is None  # nguồn khác ccxt: từ chối


def test_missing_venue_input_blocks_processed_table(tmp_path):
    a = args(tmp_path)
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    ff.write_frame(ff.to_frame(funding_rec(base, 3), OKX, LIVE0), a.out / "okx_perp_funding.parquet", "parquet")
    other = dict(OKX, venue="bybit_perp", exchange="bybit")
    assert ff.build_processed(a, [OKX, other], LIVE0) is None              # thiếu bybit -> không ghi bảng thiếu
    assert not a.processed.exists() and any("bybit_perp" in m for m in a.problems)


def test_rejected_old_file_is_backed_up_not_merged_into_new_data(tmp_path):
    a = args(tmp_path)
    base = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    path = a.out / "okx_perp_funding.parquet"
    write_with_attrs(ff.to_frame(funding_rec(base - timedelta(days=5), 6), OKX, LIVE0), path, {"data_kind": "synthetic"})
    new = ff.to_frame(funding_rec(base, 3), OKX, LIVE0)
    merged = ff.merge_with_existing(new, path, "ts_funding_utc", ["ts_funding_utc", "ts_available"], LIVE0, "okx_perp")
    assert len(merged) == 3                                                       # dòng synthetic không lọt vào
    assert list(a.out.glob("okx_perp_funding.bad-source*.parquet"))          # nhưng được giữ lại để điều tra


# ===================================================================== 2) báo cáo thiếu đầu / cuối
SINCE = datetime(2025, 10, 18, 0, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 22, 11, 57, tzinfo=UTC)


def test_funding_late_start_and_early_stop_report_head_and_tail():
    # lưới 8h: cửa sổ có 1108 kỳ; sàn chỉ có 282 kỳ từ 2026-07-20 16:00 (như OKX), rồi dừng sớm 5 kỳ
    first = datetime(2026, 7, 20, 16, 0, tzinfo=UTC)
    df = ff.to_frame(funding_rec(first, 277), OKX, LIVE0)
    rep = ff.make_report(df, OKX, ms(SINCE), ms(UNTIL), STATS)
    assert rep["n_rows"] == 277
    assert rep["n_missing_head"] == 826
    assert rep["n_missing_interior"] == 0
    assert rep["n_missing_tail"] == 5
    assert rep["n_missing_est"] == 831 and rep["n_expected_est"] == 1108      # không còn báo "thiếu 0"


def test_funding_full_coverage_reports_zero_missing():
    first = datetime(2025, 10, 18, 8, 0, tzinfo=UTC)
    df = ff.to_frame(funding_rec(first, 1108), OKX, LIVE0)
    rep = ff.make_report(df, OKX, ms(SINCE), ms(UNTIL), STATS)
    assert (rep["n_missing_head"], rep["n_missing_interior"], rep["n_missing_tail"], rep["n_missing_est"]) == (0, 0, 0, 0)
    assert rep["n_expected_est"] == 1108


def test_funding_interior_hole_is_still_counted():
    first = datetime(2025, 10, 18, 8, 0, tzinfo=UTC)
    rec = funding_rec(first, 1108)
    for k in (100, 101, 102):
        rec.pop(ms(first + timedelta(hours=8 * k)))
    rep = ff.make_report(ff.to_frame(rec, OKX, LIVE0), OKX, ms(SINCE), ms(UNTIL), STATS)
    assert (rep["n_missing_head"], rep["n_missing_interior"], rep["n_missing_tail"]) == (0, 3, 0)


def test_oi_late_start_and_early_stop_report_head_and_tail():
    tf = "1h"
    s_ms, until_ms = ms(datetime(2026, 9, 23, 11, 57, tzinfo=UTC)), ms(datetime(2026, 10, 22, 11, 57, tzinfo=UTC))
    first = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)               # bắt đầu muộn 48 nến
    df = ff.to_frame_oi(oi_rec(first, 600), OKX, tf, LIVE0, None)  # 600 nến -> dừng sớm so với 696
    rep = ff.make_oi_report(df, OKX, tf, s_ms, until_ms, STATS, None)
    assert rep["n_expected_est"] == 696
    assert (rep["n_missing_head"], rep["n_missing_interior"], rep["n_missing_tail"]) == (48, 0, 48)
    assert rep["n_missing_est"] == 96


def test_oi_full_window_with_running_candle_reports_zero():
    s_ms, until_ms = ms(datetime(2026, 9, 23, 11, 57, tzinfo=UTC)), ms(datetime(2026, 10, 22, 11, 57, tzinfo=UTC))
    df = ff.to_frame_oi(oi_rec(datetime(2026, 9, 23, 12, 0, tzinfo=UTC), 696), OKX, "1h", LIVE0, None)
    rep = ff.make_oi_report(df, OKX, "1h", s_ms, until_ms, STATS, None)
    assert rep["n_missing_est"] == 0 and rep["n_expected_est"] == 696

def test_funding_window_end_is_exclusive_full_last_period_reports_zero_tail():
    # until đúng 2026-10-23 00:00 (bộ tải loại trừ until): đã có 00:00, 08:00, 16:00 của 22/10 -> không thiếu kỳ 00:00 ngày sau
    until = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    first = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    df = ff.to_frame(funding_rec(first, 6), OKX, LIVE0)                  # 21/10 00:00 ... 22/10 16:00
    assert df["ts_funding_utc"].max() == pd.Timestamp("2026-10-22 16:00", tz="UTC")
    rep = ff.make_report(df, OKX, ms(first), ms(until), STATS)
    assert (rep["n_missing_head"], rep["n_missing_interior"], rep["n_missing_tail"], rep["n_missing_est"]) == (0, 0, 0, 0)
    assert rep["n_expected_est"] == 6


def test_funding_window_end_missing_last_period_still_reported():
    until = datetime(2026, 10, 23, 0, 0, tzinfo=UTC)
    first = datetime(2026, 10, 21, 0, 0, tzinfo=UTC)
    df = ff.to_frame(funding_rec(first, 5), OKX, LIVE0)                  # thiếu 22/10 16:00
    rep = ff.make_report(df, OKX, ms(first), ms(until), STATS)
    assert rep["n_missing_tail"] == 1 and rep["n_missing_est"] == 1


def test_count_missing_boundary_on_grid_is_exclusive():
    h = 3_600_000
    step = 8 * h
    ts = [0, step, 2 * step]
    assert ff.count_missing(ts, step, 0, 3 * step, until_inclusive=False)["tail"] == 0
    assert ff.count_missing(ts, step, 0, 3 * step + 1, until_inclusive=False)["tail"] == 1   # mốc 3*step đã nằm trong cửa sổ