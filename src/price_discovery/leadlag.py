"""C4.10: pairwise return cross-correlations on a complete UTC grid."""
from itertools import combinations
import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype
from src.utils.time_utils import require_utc


def lead_lag_xcorr(returns_df, max_lag) -> pd.DataFrame:
    """corr(ret_A(t), ret_B(t+lag)), lag measured in grid observations.

    POSITIVE lag means venue_a leads venue_b by lag grid ticks; negative means
    venue_b leads. Spot/perp pairs are allowed for price discovery. Missing rows
    stay on the grid and pairs are removed only after shifting (no gap collapse).
    Correlation is NaN for <3 paired values or zero variance, never a fake zero.
    """
    require_utc(returns_df.index, "returns index")
    if not returns_df.index.is_unique or not returns_df.index.is_monotonic_increasing:
        raise ValueError("Return index must be sorted and unique")
    if isinstance(max_lag, bool) or not isinstance(max_lag, (int, np.integer)) or max_lag < 0:
        raise ValueError("max_lag must be a nonnegative integer")
    if len(returns_df) < 3 or returns_df.shape[1] < 2:
        raise ValueError("At least three rows and two venues are required")
    steps = np.diff(returns_df.index.asi8)
    if not (steps == steps[0]).all():
        raise ValueError("Keep a regular grid; do not drop missing timestamps")
    if any(not is_numeric_dtype(returns_df[c]) for c in returns_df):
        raise ValueError("Returns must be numeric")
    rows = []
    for a, b in combinations(returns_df.columns, 2):
        for lag in range(-max_lag, max_lag+1):
            pair = pd.concat([returns_df[a], returns_df[b].shift(-lag)], axis=1).replace([np.inf, -np.inf], np.nan).dropna()
            n = len(pair)
            corr = pair.iloc[:, 0].corr(pair.iloc[:, 1]) if n >= 3 and (pair.std(ddof=1) > 0).all() else np.nan
            rows.append({"venue_a": str(a).removeprefix("ret_1m__"), "venue_b": str(b).removeprefix("ret_1m__"), "lag": lag, "lag_s": float(lag*steps[0]/1e9), "correlation": corr, "n_obs": n, "lag_convention": "positive: venue_a leads venue_b"})
    return pd.DataFrame(rows)
