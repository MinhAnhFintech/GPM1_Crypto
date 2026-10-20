"""C5.05: Information Leadership Share (Putnins 2013) for one pair of venues."""
from __future__ import annotations

import numpy as np
import pandas as pd


def information_leadership(pair_is: pd.DataFrame, pair_cs: pd.Series) -> pd.DataFrame:
    """ILS from a two-venue VECM: IL_1 = |(IS_1/IS_2) * (CS_2/CS_1)|, IL_2 = 1/IL_1,
    ILS_i = IL_i / (IL_1 + IL_2). Uses the IS midpoint.

    ILS is pairwise; never pass multivariate (>2 venue) IS/CS here. Raises
    ValueError when any IS or CS is zero/non-finite (leadership not identified).
    """
    key = "venue" if "venue" in pair_is else "exchange"
    is_mid = pair_is.set_index(key)["is_mid"].astype(float)
    cs = pd.Series(pair_cs, dtype=float)
    if len(is_mid) != 2 or set(is_mid.index) != set(cs.index):
        raise ValueError("information_leadership: need IS and CS for the same two venues")
    a, b = is_mid.index
    values = np.array([is_mid[a], is_mid[b], cs[a], cs[b]])
    if not np.isfinite(values).all() or (values == 0).any():
        raise ValueError("information_leadership: zero or non-finite IS/CS, leadership not identified")
    il_a = abs((is_mid[a] / is_mid[b]) * (cs[b] / cs[a]))
    il_b = 1 / il_a
    return pd.DataFrame({
        "venue": [a, b],
        "is_mid": [is_mid[a], is_mid[b]],
        "component_share": [cs[a], cs[b]],
        "il": [il_a, il_b],
        "ils": [il_a / (il_a + il_b), il_b / (il_a + il_b)],
    })
