"""C5.03 VECM fit and diagnostics; C5.04 Hasbrouck IS bounds and signed Component Share."""
import copy
import numpy as np
import pandas as pd
import pytest

from src.price_discovery.info_share import component_share, hasbrouck_is
from src.price_discovery.vecm import fit_vecm, vecm_diagnostics
from src.utils.synthetic import make_fixture


def _logs(cfg, case, follower_noise=None):
    cfg = copy.deepcopy(cfg)
    if follower_noise is not None:
        cfg["fixtures"]["follower_noise_sigma"] = follower_noise
    _, _, panel, truth = make_fixture(cfg, case)
    logs = panel.filter(regex=r"^log_close__").copy()
    logs.columns = logs.columns.str.removeprefix("log_close__")
    return logs, truth


@pytest.fixture(params=["multi", "spot_perp"])
def identified(cfg, request):
    # Default fixture noise (2e-5) makes the error-correction term nearly
    # collinear with lagged returns at k_ar_diff=2, so alpha is not identified.
    logs, truth = _logs(cfg, request.param, follower_noise=3e-4)
    return logs, truth, fit_vecm(logs, k_ar_diff=2, coint_rank=len(logs.columns) - 1)


def test_fit_vecm_keeps_venue_ids(identified):
    logs, _, result = identified
    assert list(result.names) == list(logs.columns)


def test_fit_vecm_rejects_missing_prices(cfg):
    logs, _ = _logs(cfg, "spot_perp")
    logs.iloc[10, 0] = np.nan
    with pytest.raises(ValueError, match="missing"):
        fit_vecm(logs, k_ar_diff=2, coint_rank=1)


def test_fit_vecm_rejects_columns_without_venue_suffix(cfg):
    logs, _ = _logs(cfg, "spot_perp")
    logs.columns = ["binance", "okx"]
    with pytest.raises(ValueError, match="venue IDs"):
        fit_vecm(logs, k_ar_diff=2, coint_rank=1)


@pytest.mark.parametrize("rank", [0, 2])
def test_fit_vecm_rejects_invalid_rank(cfg, rank):
    logs, _ = _logs(cfg, "spot_perp")
    with pytest.raises(ValueError, match="coint_rank"):
        fit_vecm(logs, k_ar_diff=2, coint_rank=rank)


def test_hasbrouck_is_bounds_and_leader(identified):
    logs, truth, result = identified
    shares = hasbrouck_is(result).set_index("venue")
    assert set(shares.index) == set(logs.columns)
    assert (shares.exchange == shares.index).all()
    for column in ["is_lower", "is_mid", "is_upper"]:
        assert shares[column].between(0, 1).all()
    assert (shares.is_lower <= shares.is_mid).all() and (shares.is_mid <= shares.is_upper).all()
    assert shares.is_lower.sum() <= 1 + 1e-9 and shares.is_upper.sum() >= 1 - 1e-9
    if len(shares) == 2:
        assert shares.is_mid.sum() == pytest.approx(1)
    assert shares.is_mid.idxmax() == truth["known_leader"]


def test_vecm_diagnostics_report_whiteness_and_stability(identified):
    _, _, result = identified
    diag = vecm_diagnostics(result, nlags=10)
    assert 0 <= diag["whiteness_pvalue"] <= 1 and diag["whiteness_lags"] >= 10
    assert diag["n_unit_roots_expected"] == 1
    assert diag["stable"] and diag["max_root_modulus_excl_unit"] < 1


def test_hasbrouck_is_tries_every_ordering(identified):
    logs, _, result = identified
    shares = hasbrouck_is(result)
    assert shares.n_orderings.iloc[0] == {2: 2, 4: 24}[logs.shape[1]]
    assert shares.orderings_method.eq("all").all()


def test_hasbrouck_is_samples_orderings_when_too_many(cfg):
    logs, _ = _logs(cfg, "multi", follower_noise=3e-4)
    result = fit_vecm(logs, k_ar_diff=2, coint_rank=3)
    full = hasbrouck_is(result).set_index("venue")
    sampled = hasbrouck_is(result, max_orderings=5, seed=7).set_index("venue")
    assert sampled.n_orderings.iloc[0] == 5 and sampled.orderings_method.eq("random").all()
    assert (sampled.is_lower >= full.is_lower - 1e-12).all() and (sampled.is_upper <= full.is_upper + 1e-12).all()
    pd.testing.assert_frame_equal(sampled, hasbrouck_is(result, max_orderings=5, seed=7).set_index("venue"))


def test_hasbrouck_is_finds_leader_on_default_fixture(cfg):
    logs, truth = _logs(cfg, "multi")
    shares = hasbrouck_is(fit_vecm(logs, k_ar_diff=2, coint_rank=len(logs.columns) - 1)).set_index("venue")
    assert shares.is_mid.idxmax() == truth["known_leader"]


def test_component_share_sums_to_one_and_finds_leader(identified):
    logs, truth, result = identified
    cs = component_share(result)
    assert set(cs.index) == set(logs.columns)
    assert np.isfinite(cs).all()
    assert cs.sum() == pytest.approx(1)
    assert cs.idxmax() == truth["known_leader"]


def test_component_share_is_not_clipped(cfg):
    logs, _ = _logs(cfg, "multi", follower_noise=3e-4)
    cs = component_share(fit_vecm(logs, k_ar_diff=2, coint_rank=3))
    assert (cs < 0).any(), "signed CS must survive; D6 forbids clipping to [0, 1]"


def test_shares_require_one_common_trend(cfg):
    logs, _ = _logs(cfg, "multi")
    result = fit_vecm(logs, k_ar_diff=2, coint_rank=1)
    for function in (hasbrouck_is, component_share):
        with pytest.raises(ValueError, match="one common trend"):
            function(result)
