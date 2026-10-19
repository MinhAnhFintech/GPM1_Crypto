"""C5.03 Hasbrouck IS bounds and C5.04 Gonzalo-Granger Component Share from a VECM fit."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.linalg import null_space


def _common_trend(result) -> tuple[list[str], np.ndarray]:
    """Venue IDs and alpha_perp, the weights of innovations in the single common trend."""
    venues = [str(v) for v in result.names]
    alpha = np.asarray(result.alpha, dtype=float)
    if len(venues) - alpha.shape[1] != 1:
        raise ValueError("IS/CS require exactly one common trend: coint_rank = n_venues - 1")
    alpha_perp = null_space(alpha.T)[:, 0]
    if abs(alpha_perp.sum()) < 1e-12:
        raise ValueError("alpha_perp sums to zero; shares are not identified")
    return venues, alpha_perp


def component_share(result) -> pd.Series:
    """CS_i = alpha_perp_i / sum(alpha_perp). Signed: never clipped to [0, 1]."""
    venues, alpha_perp = _common_trend(result)
    return pd.Series(alpha_perp / alpha_perp.sum(), index=venues, name="component_share")


def _share_when_ordered(psi: np.ndarray, omega: np.ndarray, i: int, first: bool) -> float:
    others = [j for j in range(len(psi)) if j != i]
    order = [i] + others if first else others + [i]
    chol = np.linalg.cholesky(omega[np.ix_(order, order)])
    w = psi[order] @ chol
    return float(w[0 if first else -1] ** 2 / (w @ w))


def hasbrouck_is(result) -> pd.DataFrame:
    """Hasbrouck (1995) IS per venue: bounds from ordering the venue first and
    last in the Cholesky factor of the residual covariance, is_mid their average.

    With one common trend the long-run impact rows are proportional to
    alpha_perp', and IS is invariant to that scale, so psi = alpha_perp.
    `exchange` repeats `venue` to keep the PDF column name with venue IDs.
    """
    venues, psi = _common_trend(result)
    omega = np.asarray(result.sigma_u, dtype=float)
    rows = []
    for i, venue in enumerate(venues):
        # Ordering first is the upper bound only for positively correlated
        # innovations; take min/max so the bounds hold for any sign.
        shares = (_share_when_ordered(psi, omega, i, first=True), _share_when_ordered(psi, omega, i, first=False))
        lower, upper = min(shares), max(shares)
        rows.append({"venue": venue, "exchange": venue, "is_lower": lower, "is_upper": upper, "is_mid": (lower + upper) / 2})
    return pd.DataFrame(rows)
