"""TV4 review of C6.01/C6.02: bad inputs must not create usable opportunities."""
import numpy as np
import pandas as pd
import pytest

from src.cost_model.costs import FeeSchedule, gross_gap_bps, load_fees, net_gap_bps
from src.cost_model.pairs import compute_gaps
from src.utils.synthetic import make_fixture


@pytest.fixture
def fees():
    return FeeSchedule(venues={v: {"taker_bps": 10.0} for v in ("binance_spot", "okx_spot")})


@pytest.fixture
def panel():
    frame = pd.DataFrame({
        "bid__binance_spot": [100.3, 100.4, 100.5],
        "ask__binance_spot": [100.4, 100.5, 100.6],
        "bid__okx_spot": [99.9, 100.0, 100.1],
        "ask__okx_spot": [100.0, 100.1, 100.2],
    }, index=pd.date_range("2026-01-01T00:00Z", periods=3, freq="1min", name="ts_available").as_unit("ns"))
    frame.attrs["data_kind"] = "synthetic"
    return frame


@pytest.mark.parametrize("bad_time", ["naive", "non_utc", "wrong_unit", "nat", "duplicate", "reversed"])
def test_gap_panel_rejects_invalid_availability(panel, fees, bad_time):
    if bad_time == "naive":
        panel.index = panel.index.tz_localize(None)
    elif bad_time == "non_utc":
        panel.index = panel.index.tz_convert("Asia/Ho_Chi_Minh")
    elif bad_time == "wrong_unit":
        panel.index = panel.index.as_unit("us")
    elif bad_time == "nat":
        panel.index = pd.DatetimeIndex([pd.NaT, *panel.index[1:]], name="ts_available").as_unit("ns")
    elif bad_time == "duplicate":
        panel.index = pd.DatetimeIndex([panel.index[0], *panel.index[:2]], name="ts_available")
    else:
        panel = panel.iloc[::-1]
    with pytest.raises(ValueError, match="ts_available"):
        compute_gaps(panel, fees)


@pytest.mark.parametrize("kind", [None, "mixed_column", "conflicting_metadata"])
def test_gap_panel_requires_one_consistent_provenance(panel, fees, kind):
    if kind is None:
        panel.attrs.clear()
    elif kind == "mixed_column":
        panel["data_kind"] = ["synthetic", "live", "synthetic"]
    else:
        panel["data_kind"] = "historical"
    with pytest.raises(ValueError, match="data_kind"):
        compute_gaps(panel, fees)


def test_column_provenance_is_preserved_and_input_is_not_mutated(panel, fees):
    panel.attrs.clear()
    panel["data_kind"] = "live"
    panel["missing__binance_spot"] = [True, False, False]
    original = panel.copy(deep=True)
    gaps = compute_gaps(panel.reset_index(), fees)
    assert gaps.attrs["data_kind"] == "live"
    assert gaps.loc[gaps.ts_available.eq(panel.index[0]), "net_gap_bps"].isna().all()
    assert gaps.loc[gaps.ts_available.gt(panel.index[0]), "net_gap_bps"].notna().all()
    pd.testing.assert_frame_equal(panel, original)


@pytest.mark.parametrize("bad", [-1.0, np.inf, np.nan, True, "2"])
@pytest.mark.parametrize("argument", ["slippage_bps", "extra_cost_bps", "assumed_half_spread_bps"])
def test_gap_cost_overrides_cannot_inflate_profit(panel, fees, argument, bad):
    kwargs = {argument: bad}
    if argument == "assumed_half_spread_bps":
        panel = panel.rename(columns=lambda c: c.replace("ask__", "close__")).filter(like="close__")
    with pytest.raises(ValueError, match=argument):
        compute_gaps(panel, fees, **kwargs)


@pytest.mark.parametrize("bad", [0.0, -1.0, np.inf, True, "100"])
def test_invalid_prices_do_not_generate_opportunities(bad):
    with pytest.raises(ValueError):
        gross_gap_bps(101.0, bad)
    with pytest.raises(ValueError):
        gross_gap_bps(bad, 100.0)


