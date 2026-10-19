"""C5.01: Johansen trace test selects the cointegration rank feeding fit_vecm."""
from __future__ import annotations

import pandas as pd
from statsmodels.tsa.vector_ar.vecm import coint_johansen

SIGNIFICANCE_COLUMN = {90: 0, 95: 1, 99: 2}


def johansen_test(log_prices: pd.DataFrame, det_order: int = 0, k_ar_diff: int = 1, significance: int = 95) -> dict:
    """Johansen trace & max-eigenvalue test on a log-price panel (>=2 venues).

    det_order: -1 no deterministic term, 0 constant inside the cointegration
    relation (default for log prices with no deterministic trend), 1 linear
    trend. k_ar_diff must match the VAR lag order later passed to fit_vecm.
    coint_rank is picked by the standard sequential trace test: the first r
    where the trace statistic fails to reject H0(rank<=r) at `significance`.
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
    trace_stat, trace_crit = result.lr1, result.cvt[:, col]
    rank = 0
    for r in range(len(venues)):
        if trace_stat[r] > trace_crit[r]:
            rank = r + 1
        else:
            break
    return {
        "venues": venues, "det_order": det_order, "k_ar_diff": k_ar_diff, "significance": significance,
        "trace_stat": trace_stat.tolist(), "trace_crit": trace_crit.tolist(),
        "max_eig_stat": result.lr2.tolist(), "max_eig_crit": result.cvm[:, col].tolist(),
        "eigenvalues": result.eig.tolist(), "coint_rank": int(rank),
    }
