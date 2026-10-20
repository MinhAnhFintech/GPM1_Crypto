"""C5.04: Hasbrouck IS bounds over variable orderings and Gonzalo-Granger Component Share."""
from __future__ import annotations

from itertools import permutations
from math import factorial

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


def _shares_for_ordering(psi: np.ndarray, omega: np.ndarray, order) -> np.ndarray:
    """IS of every venue (original positions) under one Cholesky ordering."""
    order = list(order)
    w = psi[order] @ np.linalg.cholesky(omega[np.ix_(order, order)])
    shares = np.empty(len(psi))
    shares[order] = w ** 2 / (w @ w)
    return shares


def _orderings(n: int, max_orderings: int, seed):
    if factorial(n) <= max_orderings:
        return list(permutations(range(n))), "all"
    rng = np.random.default_rng(seed)
    return [tuple(rng.permutation(n)) for _ in range(max_orderings)], "random"


def hasbrouck_is(result, max_orderings: int = 5040, seed=None) -> pd.DataFrame:
    """Hasbrouck (1995) IS per venue: min/max over Cholesky orderings, is_mid their average.

    All n! orderings are tried when n! <= max_orderings (n <= 7 by default);
    otherwise `max_orderings` random orderings drawn with `seed`. The count and
    method are returned and must be reported. With one common trend the
    long-run impact rows are proportional to alpha_perp', and IS is invariant to
    that scale, so psi = alpha_perp. `exchange` repeats `venue` (PDF name).
    """
    if max_orderings < 1:
        raise ValueError("hasbrouck_is: max_orderings must be positive")
    venues, psi = _common_trend(result)
    omega = np.asarray(result.sigma_u, dtype=float)
    orderings, method = _orderings(len(venues), max_orderings, seed)
    shares = np.array([_shares_for_ordering(psi, omega, order) for order in orderings])
    lower, upper = shares.min(axis=0), shares.max(axis=0)
    return pd.DataFrame({"venue": venues, "exchange": venues, "is_lower": lower, "is_upper": upper,
                         "is_mid": (lower + upper) / 2, "n_orderings": len(orderings), "orderings_method": method})