@pytest.mark.parametrize("argument", ["fee_buy_bps", "fee_sell_bps", "slippage_bps", "extra_cost_bps"])
def test_direct_net_costs_cannot_be_negative(argument):
    kwargs = {"fee_buy_bps": 10.0, "fee_sell_bps": 10.0, argument: -10.0}
    with pytest.raises(ValueError, match=argument):
        net_gap_bps(5.0, **kwargs)


def test_crossed_or_incomplete_book_is_rejected(panel, fees):
    with pytest.raises(ValueError, match="crossed book"):
        compute_gaps(panel.assign(bid__binance_spot=120.0), fees)
    with pytest.raises(KeyError, match="bid__okx_spot"):
        compute_gaps(panel.drop(columns="bid__okx_spot"), fees)


def test_nullable_missing_price_stays_missing(panel, fees):
    panel["ask__okx_spot"] = pd.Series([pd.NA, 100.1, 100.2], index=panel.index, dtype="Float64")
    gaps = compute_gaps(panel, fees)
    first = gaps[gaps.ts_available.eq(panel.index[0])]
    assert first.loc[first.buy_venue.eq("okx_spot"), "net_gap_bps"].isna().all()
    assert first.loc[first.buy_venue.eq("binance_spot"), "net_gap_bps"].notna().all()


def test_future_quotes_do_not_change_past_gaps(panel, fees):
    expected = compute_gaps(panel.iloc[:2], fees)
    actual = compute_gaps(panel, fees)
    actual = actual[actual.ts_available.le(panel.index[1])].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("yaml", ["binance: {spot: {taker_bps: null}}", "defaults: false\nbinance: {spot: {taker_bps: 10}}"])
def test_missing_taker_or_malformed_defaults_cannot_load(tmp_path, yaml):
    path = tmp_path / "fees.yaml"
    path.write_text(yaml, encoding="utf-8")
    with pytest.raises(ValueError):
        load_fees(path)


def test_unverified_repo_maker_fees_are_unavailable():
    schedule = load_fees("config/fees.yaml")
    for venue in schedule.venues:
        assert schedule.fee_bps(venue) > 0
        with pytest.raises(KeyError, match="maker_bps"):
            schedule.fee_bps(venue, "maker")


def test_direct_schedule_and_proxy_spread_are_validated(panel, fees):
    with pytest.raises(ValueError, match="taker_bps"):
        FeeSchedule(venues={"binance_spot": {"taker_bps": -1}})
    closes = panel.rename(columns=lambda c: c.replace("ask__", "close__")).filter(like="close__")
    with pytest.raises(ValueError, match="assumed_half_spread_bps"):
        compute_gaps(closes, fees, assumed_half_spread_bps=10_000)


def test_d3_fixture_handoff_keeps_market_types_and_missing(cfg):
    _, _, panel, _ = make_fixture(cfg, "multi")
    venues = [c.removeprefix("close__") for c in panel if c.startswith("close__")]
    fees = FeeSchedule(venues={v: {"taker_bps": 10.0} for v in venues})
    original = panel.copy(deep=True)
    gaps = compute_gaps(panel, fees, assumed_half_spread_bps=2.0)
    assert gaps.attrs["data_kind"] == "synthetic"
    assert (gaps.buy_venue.str.rsplit("_", n=1).str[1] == gaps.market_type).all()
    assert (gaps.sell_venue.str.rsplit("_", n=1).str[1] == gaps.market_type).all()
    assert (gaps.buy_exchange != gaps.sell_exchange).all()
    for venue in venues:
        times = panel.index[panel[f"missing__{venue}"]]
        unavailable = gaps.ts_available.isin(times) & (gaps.buy_venue.eq(venue) | gaps.sell_venue.eq(venue))
        assert gaps.loc[unavailable, "net_gap_bps"].isna().all()
    pd.testing.assert_frame_equal(panel, original)
