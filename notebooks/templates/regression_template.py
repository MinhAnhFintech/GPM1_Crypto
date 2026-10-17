"""OLS-HAC template for TV3; fit on chronological, single-venue observations."""
from __future__ import annotations
import numpy as np
import pandas as pd


def fit_ols_hac(frame, y_col, x_cols, maxlags):
    """Estimate OLS with HAC errors, dropping missing model rows explicitly.

    Input must be time-sorted. Do not concatenate venue time series then use an
    ordinary HAC covariance: the artificial boundaries invalidate that ordering.
    For pooled venue panels TV3 must choose a justified panel covariance method.
    Predictor units are retained; maxlags is in input observations, not minutes.
    """
    import statsmodels.api as sm
    if not isinstance(maxlags, int) or maxlags < 0:
        raise ValueError("maxlags must be a nonnegative integer")
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("HAC input must have a unique, sorted time index")
    if "venue" in frame and frame.venue.nunique() > 1:
        raise ValueError("Use one venue per HAC fit or specify a panel covariance model")
    used = frame[[y_col, *x_cols]].replace([np.inf, -np.inf], np.nan).dropna()
    x = sm.add_constant(used[x_cols].astype(float), has_constant="add")
    if len(used) <= max(x.shape[1], maxlags+1) or np.linalg.matrix_rank(x) < x.shape[1]:
        raise ValueError("Too few complete rows or a singular regression design")
    fitted = sm.OLS(used[y_col].astype(float), x).fit(cov_type="HAC", cov_kwds={"maxlags": maxlags})
    conf = fitted.conf_int()
    return pd.DataFrame({"term": fitted.params.index, "coefficient": fitted.params.values, "std_error_hac": fitted.bse.values, "p_value": fitted.pvalues.values, "ci_lower": conf.iloc[:, 0].values, "ci_upper": conf.iloc[:, 1].values, "n_obs": int(fitted.nobs), "n_dropped": len(frame)-len(used), "hac_maxlags": maxlags})


def fixture_regression(panel, venue, maxlags=5):
    """Smoke demo on synthetic returns; not a substitute for the IS regression."""
    close = panel[f"close__{venue}"]
    demo = pd.DataFrame({"ret_1m": np.log(close).diff(), "lagged_ret": np.log(close).diff().shift(1), "volume_base": panel[f"volume_base__{venue}"]}, index=panel.index)
    return fit_ols_hac(demo, "ret_1m", ["lagged_ret", "volume_base"], maxlags)
