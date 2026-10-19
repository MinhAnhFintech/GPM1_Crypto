"""C5.01: ADF/KPSS on log prices and their differences; ADF lag chosen by BIC."""
from __future__ import annotations

import warnings
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller, kpss

MIN_OBS = 20


def _clean(series: pd.Series, name: str) -> np.ndarray:
    values = pd.Series(series).to_numpy(dtype=float)
    if np.isnan(values).any():
        raise ValueError(f"{name}: series must not contain missing values")
    if len(values) < MIN_OBS:
        raise ValueError(f"{name}: need at least {MIN_OBS} observations, got {len(values)}")
    return values


def adf_test(series: pd.Series, regression: str = "c", autolag: str = "BIC") -> dict:
    """Augmented Dickey-Fuller test. H0: series has a unit root (non-stationary)."""
    values = _clean(series, "adf_test")
    statistic, pvalue, used_lag, nobs, crit, _ = adfuller(values, regression=regression, autolag=autolag, result_object=False)
    return {
        "test": "adf", "h0": "unit root (non-stationary)", "lag_selection": autolag,
        "statistic": float(statistic), "pvalue": float(pvalue),
        "used_lag": int(used_lag), "nobs": int(nobs),
        "critical_values": {k: float(v) for k, v in crit.items()},
        "stationary_5pct": bool(pvalue < 0.05),
    }


def kpss_test(series: pd.Series, regression: str = "c", nlags: str = "auto") -> dict:
    """KPSS test. H0: series is stationary (opposite null to ADF, used jointly)."""
    values = _clean(series, "kpss_test")
    with warnings.catch_warnings():
        # statsmodels warns when the statistic falls outside its tabulated
        # p-value range; the clipped p-value it returns is still informative.
        warnings.simplefilter("ignore", category=UserWarning)
        statistic, pvalue, used_lag, crit = kpss(values, regression=regression, nlags=nlags, result_object=False)
    return {
        "test": "kpss", "h0": "stationary", "statistic": float(statistic),
        # KPSS p-values are interpolated from a table and clipped to [0.01, 0.1].
        "pvalue": float(pvalue), "used_lag": int(used_lag), "nobs": int(len(values)),
        "critical_values": {k: float(v) for k, v in crit.items()},
        "stationary_5pct": bool(pvalue > 0.05),
    }


def integration_order(series: pd.Series, max_order: int = 2, alpha: float = 0.05) -> dict:
    """Smallest d in 0..max_order where ADF and KPSS agree the d-th difference is stationary.

    Returns order=None (with both tests' verdicts at max_order) when ADF and KPSS
    still disagree after max_order differences; callers must not silently assume I(1).
    """
    if max_order < 0:
        raise ValueError("integration_order: max_order must be nonnegative")
    diffed = pd.Series(series).astype(float)
    history = []
    for d in range(max_order + 1):
        if d > 0:
            diffed = diffed.diff().dropna()
        adf = adf_test(diffed, regression="c")
        kpss_res = kpss_test(diffed, regression="c")
        agree = (adf["pvalue"] < alpha) and (kpss_res["pvalue"] > alpha)
        history.append({"d": d, "adf": adf, "kpss": kpss_res, "agree_stationary": agree})
        if agree:
            return {"order": d, "alpha": alpha, "history": history}
    return {"order": None, "alpha": alpha, "history": history}


def summarize_stationarity(log_prices: pd.DataFrame, max_order: int = 2, alpha: float = 0.05) -> pd.DataFrame:
    """Per-venue integration order on a log-price panel; one row per column."""
    if log_prices.shape[1] < 1:
        raise ValueError("summarize_stationarity: need at least one venue column")
    rows = []
    for venue in log_prices.columns:
        result = integration_order(log_prices[venue], max_order=max_order, alpha=alpha)
        rows.append({"venue": venue, "order": result["order"], "alpha": alpha})
    return pd.DataFrame(rows).set_index("venue")
