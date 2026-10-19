"""C5.02: VECM on venue log prices; the fit feeds Hasbrouck IS and Component Share."""
from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.tsa.vector_ar.vecm import VECM

from src.utils.schema import VENUE_PATTERN


def fit_vecm(logs: pd.DataFrame, k_ar_diff: int, coint_rank: int, deterministic: str = "co"):
    """Fit a VECM on log prices whose columns are venue IDs (e.g. binance_spot).

    Rows with missing prices must be removed by the caller on an explicit
    sample window; this function never fills or interpolates. Returns the
    statsmodels VECMResults, whose `names` keep the venue IDs.
    """
    if not isinstance(logs, pd.DataFrame) or logs.shape[1] < 2:
        raise ValueError("fit_vecm: need a DataFrame with at least two venue columns")
    bad = [c for c in logs.columns if not VENUE_PATTERN.fullmatch(str(c))]
    if bad:
        raise ValueError(f"fit_vecm: columns must be venue IDs like binance_spot, got {bad}")
    if not np.isfinite(logs.to_numpy(dtype=float)).all():
        raise ValueError("fit_vecm: input must not contain missing or infinite values")
    if not 1 <= coint_rank < logs.shape[1]:
        raise ValueError("fit_vecm: coint_rank must be between 1 and n_venues-1")
    if k_ar_diff < 0:
        raise ValueError("fit_vecm: k_ar_diff must be nonnegative")
    model = VECM(logs.astype(float), k_ar_diff=k_ar_diff, coint_rank=coint_rank, deterministic=deterministic)
    return model.fit()
