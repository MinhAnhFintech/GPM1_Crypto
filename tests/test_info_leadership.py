"""C5.05: pairwise Information Leadership Share."""
import copy
import numpy as np
import pandas as pd
import pytest

from src.price_discovery.info_leadership import information_leadership
from src.price_discovery.info_share import component_share, hasbrouck_is
from src.price_discovery.vecm import fit_vecm
from src.utils.synthetic import make_fixture


def _is(a, b):
    return pd.DataFrame({"venue": ["x_spot", "y_spot"], "is_mid": [a, b]})


def test_ils_matches_putnins_formula():
    out = information_leadership(_is(0.8, 0.2), pd.Series({"x_spot": 0.6, "y_spot": 0.4})).set_index("venue")
    il = (0.8 / 0.2) * (0.4 / 0.6)
    assert out.loc["x_spot", "il"] == pytest.approx(il)
    assert out.loc["x_spot", "ils"] == pytest.approx(il / (il + 1 / il))
    assert out.ils.sum() == pytest.approx(1)


def test_ils_symmetric_when_shares_equal():
    out = information_leadership(_is(0.5, 0.5), pd.Series({"x_spot": 0.5, "y_spot": 0.5}))
    assert out.ils.tolist() == pytest.approx([0.5, 0.5])


def test_ils_accepts_exchange_key_from_pdf_signature():
    pair_is = _is(0.7, 0.3).rename(columns={"venue": "exchange"})
    out = information_leadership(pair_is, pd.Series({"x_spot": 0.7, "y_spot": 0.3}))
    assert set(out.venue) == {"x_spot", "y_spot"}


@pytest.mark.parametrize("cs", [{"x_spot": 0.0, "y_spot": 1.0}, {"x_spot": np.nan, "y_spot": 1.0}])
def test_ils_rejects_unidentified_inputs(cs):
    with pytest.raises(ValueError, match="not identified"):
        information_leadership(_is(0.6, 0.4), pd.Series(cs))


def test_ils_rejects_more_than_two_venues():
    three = pd.DataFrame({"venue": ["a_spot", "b_spot", "c_spot"], "is_mid": [0.5, 0.3, 0.2]})
    with pytest.raises(ValueError, match="two venues"):
        information_leadership(three, pd.Series({"a_spot": 0.5, "b_spot": 0.3, "c_spot": 0.2}))


@pytest.mark.parametrize("case", ["multi", "spot_perp"])
def test_ils_finds_leader_in_every_pair(cfg, case):
    cfg = copy.deepcopy(cfg)
    cfg["fixtures"]["follower_noise_sigma"] = 3e-4  # identified DGP, see test_vecm_is_cs.py
    _, _, panel, truth = make_fixture(cfg, case)
    logs = panel.filter(regex=r"^log_close__").copy()
    logs.columns = logs.columns.str.removeprefix("log_close__")
    leader = truth["known_leader"]
    for follower in logs.columns.drop(leader):
        pair = fit_vecm(logs[[leader, follower]], k_ar_diff=2, coint_rank=1)
        ils = information_leadership(hasbrouck_is(pair), component_share(pair)).set_index("venue").ils
        assert ils.between(0, 1).all() and ils.sum() == pytest.approx(1)
        assert ils.idxmax() == leader
