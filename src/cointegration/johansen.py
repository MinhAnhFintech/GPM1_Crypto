"""C5.02: Johansen trace/max-eigen tests; lag (k_ar_diff) chosen by information criterion."""
from __future__ import annotations

import pandas as pd
from statsmodels.tsa.vector_ar.vecm import coint_johansen, select_order

SIGNIFICANCE_COLUMN = {90: 0, 95: 1, 99: 2}
DET_ORDER = {"n": -1, "co": 0, "ci": 0, "lo": 1, "li": 1}


def select_lag_order(log_prices: pd.DataFrame, max_k_ar_diff: int = 10, deterministic: str = "co",
                     criterion: str = "bic") -> int:
    """k_ar_diff (lags in differences) minimising `criterion` (aic, bic, hqic, fpe)."""
    if criterion not in {"aic", "bic", "hqic", "fpe"}:
        raise ValueError("criterion must be aic, bic, hqic or fpe")
    order = select_order(log_prices.to_numpy(dtype=float), maxlags=max_k_ar_diff, deterministic=deterministic)
    return int(getattr(order, criterion))


def _sequential_rank(stats, crits) -> int:
    rank = 0
    for stat, crit in zip(stats, crits):
        if stat <= crit:
            break
        rank += 1
    return rank


def johansen_test(log_prices: pd.DataFrame, det_order: int = 0, k_ar_diff: int = 1, significance: int = 95) -> dict:
    """Johansen trace & max-eigenvalue tests on a log-price panel (>=2 venues).

    det_order: -1 none, 0 constant (default for log prices without trend),
    1 linear trend. k_ar_diff must match the lag later passed to fit_vecm.
    Each rank is the first r where the statistic does not exceed its critical
    value at `significance`. Critical values are thresholds, not p-values.
    """
    if significance not in SIGNIFICANCE_COLUMN:
        raise ValueError("johansen_test: significance must be 90, 95 or 99")
    if log_prices.shape[1] < 2:
        raise ValueError("johansen_test: need at least two venues")
    if log_prices.isna().any().any():
        raise ValueError("johansen_test: input must not contain missing values")
    venues = list(log_prices.columns)
    result = coint_johansen(log_prices.to_numpy(dtype=float), det_order, k_ar_diff)
    col = SIGNIFICANCE_COLUMN[significance]
    trace_crit, max_eig_crit = result.cvt[:, col], result.cvm[:, col]
    rank_trace = _sequential_rank(result.lr1, trace_crit)
    return {
        "venues": venues, "det_order": det_order, "k_ar_diff": k_ar_diff, "significance": significance,
        "nobs": int(len(log_prices)),
        "trace_stat": result.lr1.tolist(), "trace_crit": trace_crit.tolist(),
        "max_eig_stat": result.lr2.tolist(), "max_eig_crit": max_eig_crit.tolist(),
        "eigenvalues": result.eig.tolist(),
        "rank_trace": rank_trace, "rank_max_eig": _sequential_rank(result.lr2, max_eig_crit),
        "coint_rank": rank_trace,
    }
