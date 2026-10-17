"""Strict, timezone-aware UTC conversion; never assume a naive time is UTC."""
from __future__ import annotations

import pandas as pd


def utc_timestamp(value) -> pd.Timestamp:
    """Convert an aware instant to UTC (nanoseconds); reject naive and NaT."""
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"Timestamp must be timezone-aware, not {value!r}")
    return stamp.tz_convert("UTC").as_unit("ns")


def utc_series(values, *, nullable: bool = False) -> pd.Series:
    """Convert aware input values to datetime64[ns, UTC]; preserve allowed NaT."""
    converted = []
    for value in values:
        if pd.isna(value) and nullable:
            converted.append(pd.NaT)
        else:
            converted.append(utc_timestamp(value))
    return pd.Series(converted, index=getattr(values, "index", None), dtype="datetime64[ns, UTC]")


def require_utc(values, name: str = "timestamp", *, nullable: bool = False) -> None:
    """Validate already-normalized UTC data without silently fixing input."""
    dtype = values.dtype
    if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC" or dtype.unit != "ns":
        raise ValueError(f"{name}: expected datetime64[ns, UTC], got {dtype}")
    if not nullable and values.isna().any():
        raise ValueError(f"{name}: missing timestamp")
