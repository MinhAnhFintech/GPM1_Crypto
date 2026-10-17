"""C4.06/C4.08: previous-tick alignment; prices in USDT, age in wall-clock time."""
from __future__ import annotations

import numpy as np
import pandas as pd
from src.utils.schema import CONTRACT_VERSION, data_kind, validate_frame


def align_previous_tick(df_long, grid_freq="1min", max_age="5min") -> pd.DataFrame:
    """Build D3 using only the last ts_available <= each UTC grid instant.

    Exactly max_age is allowed. Older observations become NaN with missing=True.
    No interpolation, backfill or cross-venue fill. Input is canonical D1 (one
    symbol/source kind). Grid spans floor(min availability) to ceil(max).
    A row with a later candle opening time is never eligible before its close.
    """
    validate_frame(df_long, "D1")
    age = pd.Timedelta(max_age)
    offset = pd.tseries.frequencies.to_offset(grid_freq)
    if age < pd.Timedelta(0) or offset.nanos <= 0:
        raise ValueError("max_age must be nonnegative; grid_freq must be positive and fixed")
    ordered = df_long.sort_values("ts_available")
    grid = pd.date_range(ordered.ts_available.min().floor(grid_freq), ordered.ts_available.max().ceil(grid_freq), freq=grid_freq, name="ts_available").as_unit("ns")
    panel = pd.DataFrame(index=grid)
    venues = {}
    for (exchange, market_type), group in ordered.groupby(["exchange", "market_type"], sort=True):
        venue = f"{exchange}_{market_type}"
        source = group[["ts_available", "close", "volume_base"]].rename(columns={"ts_available": "source_available"})
        aligned = pd.merge_asof(pd.DataFrame({"ts_available": grid}), source, left_on="ts_available", right_on="source_available", direction="backward", allow_exact_matches=True, tolerance=age)
        close = aligned.close.to_numpy(dtype=float)
        panel[f"close__{venue}"] = close
        panel[f"log_close__{venue}"] = np.log(close)
        panel[f"volume_base__{venue}"] = aligned.volume_base.to_numpy(dtype=float)
        panel[f"missing__{venue}"] = np.isnan(close)
        venues[venue] = {"exchange": exchange, "market_type": market_type}
    panel.attrs = {"contract_version": CONTRACT_VERSION, "data_kind": data_kind(df_long), "symbol": "BTC/USDT", "venues": venues, "grid_freq": grid_freq, "max_age": str(age), "alignment": "previous_tick"}
    return validate_frame(panel, "D3")
