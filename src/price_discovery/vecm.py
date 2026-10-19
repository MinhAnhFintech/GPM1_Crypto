"""C5.03: VECM on venue log prices plus residual-autocorrelation and stability checks."""
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
    # The VECM uses row order only; a dropped-row DatetimeIndex has no frequency.
    model = VECM(logs.astype(float).reset_index(drop=True), k_ar_diff=k_ar_diff, coint_rank=coint_rank, deterministic=deterministic)
    return model.fit()


def vecm_diagnostics(result, nlags: int = 10, tol: float = 1e-6) -> dict:
    """Portmanteau test for residual autocorrelation and companion-matrix stability.

    A rank-r VECM in n prices has exactly n-r unit roots by construction; it is
    stable when every other root of the levels VAR lies inside the unit circle.
    """
    n, k_ar = len(result.names), int(result.k_ar)
    nlags = max(nlags, k_ar + 1)
    white = result.test_whiteness(nlags=nlags)
    coefs = np.asarray(result.var_rep)
    companion = np.zeros((n * k_ar, n * k_ar))
    companion[:n] = np.hstack(list(coefs))
    companion[n:, :-n] = np.eye(n * (k_ar - 1))
    moduli = np.sort(np.abs(np.linalg.eigvals(companion)))[::-1]
    n_unit = n - int(result.coint_rank)
    other = moduli[n_unit:]
    max_other = float(other.max()) if len(other) else 0.0
    return {
        "whiteness_lags": nlags, "whiteness_stat": float(white.test_statistic),
        "whiteness_pvalue": float(white.pvalue), "whiteness_df": int(white.df),
        "residual_autocorrelation_5pct": bool(white.pvalue < 0.05),
        "n_unit_roots_expected": n_unit, "max_root_modulus_excl_unit": max_other,
        "stable": bool(max_other < 1 - tol),
    }
