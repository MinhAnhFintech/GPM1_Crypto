"""C4.13: execute TV5's real functions; absent modules are pending, never a pass."""
import importlib
import importlib.util
import numpy as np
import pandas as pd
import pytest
from src.utils.synthetic import make_fixture


def tv5_functions(request):
    modules = {"vecm": "src.price_discovery.vecm", "is": "src.price_discovery.info_share", "ils": "src.price_discovery.info_leadership"}
    missing = [name for name in modules.values() if importlib.util.find_spec(name) is None]
    if missing:
        message = "C4.13 awaits TV5: "+", ".join(missing)
        if request.config.getoption("--require-tv5"):
            pytest.fail(message)
        pytest.skip(message)
    loaded = {name: importlib.import_module(module) for name, module in modules.items()}
    return loaded["vecm"].fit_vecm, loaded["is"].hasbrouck_is, loaded["is"].component_share, loaded["ils"].information_leadership


@pytest.mark.tv5
@pytest.mark.parametrize("case", ["multi", "spot_perp"])
def test_real_tv5_functions_recover_injected_leader(cfg, case, request):
    fit_vecm, hasbrouck_is, component_share, information_leadership = tv5_functions(request)
    _, _, panel, truth = make_fixture(cfg, case)
    logs = panel.filter(regex=r"^log_close__").copy()
    logs.columns = logs.columns.str.removeprefix("log_close__")
    result = fit_vecm(logs, k_ar_diff=2, coint_rank=len(logs.columns)-1, deterministic="co")
    shares = hasbrouck_is(result)
    # Public PDF signature uses exchange; values MUST be venue IDs, incl. perp.
    key = "venue" if "venue" in shares else "exchange"
    shares = shares.set_index(key, drop=False)
    assert set(shares.index) == set(logs.columns)
    for column in ["is_lower", "is_upper", "is_mid"]:
        assert np.isfinite(shares[column]).all()
        assert shares[column].between(-1e-7, 1+1e-7).all()
    assert (shares.is_lower <= shares.is_mid).all()
    assert (shares.is_mid <= shares.is_upper).all()
    # Marginal lower/upper bounds come from different orderings; multivariate
    # midpoints need not sum to one. Every feasible IS allocation sums to one.
    assert shares.is_lower.sum() <= 1+1e-5
    assert shares.is_upper.sum() >= 1-1e-5
    if len(shares) == 2:
        assert shares.is_mid.sum() == pytest.approx(1, abs=1e-5)
    cs = component_share(result)
    assert set(cs.index) == set(logs.columns)
    assert cs.sum() == pytest.approx(1, abs=1e-5)
    assert shares.is_mid.idxmax() == truth["known_leader"]
    assert cs.idxmax() == truth["known_leader"]
    # ILS is a pairwise measure: validate every known leader/follower pair;
    # do not pass unqualified multivariate IS bounds into a two-market formula.
    for follower in logs.columns:
        if follower == truth["known_leader"]:
            continue
        pair = fit_vecm(logs[[truth["known_leader"], follower]], k_ar_diff=2, coint_rank=1, deterministic="co")
        pair_is, pair_cs = hasbrouck_is(pair), component_share(pair)
        ils = information_leadership(pair_is, pair_cs).set_index("venue").ils
        assert set(ils.index) == {truth["known_leader"], follower}
        assert np.isfinite(ils).all() and ils.between(0, 1).all()
        assert ils.sum() == pytest.approx(1, abs=1e-5)
        assert ils.idxmax() == truth["known_leader"]
