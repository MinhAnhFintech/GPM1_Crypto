"""C3.09 guards (offline, CSV format): không ghi đè bảng đủ 3 sàn bằng bảng thiếu, kiểm nguồn từng file, báo lỗi khi không tải được."""
from datetime import datetime, timezone

import pandas as pd

from src.collectors.live import fetch_funding as F

VENUES = ["binance_perp", "okx_perp", "bybit_perp"]
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _config(tmp_path):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(
        "symbol: BTC/USDT\nvenues: [binance_perp, okx_perp, bybit_perp]\n"
        "sample: {start: '2026-10-01T00:00:00Z', end: '2026-10-02T00:00:00Z'}\n", encoding="utf-8")
    (cfg / "exchanges.yaml").write_text(
        "venues:\n" + "".join(f"  - {{exchange: {v.split('_')[0]}, market_type: perp, ccxt_id: {v.split('_')[0]}, "
                              f"symbol: 'BTC/USDT:USDT'}}\n" for v in VENUES), encoding="utf-8")
    return cfg


def _funding(venue, kind="historical", source="ccxt"):
    ts = pd.date_range(T0, periods=3, freq="8h", tz="UTC")
    return pd.DataFrame({"ts_funding_utc": ts, "ts_available": ts, "exchange": venue.split("_")[0], "market_type": "perp",
                         "symbol": "BTC/USDT", "funding_rate": 1e-4, "funding_rate_bps": 1.0, "funding_interval_h": 8.0,
                         "data_kind": kind, "in_live_window": False, "source": source})


def _oi(venue, kind="historical", source="ccxt"):
    ts = pd.date_range(T0, periods=3, freq="1h", tz="UTC")
    return pd.DataFrame({"ts_oi_utc": ts, "ts_available": ts + pd.Timedelta(hours=1), "exchange": venue.split("_")[0],
                         "market_type": "perp", "symbol": "BTC/USDT", "timeframe": "1h", "open_interest_base": 10.0,
                         "open_interest_usdt": 1_000_000.0, "data_kind": kind, "in_live_window": False, "source": source})


def _write_raw(out, venues, **kw):
    out.mkdir(parents=True, exist_ok=True)
    for v in venues:
        _funding(v, **kw).to_csv(out / f"{v}_funding.csv", index=False)
        _oi(v, **kw).to_csv(out / f"{v}_oi.csv", index=False)


def _run(tmp_path, *extra, make_ex=None):
    cfg, out, proc = _config(tmp_path) if not (tmp_path / "cfg").exists() else tmp_path / "cfg", tmp_path / "raw", tmp_path / "funding_oi.csv"
    args = ["--format", "csv", "--config-dir", str(cfg), "--out", str(out), "--processed", str(proc), *extra]
    return F.main(args, make_ex=make_ex), proc


def test_missing_venue_does_not_overwrite_three_venue_table(tmp_path):
    _config(tmp_path)
    _write_raw(tmp_path / "raw", ["binance_perp", "bybit_perp"])          # thiếu okx_perp
    (tmp_path / "funding_oi.csv").write_text("sentinel,3-venue-table\n", encoding="utf-8")
    code, proc = _run(tmp_path, "--build-only")
    assert code == 1
    assert proc.read_text(encoding="utf-8") == "sentinel,3-venue-table\n"


def test_full_inputs_build_all_three_venues(tmp_path):
    _config(tmp_path)
    _write_raw(tmp_path / "raw", VENUES)
    code, proc = _run(tmp_path, "--build-only")
    out = pd.read_csv(proc)
    assert code == 0 and set(out["venue"]) == set(VENUES) and set(out["data_kind"]) == {"historical"}


def test_synthetic_file_is_rejected_not_relabelled(tmp_path):
    _config(tmp_path)
    _write_raw(tmp_path / "raw", VENUES)
    _funding("okx_perp", kind="synthetic").to_csv(tmp_path / "raw" / "okx_perp_funding.csv", index=False)
    code, proc = _run(tmp_path, "--build-only")
    assert code == 1 and not proc.exists()


