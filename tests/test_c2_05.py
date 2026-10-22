import numpy as np
import pandas as pd
import pytest

from notebooks.tv2.plot_c2_05 import dispersion_bps, plot_dispersion_bps
from notebooks.templates.plot_template import plot_prices


@pytest.fixture
def panel():
    index = pd.date_range("2026-01-01", periods=3, freq="min", tz="UTC").as_unit("ns")
    index = index.rename("ts_available")
    values = {
        "binance_spot": [100.0, 101.0, 102.0],
        "okx_spot": [102.0, 103.0, 104.0],
        "binance_perp": [200.0, 202.0, 204.0],
        "okx_perp": [204.0, 206.0, 208.0],
    }
    frame = pd.DataFrame(index=index)
    for venue, prices in values.items():
        close = pd.Series(prices, index=index)
        frame[f"close__{venue}"] = close
        frame[f"log_close__{venue}"] = np.log(close)
        frame[f"volume_base__{venue}"] = 1.0
        frame[f"missing__{venue}"] = False
    frame.attrs.update(data_kind="synthetic", contract_version="1.0")
    return frame


def test_dispersion_uses_dashboard_formula_separately_by_market(panel):
    spot = dispersion_bps(panel, "spot")
    perp = dispersion_bps(panel, "perp")

    assert spot.iloc[0] == pytest.approx((102 - 100) / 101 * 10_000)
    assert perp.iloc[0] == pytest.approx((204 - 200) / 202 * 10_000)
    assert spot.name == "dispersion_bps_spot"
    assert perp.name == "dispersion_bps_perp"


def test_dispersion_keeps_missing_when_any_venue_price_is_missing(panel):
    timestamp = panel.index[1]
    panel.loc[timestamp, ["close__okx_spot", "log_close__okx_spot", "volume_base__okx_spot"]] = np.nan
    panel.loc[timestamp, "missing__okx_spot"] = True

    spot = dispersion_bps(panel, "spot")

    assert np.isnan(spot.loc[timestamp])
    assert np.isfinite(dispersion_bps(panel, "perp").loc[timestamp])


def test_dispersion_rejects_mixed_or_unknown_market_type(panel):
    with pytest.raises(ValueError, match="market_type"):
        dispersion_bps(panel, "future")


def test_f2_plot_is_created(panel, tmp_path):
    output = plot_dispersion_bps(panel, tmp_path / "F2_price_dispersion.png")

    assert output.is_file()
    assert output.stat().st_size > 0


def test_f1_plot_is_created(panel, tmp_path):
    output = plot_prices(panel, tmp_path / "F1_synchronized_prices.png")

    assert output.is_file()
    assert output.stat().st_size > 0
