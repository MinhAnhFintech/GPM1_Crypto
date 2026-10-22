"""TV1 C1.06: build D5 regime flags using the official D4 split manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
import yaml

from src.synchronization.split import validate_panel_split
from src.utils.io_utils import read_parquet, write_parquet


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_index(df: pd.DataFrame, label: str) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        raise ValueError(f"{label}: index must be timezone-aware")
    idx = idx.tz_convert("UTC")
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError(f"{label}: index must be unique and increasing")
    return idx


def load_events(path: Path) -> tuple[dict, list[dict]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    events = payload.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events.yaml: 'events' must be a list")
    return payload, events


def build(panel: pd.DataFrame, features: pd.DataFrame, manifest: dict, events: list[dict],
          quantile: float = 0.90, ma_days: int = 30) -> tuple[pd.DataFrame, float]:
    pidx = utc_index(panel, "D3")
    fidx = utc_index(features, "D3b")
    if not pidx.equals(fidx):
        raise ValueError("D3 and D3b timestamps must match exactly")

    # IMPORTANT: bind C1.06 to the official D4 manifest, including fingerprint,
    # row counts and dev/val/test boundaries. Do not infer the split from N.
    manifest = validate_panel_split(panel, manifest)

    rv_cols = sorted(c for c in features.columns if str(c).startswith("rv_60m__"))
    if len(rv_cols) != 6:
        raise ValueError(f"Expected 6 rv_60m venue columns, found {len(rv_cols)}: {rv_cols}")
    rv_ref = features[rv_cols].median(axis=1, skipna=True)

    dev_start = pd.Timestamp(manifest["dev_start"])
    dev_end = pd.Timestamp(manifest["dev_end"])
    dev_mask = (pidx >= dev_start) & (pidx <= dev_end)
    if int(dev_mask.sum()) != int(manifest["counts"]["dev"]):
        raise ValueError("Development-row count does not match split_manifest.json")
    dev_rv = rv_ref.loc[dev_mask].dropna()
    if dev_rv.empty:
        raise ValueError("No usable development rv_60m observations")
    threshold = float(dev_rv.quantile(quantile))

    price_col = "close__binance_spot"
    if price_col not in panel.columns:
        raise ValueError(f"Missing required reference price: {price_col}")
    price = panel[price_col]

    out = pd.DataFrame({"ts_available": pidx})
    out["flag_high_vol"] = rv_ref.gt(threshold).fillna(False).to_numpy(dtype=bool)
    out["flag_weekend"] = (pidx.dayofweek >= 5)

    event_flag = pd.Series(False, index=pidx, dtype=bool)
    for event in events:
        start = pd.Timestamp(event["start_utc"])
        end = pd.Timestamp(event["end_utc"])
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Event timestamps must be timezone-aware")
        start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
        if end <= start:
            raise ValueError("Event end_utc must be after start_utc")
        event_flag |= (pidx >= start) & (pidx < end)
    out["flag_event"] = event_flag.to_numpy(dtype=bool)

    window = ma_days * 24 * 60
    ma30 = price.rolling(window=window, min_periods=window).mean()
    regime = pd.Series(pd.NA, index=pidx, dtype="string")
    valid = price.notna() & ma30.notna()
    regime.loc[valid & price.gt(ma30)] = "bull"
    regime.loc[valid & price.le(ma30)] = "bear"
    out["regime_bull_bear"] = regime.to_numpy()

    return out, threshold


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="C1.06: build D5 using official split_manifest.json")
    ap.add_argument("--panel", type=Path, default=Path("data/processed/panel_1m_wide.parquet"))
    ap.add_argument("--features", type=Path, default=Path("data/processed/features_1m.parquet"))
    ap.add_argument("--manifest", type=Path, default=Path("data/processed/split_manifest.json"))
    ap.add_argument("--events", type=Path, default=Path("config/events.yaml"))
    ap.add_argument("--output", type=Path, default=Path("data/processed/regime_flags.parquet"))
    ap.add_argument("--receipt", type=Path, default=Path("data/processed/regime_flags_receipt.json"))
    ap.add_argument("--quantile", type=float, default=0.90)
    ap.add_argument("--ma-days", type=int, default=30)
    args = ap.parse_args(argv)

    panel = read_parquet(args.panel)
    features = read_parquet(args.features)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    events_payload, events = load_events(args.events)

    out, threshold = build(panel, features, manifest, events, args.quantile, args.ma_days)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(out, args.output, "D5")

    receipt = {
        "task": "C1.06",
        "rules": {
            "flag_high_vol": "median of 6 rv_60m venue columns > dev-only P90; NaN -> False",
            "flag_weekend": "Saturday/Sunday in UTC",
            "flag_event": "ts_available in any [start_utc, end_utc) from config/events.yaml",
            "regime_bull_bear": "close__binance_spot vs trailing 30-day MA; warm-up/missing price -> <NA>",
        },
        "threshold_p90_rv60_median_6venue": threshold,
        "threshold_dev_only": True,
        "split_source": str(args.manifest),
        "split_manifest_validated_against_panel": True,
        "counts": manifest["counts"],
        "dev_start_utc": manifest["dev_start"],
        "dev_end_utc": manifest["dev_end"],
        "val_start_utc": manifest["val_start"],
        "val_end_utc": manifest["val_end"],
        "test_start_utc": manifest["test_start"],
        "test_end_utc": manifest["test_end"],
        "test_consumed": manifest.get("test_consumed"),
        "panel_fingerprint": manifest["fingerprint"],
        "event_config_version": events_payload.get("version"),
        "n_events": len(events),
        "sha256": {
            "panel_1m_wide.parquet": sha256(args.panel),
            "features_1m.parquet": sha256(args.features),
            "split_manifest.json": sha256(args.manifest),
            "events.yaml": sha256(args.events),
            "regime_flags.parquet": sha256(args.output),
        },
    }
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote D5: {args.output} ({len(out)} rows)")
    print(f"Dev-only P90: {threshold:.16g}")
    print(f"Manifest validated: dev={manifest['counts']['dev']}, val={manifest['counts']['val']}, test={manifest['counts']['test']}")
    print(f"Final test consumed: {manifest.get('test_consumed')}")
    print(f"D5 SHA256: {receipt['sha256']['regime_flags.parquet']}")
    print(f"Receipt: {args.receipt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