def test_legacy_live_label_from_ccxt_is_normalised(tmp_path):
    _config(tmp_path)
    _write_raw(tmp_path / "raw", VENUES, kind="live")
    code, proc = _run(tmp_path, "--build-only")
    assert code == 0 and set(pd.read_csv(proc)["data_kind"]) == {"historical"}


def test_live_label_without_ccxt_source_is_rejected(tmp_path):
    _config(tmp_path)
    _write_raw(tmp_path / "raw", VENUES, kind="live", source="simulator")
    code, proc = _run(tmp_path, "--build-only")
    assert code == 1 and not proc.exists()


class FakeEx:
    def __init__(self, funding=()):
        self.funding = list(funding)

    def fetch_funding_rate_history(self, symbol, since, limit, params):
        return [r for r in self.funding if since <= r["timestamp"] < params["until"]]

    def market(self, symbol):
        return {"contractSize": 1}


def test_old_synthetic_rows_are_not_merged_into_new_download(tmp_path):
    _config(tmp_path)
    out = tmp_path / "raw"
    out.mkdir()
    _funding("binance_perp", kind="synthetic").to_csv(out / "binance_perp_funding.csv", index=False)
    rows = [{"timestamp": int(T0.timestamp() * 1000) + i * 8 * 3_600_000, "fundingRate": 2e-4} for i in range(3)]
    code, _ = _run(tmp_path, "--venues", "binance_perp", "--no-oi", "--since", "2026-10-01", "--until", "2026-10-02",
                   make_ex=lambda cid: FakeEx(rows))
    saved = pd.read_csv(out / "binance_perp_funding.csv")
    assert code == 1                                            # file cũ không hợp lệ được báo lỗi
    assert set(saved["data_kind"]) == {"historical"} and (saved["funding_rate"] == 2e-4).all()
    assert list(out.glob("binance_perp_funding.bad-source*.csv"))   # bản cũ được sao lưu, không bị xóa


def test_no_data_downloaded_returns_error_and_keeps_old_file(tmp_path):
    _config(tmp_path)
    out = tmp_path / "raw"
    out.mkdir()
    _funding("binance_perp").to_csv(out / "binance_perp_funding.csv", index=False)
    before = (out / "binance_perp_funding.csv").read_bytes()
    code, _ = _run(tmp_path, "--venues", "binance_perp", "--no-oi", "--since", "2026-10-01", "--until", "2026-10-02",
                   make_ex=lambda cid: FakeEx([]))
    assert code == 1
    assert (out / "binance_perp_funding.csv").read_bytes() == before


class FullEx(FakeEx):
    """Sàn giả đủ funding + OI + ticker; OKX trả OI theo hợp đồng (1000 hợp đồng = 10 BTC)."""
    def __init__(self, cid):
        super().__init__([{"timestamp": int(T0.timestamp() * 1000) + i * 8 * 3_600_000, "fundingRate": 1e-4} for i in range(3)])
        self.base = 1000.0 if cid == "okx" else 10.0

    def fetch_open_interest_history(self, symbol, tf, since, limit, params):
        return [{"timestamp": int(T0.timestamp() * 1000) + i * 3_600_000, "openInterestAmount": self.base,
                 "openInterestValue": 1_000_000.0} for i in range(6)]

    def market(self, symbol):
        return {"contractSize": 0.01 if self.base == 1000.0 else 1}

    def fetch_ticker(self, symbol):
        return {"last": 100_000.0}


def test_normal_run_three_venues_exit_zero_and_okx_oi_in_btc(tmp_path):
    _config(tmp_path)
    code, proc = _run(tmp_path, "--since", "2026-10-01", "--until", "2026-10-02", make_ex=FullEx)
    out = pd.read_csv(proc)
    oi = out.dropna(subset=["open_interest_base"])
    assert code == 0 and set(out["venue"]) == set(VENUES)
    assert (oi["open_interest_base"] == 10.0).all()            # OKX đã quy đổi hợp đồng -> BTC