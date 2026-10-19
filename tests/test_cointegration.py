"""C5.01 ADF/KPSS integration order and C5.02 Johansen rank on synthetic series."""
import numpy as np
import pandas as pd
import pytest

from src.cointegration.johansen import johansen_test, select_lag_order
from src.cointegration.stationarity import adf_test, integration_order, kpss_test, summarize_stationarity


@pytest.fixture
def rng():
    return np.random.default_rng(20261019)


@pytest.fixture
def white_noise(rng):
    return pd.Series(rng.normal(0, 1, 500))


@pytest.fixture
def random_walk(rng):
    return pd.Series(np.cumsum(rng.normal(0, 1, 500)))


def test_adf_rejects_unit_root_for_stationary_series(white_noise):
    result = adf_test(white_noise)
    assert result["pvalue"] < 0.05
    assert result["stationary_5pct"] is True


def test_adf_fails_to_reject_unit_root_for_random_walk(random_walk):
    result = adf_test(random_walk)
    assert result["pvalue"] > 0.05
    assert result["stationary_5pct"] is False


def test_kpss_fails_to_reject_stationarity_for_white_noise(white_noise):
    result = kpss_test(white_noise)
    assert result["stationary_5pct"] is True


def test_kpss_rejects_stationarity_for_random_walk(random_walk):
    result = kpss_test(random_walk)
    assert result["stationary_5pct"] is False


def test_results_report_h0_nobs_and_bic_lag(random_walk):
    adf, kpss_res = adf_test(random_walk), kpss_test(random_walk)
    assert adf["lag_selection"] == "BIC" and "unit root" in adf["h0"]
    assert kpss_res["h0"] == "stationary"
    assert adf["nobs"] > 0 and kpss_res["nobs"] == len(random_walk)


def test_select_lag_order_by_bic(rng):
    common = np.cumsum(rng.normal(0, 1, 400))
    panel = pd.DataFrame({"a_spot": common + rng.normal(0, 0.3, 400), "b_spot": common + rng.normal(0, 0.3, 400)})
    k = select_lag_order(panel, max_k_ar_diff=5)
    assert isinstance(k, int) and 0 <= k <= 5
    with pytest.raises(ValueError, match="criterion"):
        select_lag_order(panel, criterion="r2")


def test_adf_and_kpss_reject_short_series():
    with pytest.raises(ValueError, match="observations"):
        adf_test(pd.Series([1.0, 2.0, 3.0]))


def test_integration_order_zero_for_white_noise(white_noise):
    result = integration_order(white_noise)
    assert result["order"] == 0


def test_integration_order_one_for_random_walk(random_walk):
    result = integration_order(random_walk)
    assert result["order"] == 1


def test_summarize_stationarity_one_row_per_venue(random_walk, white_noise):
    panel = pd.DataFrame({"binance_spot": random_walk, "okx_spot": random_walk + white_noise * 0.01})
    summary = summarize_stationarity(panel)
    assert list(summary.index) == ["binance_spot", "okx_spot"]
    assert summary.loc["binance_spot", "order"] == 1


def test_johansen_rejects_single_venue():
    with pytest.raises(ValueError, match="two venues"):
        johansen_test(pd.DataFrame({"binance_spot": [1.0, 2.0, 3.0]}))


def test_johansen_rejects_missing_values(rng):
    df = pd.DataFrame({"a": rng.normal(size=50), "b": rng.normal(size=50)})
    df.iloc[5, 0] = np.nan
    with pytest.raises(ValueError, match="missing values"):
        johansen_test(df)


def test_johansen_finds_no_cointegration_between_independent_random_walks(rng):
    independent = pd.DataFrame({
        "binance_spot": np.cumsum(rng.normal(0, 1, 500)),
        "okx_spot": np.cumsum(rng.normal(0, 1, 500)),
    })
    result = johansen_test(independent, det_order=0, k_ar_diff=1)
    assert result["coint_rank"] == result["rank_trace"] == 0
    assert result["rank_max_eig"] == 0
    assert len(result["trace_crit"]) == len(result["max_eig_crit"]) == 2


def test_johansen_finds_cointegration_on_project_fixture(cfg, multi):
    """The project's own 'multi' fixture is lagged copies of one common random
    walk (src/utils/synthetic.py), so spreads between venues are stationary
    and the panel must show coint_rank >= 1."""
    _, _, panel, truth = multi
    logs = panel.filter(regex=r"^log_close__").dropna()
    logs.columns = logs.columns.str.removeprefix("log_close__")
    result = johansen_test(logs, det_order=0, k_ar_diff=2, significance=95)
    assert set(result["venues"]) == set(logs.columns)
    assert result["coint_rank"] >= 1
    assert truth["known_leader"] in result["venues"]
