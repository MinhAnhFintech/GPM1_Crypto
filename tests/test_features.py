import numpy as np
import pandas as pd
import pytest
from src.features.features_1m import build_features_1m
from src.utils.schema import validate_frame


@pytest.fixture
def panel():
    index = pd.date_range("2026-01-01", periods=5, freq="min", tz="UTC").as_unit("ns")
    index = index.rename("ts_available")
    closes = {
        "binance_spot": [100, 101, 102, 103, 104],
        "okx_spot": [102, 103, 104, 105, 106],
        "binance_perp": [200, 202, 204, 206, 208],
        "okx_perp": [204, 206, 208, 210, 212],
    }
    volumes = {
        "binance_spot": [1, 1, 1, 1, 1],
        "okx_spot": [3, 3, 3, 3, 3],
        "binance_perp": [2, 2, 2, 2, 2],
        "okx_perp": [2, 2, 2, 2, 2],
    }
    frame = pd.DataFrame(index=index)
    for venue, values in closes.items():
        frame[f"close__{venue}"] = values
        frame[f"log_close__{venue}"] = np.log(values)
        frame[f"volume_base__{venue}"] = volumes[venue]
        frame[f"missing__{venue}"] = False
    frame.attrs.update(data_kind="synthetic", contract_version="1.0")
    return frame


def test_builds_required_features_and_valid_d3b(panel):
    features = build_features_1m(panel, rv_window=3, rv_min_periods=2)

    for venue in ("binance_spot", "okx_spot", "binance_perp", "okx_perp"):
        assert f"ret_1m__{venue}" in features
        assert f"rv_60m__{venue}" in features
        assert f"vol_share__{venue}" in features
    assert "dispersion_bps_spot" in features
    assert "dispersion_bps_perp" in features
    assert features.index.equals(panel.index)
    assert str(features.index.tz) == "UTC"
    validate_frame(features, "D3b")


def test_volume_shares_sum_to_one_within_each_market_type(panel):
    features = build_features_1m(panel)

    assert np.allclose(
        features[["vol_share__binance_spot", "vol_share__okx_spot"]].sum(axis=1),
        1.0,
    )
    assert np.allclose(
        features[["vol_share__binance_perp", "vol_share__okx_perp"]].sum(axis=1),
        1.0,
    )


def test_returns_and_rolling_volatility_use_past_observations(panel):
    features = build_features_1m(panel, rv_window=3, rv_min_periods=2)
    returns = panel["log_close__binance_spot"].diff()

    assert features["ret_1m__binance_spot"].iloc[1] == pytest.approx(returns.iloc[1])
    assert features["rv_60m__binance_spot"].iloc[2] == pytest.approx(
        returns.rolling(3, min_periods=2).std().iloc[2]
    )


def test_volume_share_stays_missing_if_any_group_volume_is_missing(panel):
    panel.loc[panel.index[0], "volume_base__okx_spot"] = np.nan

    features = build_features_1m(panel)

    assert features.loc[panel.index[0], ["vol_share__binance_spot", "vol_share__okx_spot"]].isna().all()


def test_dispersion_matches_dashboard_formula_and_requires_all_prices(panel):
    features = build_features_1m(panel)
    expected_spot = (102 - 100) / ((102 + 100) / 2) * 10_000
    assert features["dispersion_bps_spot"].iloc[0] == pytest.approx(expected_spot)

    panel.loc[panel.index[0], "close__okx_spot"] = np.nan
    features = build_features_1m(panel)
    assert np.isnan(features["dispersion_bps_spot"].iloc[0])
    assert np.isfinite(features["dispersion_bps_perp"].iloc[0])


def test_features_keep_panel_provenance(panel):
    features = build_features_1m(panel)

    assert features.attrs["data_kind"] == "synthetic"
    assert features.attrs["contract_version"] == "1.0"