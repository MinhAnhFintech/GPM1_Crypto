"""TV4 D6 handoff: preserve signed CS while validating bounded IS and ILS."""
import numpy as np
import pandas as pd
import pytest

from src.utils.schema import validate_frame


@pytest.fixture
def rolling():
    times = pd.to_datetime(["2026-01-01T00:00Z"] * 2).as_unit("ns")
    return pd.DataFrame({
        "window_start": times,
        "window_end": times + pd.Timedelta(hours=1),
        "available_at": times + pd.Timedelta(hours=1),
        "venue": ["binance_spot", "binance_perp"],
        "is_lower": [0.6, 0.1], "is_mid": [0.7, 0.3], "is_upper": [0.9, 0.4],
        "component_share": [1.2, -0.2], "ils": [0.8, 0.2],
        "leader": ["binance_spot"] * 2, "status": ["ok"] * 2, "reason": [""] * 2,
    })


def test_signed_component_share_survives_handoff(rolling):
    original = rolling.copy(deep=True)
    validate_frame(rolling, "D6")
    pd.testing.assert_frame_equal(rolling, original)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_successful_component_share_must_be_present_and_finite(rolling, bad):
    rolling.loc[0, "component_share"] = bad
    with pytest.raises(ValueError, match="component_share"):
        validate_frame(rolling, "D6")


@pytest.mark.parametrize("column", ["is_lower", "is_mid", "is_upper", "ils"])
@pytest.mark.parametrize("bad", [-0.2, 1.2])
def test_signed_cs_exception_does_not_relax_is_or_ils(rolling, column, bad):
    rolling.loc[0, column] = bad
    with pytest.raises(ValueError, match="shares"):
        validate_frame(rolling, "D6")


def test_failed_window_may_keep_missing_estimates(rolling):
    rolling["status"] = "failed"
    rolling["reason"] = "model_not_estimable"
    rolling["leader"] = None
    rolling[["is_lower", "is_mid", "is_upper", "ils", "component_share"]] = np.nan
    validate_frame(rolling, "D6")
