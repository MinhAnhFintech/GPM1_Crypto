#!/usr/bin/env python3
"""C1.07 (TV1): bảng T1 đặc điểm từng venue -> outputs/tables/T1_exchange_characteristics.csv

Chạy từ gốc repo (PowerShell):
    py -3.12 -m scripts.exchange_characteristics `
        --ohlcv data/interim/ohlcv_1m_long.parquet `
        --live data/processed/live_quotes_long.parquet `
        --funding data/processed/funding_oi.parquet `
        --manifest data/processed/split_manifest.json `
        --fees config/fees.yaml `
        --out outputs/tables/T1_exchange_characteristics.csv `
        --sample dev_val

Mỗi dòng là một venue ({exchange}_{market_type}). Mọi số trung bình kèm số quan sát và khoảng thời gian
mà nó được tính, vì các nguồn phủ những giai đoạn khác nhau (nến cả năm, live vài ngày, funding và OI ngắn hơn).

--sample dev_val (mặc định): volume tính trên mẫu phát triển và kiểm định, không đụng mẫu kiểm tra cuối.
--sample full: tính trên toàn bộ D1 (chỉ dùng khi nhóm xác nhận được phép mô tả mẫu kiểm tra cuối).
Số quan sát ohlcv_n_obs luôn là toàn bộ số nến của venue.
Phí lấy qua load_fees(...).fee_bps(venue, "taker") của TV6 (nguồn: config/fees.yaml, việc C1.01); không có phí
thì để trống, không điền số từ trí nhớ.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

MIN_PER_DAY = 1440
ALLOWED_DATA_KINDS = {"historical", "synthetic", "live"}
EXPECTED_VENUES = {
    "binance_spot",
    "binance_perp",
    "bybit_spot",
    "bybit_perp",
    "okx_spot",
    "okx_perp",
}


def _data_kind_of(df: pd.DataFrame, label: str) -> str:
    """Return exactly one provenance label from the data_kind column."""
    if "data_kind" not in df.columns:
        raise ValueError(f"{label}: missing required data_kind column")

    values = sorted(
        {str(v).strip().lower() for v in df["data_kind"].dropna().unique()}
    )
    if len(values) != 1:
        raise ValueError(f"{label}: expected exactly one data_kind, found {values}")

    kind = values[0]
    if kind not in ALLOWED_DATA_KINDS:
        raise ValueError(f"{label}: invalid data_kind={kind!r}")
    return kind


def _validate_provenance(
    ohlcv: pd.DataFrame,
    live: pd.DataFrame,
    funding: pd.DataFrame,
    manifest: dict,
) -> None:
    """Validate the expected provenance of each T1 input."""
    ohlcv_kind = _data_kind_of(ohlcv, "OHLCV")
    live_kind = _data_kind_of(live, "LIVE")
    funding_kind = _data_kind_of(funding, "FUNDING/OI")
    manifest_kind = str(manifest.get("data_kind", "")).strip().lower()

    if manifest_kind not in ALLOWED_DATA_KINDS:
        raise ValueError(f"MANIFEST: missing or invalid data_kind={manifest_kind!r}")

    actual = {
        "OHLCV": ohlcv_kind,
        "LIVE": live_kind,
        "FUNDING/OI": funding_kind,
        "MANIFEST": manifest_kind,
    }

    if "synthetic" in actual.values():
        raise ValueError(f"Synthetic data must not be used to build T1: {actual}")

    expected = {
        "OHLCV": "historical",
        "LIVE": "live",
        "FUNDING/OI": "historical",
        "MANIFEST": "historical",
    }

    mismatches = {
        name: f"expected {expected[name]!r}, found {kind!r}"
        for name, kind in actual.items()
        if kind != expected[name]
    }
    if mismatches:
        raise ValueError(f"Unexpected T1 input provenance: {mismatches}")


def _fees_for(venues: list[str], fees_path: Path | None) -> dict[str, float]:
    if fees_path is None:
        return {v: np.nan for v in venues}
    from src.cost_model.costs import load_fees  # hàm công khai của TV6 (hợp đồng mục 2.6)

    fs = load_fees(fees_path)
    return {v: float(fs.fee_bps(v, "taker")) for v in venues}


def build_t1(
    ohlcv: pd.DataFrame,
    live: pd.DataFrame,
    funding: pd.DataFrame,
    manifest: dict,
    fees: dict[str, float] | None = None,
    sample: str = "dev_val",
) -> pd.DataFrame:
    _validate_provenance(ohlcv, live, funding, manifest)

    ohlcv = ohlcv.copy()
    ohlcv["venue"] = ohlcv["exchange"] + "_" + ohlcv["market_type"]
    venues = sorted(ohlcv["venue"].unique())

    venue_set = set(venues)
    if venue_set != EXPECTED_VENUES:
        raise ValueError(
            "Unexpected venue universe: "
            f"missing={sorted(EXPECTED_VENUES - venue_set)}, "
            f"extra={sorted(venue_set - EXPECTED_VENUES)}"
        )

    if sample == "dev_val":
        cutoff = pd.Timestamp(manifest["val_end"])
        vol_src = ohlcv[ohlcv["ts_available"] <= cutoff]
    elif sample == "full":
        vol_src = ohlcv
    else:
        raise ValueError("sample phải là dev_val hoặc full")

    fees = fees or {v: np.nan for v in venues}

    rows = []
    for v in venues:
        o_all = ohlcv[ohlcv["venue"] == v]
        o = vol_src[vol_src["venue"] == v]

        quote = sorted({s.split("/")[1] for s in o_all["symbol"].unique()})
        if len(quote) != 1:
            raise ValueError(f"{v}: nhiều quote currency {quote}")

        r = {
            "venue": v,
            "exchange": o_all["exchange"].iloc[0],
            "market_type": o_all["market_type"].iloc[0],
            "quote_currency": quote[0],
            "fee_taker_bps": fees.get(v, np.nan),
            "ohlcv_n_obs": len(o_all),
            "ohlcv_first_utc": o_all["ts_available"].min(),
            "ohlcv_last_utc": o_all["ts_available"].max(),
            "volume_sample": sample,
            "volume_n_obs": len(o),
            "avg_daily_volume_btc": o["volume_base"].mean() * MIN_PER_DAY,
            "avg_daily_volume_usdt": o["volume_quote"].mean() * MIN_PER_DAY,
        }

        lv = live[live["venue"] == v]
        r.update({
            "live_n_obs": len(lv),
            "live_first_utc": lv["ts_recv_utc"].min(),
            "live_last_utc": lv["ts_recv_utc"].max(),
            "avg_spread_bps_live": lv["spread_bps"].mean(),
            "median_spread_bps_live": lv["spread_bps"].median(),
        })

        perp = r["market_type"] == "perp"
        fd = funding[funding["venue"] == v] if perp else funding.iloc[0:0]
        f = fd[fd["funding_rate_bps"].notna()]
        oi_b = fd[fd["open_interest_base"].notna()]
        oi_u = fd[fd["open_interest_usdt"].notna()]

        r.update({
            "funding_n_obs": len(f) if perp else np.nan,
            "funding_first_utc": f["ts_utc"].min() if perp else pd.NaT,
            "funding_last_utc": f["ts_utc"].max() if perp else pd.NaT,
            "funding_interval_h": fd["funding_interval_h"].median() if perp else np.nan,
            "avg_funding_rate_bps": f["funding_rate_bps"].mean() if perp else np.nan,
            "oi_n_obs": len(oi_b) if perp else np.nan,
            "oi_first_utc": oi_b["oi_ts_utc"].min() if perp else pd.NaT,
            "oi_last_utc": oi_b["oi_ts_utc"].max() if perp else pd.NaT,
            "avg_oi_base_btc": oi_b["open_interest_base"].mean() if perp else np.nan,
            "oi_usdt_n_obs": len(oi_u) if perp else np.nan,
            "avg_oi_usdt": oi_u["open_interest_usdt"].mean() if perp else np.nan,
        })
        rows.append(r)

    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ohlcv", type=Path, required=True)
    ap.add_argument("--live", type=Path, required=True)
    ap.add_argument("--funding", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--fees", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--sample", choices=["dev_val", "full"], default="dev_val")
    a = ap.parse_args(argv)

    ohlcv = pd.read_parquet(a.ohlcv)
    live = pd.read_parquet(a.live)
    funding = pd.read_parquet(a.funding)
    manifest = json.loads(a.manifest.read_text(encoding="utf-8"))

    venues = sorted((ohlcv["exchange"] + "_" + ohlcv["market_type"]).unique())
    t1 = build_t1(
        ohlcv,
        live,
        funding,
        manifest,
        _fees_for(venues, a.fees),
        a.sample,
    )

    a.out.parent.mkdir(parents=True, exist_ok=True)
    t1.to_csv(
        a.out,
        index=False,
        float_format="%.10g",
        date_format="%Y-%m-%dT%H:%M:%SZ",
        encoding="utf-8",
    )

    print(f"Đã ghi {a.out} ({len(t1)} venue, volume trên mẫu: {a.sample})")
    if t1["fee_taker_bps"].isna().any():
        print("Cảnh báo: thiếu phí taker (chưa truyền --fees); cột fee_taker_bps để trống.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
