"""C3.08 — Compare rolling Information Share across market regimes.

Inputs (relative to project root by default):
  outputs/tables/rolling_is.parquet
  data/processed/regime_flags.parquet

Output:
  outputs/tables/T6_stress.csv

The regime for each rolling IS estimate is assigned from the latest regime row
whose ts_available is <= the IS window_end (or available_at if window_end is
not present). This is an explicit alignment convention; review it with TV4 if
team conventions differ. Only successful/usable IS rows are included.

Source checks run BEFORE anything is written (a failure exits with code 1 and the existing T6_stress.csv is left untouched):
  * rolling_is (D6) and regime_flags (D5) must each carry a data_kind (column, DataFrame metadata, or for D5 the receipt
    next to it, regime_flags_receipt.json) and the two must be the same kind; a synthetic D6 can never be combined with a
    historical D5;
  * the kind must fit the output location: real results (outputs/ without a directory named "synthetic") need historical
    inputs, a path under a directory named "synthetic" needs synthetic inputs;
  * an unlabeled D5 is refused unless --allow-unlabeled-flags is given (then only a warning is printed).
The CSV is written through a temporary file and then moved into place.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd


REGIME_COLUMNS = {
    "flag_weekend": "weekend_vs_weekday",
    "flag_high_vol": "high_vs_low_volatility",
    "regime_bull_bear": "bull_vs_bear",
    "flag_event": "event_vs_normal",
}


def _kind_of(df: pd.DataFrame, label: str):
    """data_kind from the column and from df.attrs; disagreement is an error; none -> None."""
    kinds = set(df["data_kind"].dropna().astype(str).unique()) if "data_kind" in df.columns else set()
    meta = df.attrs.get("data_kind")
    if meta:
        kinds.add(str(meta))
    if len(kinds) > 1:
        raise ValueError(f"{label}: inconsistent data_kind {sorted(kinds)}")
    return next(iter(kinds)) if kinds else None


def _receipt_kind(flags_path: Path):
    receipt = flags_path.with_name(flags_path.stem + "_receipt.json")
    if receipt.exists():
        try:
            return json.loads(receipt.read_text(encoding="utf-8")).get("data_kind")
        except (OSError, ValueError):
            return None
    return None


def _output_zone(path: Path) -> str:
    return "synthetic" if any(p.lower() == "synthetic" for p in Path(path).resolve().parts) else "real"


def check_sources(rolling_kind, flags_kind, output_path: Path, allow_unlabeled_flags: bool = False) -> None:
    """Raise ValueError (before anything is written) when the sources do not fit each other or the output location."""
    zone = _output_zone(output_path)
    want = "historical" if zone == "real" else "synthetic"
    if rolling_kind is None:
        raise ValueError("rolling IS input has no data_kind (column or metadata); cannot verify its source")
    if flags_kind is None:
        if not allow_unlabeled_flags:
            raise ValueError("regime flags input has no data_kind (column, metadata or receipt); cannot verify its source "
                             "(--allow-unlabeled-flags only prints a warning instead)")
        print("WARNING: regime flags have no data_kind; source not verified", file=sys.stderr)
    elif flags_kind != rolling_kind:
        raise ValueError(f"rolling IS is {rolling_kind!r} but regime flags are {flags_kind!r}: refusing to combine them")
    if rolling_kind != want:
        raise ValueError(f"inputs are {rolling_kind!r} but output location {output_path} ({zone}) needs {want!r}")


def _write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8-sig")
    os.replace(tmp, path)


def _load_parquet(path: Path, label: str) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        raise RuntimeError(f"Cannot read {label} at {path}: {exc}") from exc
    if df.empty:
        raise ValueError(f"{label} is empty: {path}")
    # Some project files store ts_available as the index rather than a column.
    if "ts_available" not in df.columns and df.index.name == "ts_available":
        df = df.reset_index()
    return df


def _to_utc(series: pd.Series, name: str) -> pd.Series:
    out = pd.to_datetime(series, utc=True, errors="coerce")
    if out.isna().any():
        bad = int(out.isna().sum())
        raise ValueError(f"Column {name!r} has {bad} invalid/missing timestamps")
    return out


def _normalise_regime_values(flags: pd.DataFrame) -> pd.DataFrame:
    # Support bools, 0/1 flags, and common text labels.
    if "flag_weekend" in flags.columns:
        s = flags["flag_weekend"]
        if s.dtype == object:
            mapped = s.astype(str).str.strip().str.lower().map({
                "true": True, "false": False, "1": True, "0": False,
                "yes": True, "no": False, "weekend": True, "weekday": False,
            })
            flags["flag_weekend"] = mapped.where(mapped.notna(), s)
        flags["flag_weekend"] = flags["flag_weekend"].map(
            lambda x: "weekend" if x is True or (isinstance(x, (int, np.integer, float)) and not pd.isna(x) and x == 1)
            else "weekday" if x is False or (isinstance(x, (int, np.integer, float)) and not pd.isna(x) and x == 0)
            else (str(x).strip().lower() if pd.notna(x) else np.nan)
        )
        flags["flag_weekend"] = flags["flag_weekend"].replace({"true": "weekend", "false": "weekday", "1.0": "weekend", "0.0": "weekday"})

    if "flag_high_vol" in flags.columns:
        s = flags["flag_high_vol"]
        if s.dtype == object:
            mapped = s.astype(str).str.strip().str.lower().map({
                "true": True, "false": False, "1": True, "0": False,
                "yes": True, "no": False, "high": True, "low": False,
            })
            flags["flag_high_vol"] = mapped.where(mapped.notna(), s)
        flags["flag_high_vol"] = flags["flag_high_vol"].map(
            lambda x: "high_volatility" if x is True or (isinstance(x, (int, np.integer, float)) and not pd.isna(x) and x == 1)
            else "low_volatility" if x is False or (isinstance(x, (int, np.integer, float)) and not pd.isna(x) and x == 0)
            else (str(x).strip().lower() if pd.notna(x) else np.nan)
        )
        flags["flag_high_vol"] = flags["flag_high_vol"].replace({"true": "high_volatility", "false": "low_volatility", "1.0": "high_volatility", "0.0": "low_volatility", "high": "high_volatility", "low": "low_volatility"})

    if "flag_event" in flags.columns:
        s = flags["flag_event"]
        if s.dtype == object or str(s.dtype) in ("string", "boolean"):
            s = s.astype("string").str.strip().str.lower().map({"true": True, "false": False, "1": True, "0": False})
        flags["flag_event"] = s.map(lambda x: "event" if x is True or (pd.notna(x) and x == 1)
                                    else "normal" if x is False or (pd.notna(x) and x == 0) else np.nan)

    if "regime_bull_bear" in flags.columns:
        flags["regime_bull_bear"] = flags["regime_bull_bear"].astype("string").str.strip().str.lower().replace({
            "bullish": "bull", "bearish": "bear", "bull_market": "bull", "bear_market": "bear",
        })
    return flags


def _usable_rows(rolling: pd.DataFrame) -> pd.DataFrame:
    # Current project output uses `venue`; older D6 contract used `exchange`.
    # Normalize the identifier once, without changing the source parquet.
    if "exchange" not in rolling.columns and "venue" in rolling.columns:
        rolling = rolling.rename(columns={"venue": "exchange"})
    if "exchange" not in rolling.columns:
        raise ValueError("rolling_is.parquet must contain 'venue' or 'exchange' column")
    # Prefer the central IS estimate. If not supplied, use midpoint of bounds.
    if "is_mid" not in rolling.columns:
        if {"is_lower", "is_upper"}.issubset(rolling.columns):
            rolling["is_mid"] = (pd.to_numeric(rolling["is_lower"], errors="coerce") + pd.to_numeric(rolling["is_upper"], errors="coerce")) / 2
        else:
            raise ValueError("rolling_is.parquet needs 'is_mid' or both 'is_lower' and 'is_upper'")
    rolling["is_mid"] = pd.to_numeric(rolling["is_mid"], errors="coerce")
    rolling = rolling.loc[rolling["is_mid"].notna()].copy()

    if "status" in rolling.columns:
        # Keep conventional successful statuses; unknown statuses are retained
        # unless they clearly indicate failure, so project-specific labels work.
        bad = rolling["status"].astype(str).str.lower().str.contains(r"fail|error|invalid|skip", regex=True, na=False)
        rolling = rolling.loc[~bad].copy()
    if rolling.empty:
        raise ValueError("No usable rolling IS rows remain after filtering missing values/status failures")
    return rolling


def _make_regime_summary(merged: pd.DataFrame) -> pd.DataFrame:
    outputs: list[dict] = []
    comparisons = [
        ("weekend_vs_weekday", "flag_weekend", "weekend", "weekday"),
        ("high_vs_low_volatility", "flag_high_vol", "high_volatility", "low_volatility"),
        ("bull_vs_bear", "regime_bull_bear", "bull", "bear"),
        ("event_vs_normal", "flag_event", "event", "normal"),
    ]
    for comparison, col, group_a, group_b in comparisons:
        if col not in merged.columns:
            print(f"WARNING: regime column {col!r} missing; skipping {comparison}", file=sys.stderr)
            continue
        valid = merged.loc[merged[col].notna()].copy()
        if valid.empty:
            print(f"WARNING: no valid labels in {col!r}; skipping {comparison}", file=sys.stderr)
            continue
        # Per-exchange summaries preserve venue-level differences.
        for exchange, exch_df in valid.groupby("exchange", dropna=False):
            groups = {
                group_a: exch_df.loc[exch_df[col].astype(str).str.lower() == group_a, "is_mid"].dropna(),
                group_b: exch_df.loc[exch_df[col].astype(str).str.lower() == group_b, "is_mid"].dropna(),
            }
            for label, values in groups.items():
                outputs.append({
                    "comparison": comparison,
                    "exchange": exchange,
                    "regime_column": col,
                    "regime": label,
                    "n_obs": int(values.size),
                    "mean_is_mid": float(values.mean()) if values.size else np.nan,
                    "std_is_mid": float(values.std(ddof=1)) if values.size > 1 else np.nan,
                    "median_is_mid": float(values.median()) if values.size else np.nan,
                })
            mean_a = groups[group_a].mean() if groups[group_a].size else np.nan
            mean_b = groups[group_b].mean() if groups[group_b].size else np.nan
            outputs.append({
                "comparison": comparison,
                "exchange": exchange,
                "regime_column": col,
                "regime": f"difference_{group_a}_minus_{group_b}",
                "n_obs": int(groups[group_a].size + groups[group_b].size),
                "mean_is_mid": float(mean_a - mean_b) if pd.notna(mean_a) and pd.notna(mean_b) else np.nan,
                "std_is_mid": np.nan,
                "median_is_mid": np.nan,
            })
        # Pooled cross-exchange view, explicitly labelled.
        groups = {
            group_a: valid.loc[valid[col].astype(str).str.lower() == group_a, "is_mid"].dropna(),
            group_b: valid.loc[valid[col].astype(str).str.lower() == group_b, "is_mid"].dropna(),
        }
        for label, values in groups.items():
            outputs.append({
                "comparison": comparison,
                "exchange": "ALL_EXCHANGES_POOLED",
                "regime_column": col,
                "regime": label,
                "n_obs": int(values.size),
                "mean_is_mid": float(values.mean()) if values.size else np.nan,
                "std_is_mid": float(values.std(ddof=1)) if values.size > 1 else np.nan,
                "median_is_mid": float(values.median()) if values.size else np.nan,
            })
        mean_a = groups[group_a].mean() if groups[group_a].size else np.nan
        mean_b = groups[group_b].mean() if groups[group_b].size else np.nan
        outputs.append({
            "comparison": comparison,
            "exchange": "ALL_EXCHANGES_POOLED",
            "regime_column": col,
            "regime": f"difference_{group_a}_minus_{group_b}",
            "n_obs": int(groups[group_a].size + groups[group_b].size),
            "mean_is_mid": float(mean_a - mean_b) if pd.notna(mean_a) and pd.notna(mean_b) else np.nan,
            "std_is_mid": np.nan,
            "median_is_mid": np.nan,
        })
    return pd.DataFrame(outputs, columns=[
        "comparison", "exchange", "regime_column", "regime", "n_obs",
        "mean_is_mid", "std_is_mid", "median_is_mid",
    ])


def run(project_root: Path, allow_unlabeled_flags: bool = False) -> Path:
    rolling_path = project_root / "outputs" / "tables" / "rolling_is.parquet"
    flags_path = project_root / "data" / "processed" / "regime_flags.parquet"
    output_path = project_root / "outputs" / "tables" / "T6_stress.csv"

    rolling = _load_parquet(rolling_path, "rolling IS input")
    flags = _load_parquet(flags_path, "regime flags input")
    rolling_kind = _kind_of(rolling, "rolling IS input")
    flags_kind = _kind_of(flags, "regime flags input") or _receipt_kind(flags_path)
    check_sources(rolling_kind, flags_kind, output_path, allow_unlabeled_flags)    # nothing is written before this passes
    rolling = _usable_rows(rolling)

    time_col = "window_end" if "window_end" in rolling.columns else "available_at" if "available_at" in rolling.columns else None
    if time_col is None:
        raise ValueError("rolling_is.parquet must contain 'window_end' or 'available_at' to align regimes")
    if "ts_available" not in flags.columns:
        raise ValueError("regime_flags.parquet must contain 'ts_available' column or index named 'ts_available'")

    rolling[time_col] = _to_utc(rolling[time_col], time_col)
    flags["ts_available"] = _to_utc(flags["ts_available"], "ts_available")
    flags = _normalise_regime_values(flags)
    wanted = [c for c in ["ts_available", *REGIME_COLUMNS.keys()] if c in flags.columns]
    flags = flags[wanted].drop_duplicates("ts_available").sort_values("ts_available")
    rolling = rolling.sort_values(time_col).copy()

    # merge_asof prevents using a regime label that was not yet available.
    merged = pd.merge_asof(
        rolling,
        flags,
        left_on=time_col,
        right_on="ts_available",
        direction="backward",
        allow_exact_matches=True,
    )
    merged = merged.dropna(subset=["exchange", "is_mid"])
    if merged.empty:
        raise ValueError("No rolling IS rows could be aligned with regime flags")

    result = _make_regime_summary(merged)
    if result.empty:
        raise ValueError("No regime comparisons produced. Check regime_flags columns/labels.")
    _write_csv_atomic(result, output_path)
    print(f"C3.08 complete: {output_path}")
    print(f"Aligned rolling IS rows: {len(merged):,}")
    print(f"Output rows: {len(result):,}")
    print("Note: pooled rows combine exchanges; use exchange-specific rows for venue comparisons.")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="C3.08: rolling IS by market regime")
    parser.add_argument("--project-root", type=Path, default=Path.cwd(), help="Project root (default: current directory)")
    parser.add_argument("--allow-unlabeled-flags", action="store_true",
                        help="do not refuse a regime_flags file without data_kind (prints a warning)")
    args = parser.parse_args()
    try:
        run(args.project_root.resolve(), args.allow_unlabeled_flags)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()