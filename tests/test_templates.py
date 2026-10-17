import numpy as np
import pandas as pd
import pytest
from notebooks.templates.regression_template import fit_ols_hac, fixture_regression


def test_hac_template_runs_on_fixtures(multi):
    result = fixture_regression(multi[2], "binance_spot")
    assert set(result.term) == {"const", "lagged_ret", "volume_base"}
    assert result.std_error_hac.ge(0).all()
    assert result.n_obs.eq(len(multi[2])-2).all()


def test_hac_template_recovers_slope():
    rng = np.random.default_rng(123)
    x = rng.normal(size=1000)
    frame = pd.DataFrame({"x": x, "y": 2+3*x+rng.normal(0, .05, len(x))})
    fitted = fit_ols_hac(frame, "y", ["x"], 5).set_index("term")
    assert fitted.loc["x", "coefficient"] == pytest.approx(3, abs=.01)
