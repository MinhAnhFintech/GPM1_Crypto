"""C6: fee/gap cost model. Hand-computed numbers, no real data or network."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.cost_model.costs import FeeSchedule, gross_gap_bps, load_fees, net_gap_bps
from src.cost_model.pairs import compute_gaps, generate_pairs

ROOT = Path(__file__).resolve().parents[1]
VENUES = ["binance_spot", "okx_spot", "bybit_spot", "binance_perp", "okx_perp"]
FEES_YAML = """
binance:
  spot: {taker_bps: 10.0, maker_bps: 8.0}
  perp: {taker_bps: 5.0, maker_bps: 2.0}
okx:
  spot: {taker_bps: 8.0}
  perp: {taker_bps: 5.0}
bybit:
  spot: {taker_bps: 10.0}
defaults:
  slippage_bps: 0.0
  extra_cost_bps: 0.0
  assumed_half_spread_bps: null
"""


@pytest.fixture
def fees(tmp_path):
    path = tmp_path/"fees.yaml"
    path.write_text(FEES_YAML, encoding="utf-8")
    return load_fees(path)


def quote_panel(quotes: dict, n=2) -> pd.DataFrame:
    """Wide bid/ask panel: quotes = {venue: (bid, ask)}, same values on n UTC minutes."""
    index = pd.date_range("2026-01-01T00:01Z", periods=n, freq="1min", name="ts_available").as_unit("ns")
    columns = {f"{side}__{venue}": float(px) for venue, (bid, ask) in quotes.items() for side, px in (("bid", bid), ("ask", ask))}
    panel = pd.DataFrame(columns, index=index)
    panel.attrs["data_kind"] = "synthetic"
    return panel


def row(gaps, buy, sell):
    selected = gaps[(gaps.buy_venue == buy) & (gaps.sell_venue == sell)]
    assert not selected.empty, (buy, sell)
    return selected.iloc[0]


# --- Four required cases ---------------------------------------------------------

def test_case1_gross_positive_but_net_negative():
    gross = gross_gap_bps(100.10, 100.00)
    net = net_gap_bps(gross, fee_buy_bps=8.0, fee_sell_bps=8.0)
    assert gross == pytest.approx(10.0)
    assert net == pytest.approx(-6.0)
    assert gross > 0 and net < 0


def test_case2_spread_not_counted_twice_with_real_quotes(fees):
    # Buy okx_spot at ask 100.00, sell binance_spot at bid 100.30: spreads are inside bid/ask.
    panel = quote_panel({"binance_spot": (100.30, 100.40), "okx_spot": (99.90, 100.00)})
    gap = row(compute_gaps(panel, fees, slippage_bps=1.0, extra_cost_bps=0.5), "okx_spot", "binance_spot")
    hand_gross = (100.30 - 100.00) / 100.00 * 10_000          # 30 bps
    hand_net = hand_gross - (8.0 + 10.0) - 1.0 - 0.5          # 10.5 bps, no spread term
    assert gap.gross_gap_bps == pytest.approx(hand_gross)
    assert gap.net_gap_bps == pytest.approx(hand_net)
    assert (gap.ask_buy, gap.bid_sell) == (100.00, 100.30)


def test_case2_close_mode_applies_half_spread_once_per_leg(fees):
    panel = quote_panel({}).assign(close__binance_spot=100.30, close__okx_spot=100.00)
    h = 2.0
    gap = row(compute_gaps(panel, fees, assumed_half_spread_bps=h), "okx_spot", "binance_spot")
    ask_buy, bid_sell = 100.00 * (1 + h / 10_000), 100.30 * (1 - h / 10_000)
    assert gap.ask_buy == pytest.approx(ask_buy)
    assert gap.bid_sell == pytest.approx(bid_sell)
    assert gap.gross_gap_bps == pytest.approx((bid_sell - ask_buy) / ask_buy * 10_000)
    assert gap.net_gap_bps == pytest.approx(gap.gross_gap_bps - 18.0)
    # Roughly 30 - 2h bps gross: one half spread per leg, not a full spread per leg.
    assert gap.gross_gap_bps == pytest.approx(30.0 - 2 * h, abs=0.1)


def test_case3_fees_charged_on_both_legs_and_symmetric():
    gross = 30.0
    net = net_gap_bps(gross, fee_buy_bps=5.0, fee_sell_bps=10.0)
    assert net == pytest.approx(gross - 15.0)
    assert net_gap_bps(gross, fee_buy_bps=10.0, fee_sell_bps=5.0) == pytest.approx(net)


def test_case3_compute_gaps_adds_both_venue_fees(fees):
    panel = quote_panel({"binance_spot": (100.0, 100.0), "okx_spot": (100.0, 100.0)})
    gaps = compute_gaps(panel, fees)
    assert set(gaps.fee_total_bps) == {18.0}                    # 10 (binance) + 8 (okx)
    assert gaps.net_gap_bps.to_numpy() == pytest.approx(np.full(len(gaps), -18.0))


def test_case4_no_spot_perp_pairs(fees):
    pairs = generate_pairs(VENUES)
    market = {v: v.rsplit("_", 1)[1] for v in VENUES}
    assert all(market[buy] == market[sell] for buy, sell in pairs)
    for forbidden in [("binance_spot", "binance_perp"), ("binance_perp", "binance_spot"), ("okx_spot", "binance_perp")]:
        assert forbidden not in pairs
    assert generate_pairs(["binance_spot", "binance_perp"]) == []

    panel = quote_panel({v: (100.0, 100.1) for v in VENUES})
    gaps = compute_gaps(panel, fees)
    buy_market = gaps.buy_venue.str.rsplit("_", n=1).str[1]
    sell_market = gaps.sell_venue.str.rsplit("_", n=1).str[1]
    assert (buy_market == sell_market).all() and (buy_market == gaps.market_type).all()
    assert set(gaps.market_type) == {"spot", "perp"}


# --- Additional cases ----------------------------------------------------------

def test_slippage_and_extra_cost_subtracted():
    assert net_gap_bps(20.0, 5.0, 5.0, slippage_bps=2.0, extra_cost_bps=3.0) == pytest.approx(5.0)
    assert net_gap_bps(20.0, 5.0, 5.0) == pytest.approx(10.0)


def test_both_directions_distinct_and_no_self_pair(fees):
    pairs = generate_pairs(VENUES)
    assert len(pairs) == 3 * 2 + 2 * 1                           # spot 3P2 + perp 2P2
    assert all(buy != sell for buy, sell in pairs)
    assert generate_pairs(["okx_spot", "okx_spot"]) == []
    panel = quote_panel({"binance_spot": (100.30, 100.40), "okx_spot": (99.90, 100.00)})
    gaps = compute_gaps(panel, fees)
    forward, backward = row(gaps, "okx_spot", "binance_spot"), row(gaps, "binance_spot", "okx_spot")
    assert forward.gross_gap_bps == pytest.approx(30.0)
    assert backward.gross_gap_bps == pytest.approx((99.90 - 100.40) / 100.40 * 10_000)
    assert forward.gross_gap_bps > 0 > backward.gross_gap_bps
    assert (forward.buy_exchange, forward.sell_exchange) == ("okx", "binance")


def test_missing_venue_fee_raises(fees):
    with pytest.raises(KeyError, match="kraken_spot"):
        fees.fee_bps("kraken_spot")
    panel = quote_panel({"binance_spot": (100.0, 100.1), "kraken_spot": (100.0, 100.1)})
    with pytest.raises(KeyError, match="kraken_spot"):
        compute_gaps(panel, fees)
    with pytest.raises(KeyError, match="maker_bps"):
        fees.fee_bps("okx_spot", "maker")


@pytest.mark.parametrize("bad", ["binance: {spot: {maker_bps: 2}}", "binance: {spot: {taker_bps: -1}}",
                                 "binance: {spot: {taker_bps: '10'}}", "Binance: {spot: {taker_bps: 10}}",
                                 "binance: {swap: {taker_bps: 10}}"])
def test_invalid_fees_yaml_rejected(tmp_path, bad):
    path = tmp_path/"fees.yaml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError):
        load_fees(path)


def test_funding_rate_column_does_not_change_net(fees):
    panel = quote_panel({"binance_perp": (100.20, 100.30), "okx_perp": (99.90, 100.00)})
    base = compute_gaps(panel, fees)
    with_funding = compute_gaps(panel.assign(funding_rate=0.0001, funding_rate__binance_perp=0.01), fees)
    pd.testing.assert_frame_equal(base, with_funding)


def test_close_mode_requires_half_spread(fees):
    panel = quote_panel({}).assign(close__binance_spot=100.0, close__okx_spot=100.0)
    with pytest.raises(ValueError, match="assumed_half_spread_bps"):
        compute_gaps(panel, fees)
    configured = FeeSchedule(venues=fees.venues, assumed_half_spread_bps=1.5)
    assert compute_gaps(panel, configured).gross_gap_bps.to_numpy() == pytest.approx(
        gross_gap_bps(100.0 * (1 - 1.5e-4), 100.0 * (1 + 1.5e-4)))


def test_nan_price_gives_nan_not_error(fees):
    assert np.isnan(gross_gap_bps(np.nan, 100.0))
    assert np.isnan(net_gap_bps(np.nan, 5.0, 5.0))
    panel = quote_panel({"binance_spot": (100.30, 100.40), "okx_spot": (99.90, 100.00)})
    panel.iloc[0, panel.columns.get_loc("ask__okx_spot")] = np.nan
    gaps = compute_gaps(panel, fees)
    first = gaps[gaps.ts_available == panel.index[0]]
    assert np.isnan(row(first, "okx_spot", "binance_spot").net_gap_bps)
    assert not np.isnan(row(first, "binance_spot", "okx_spot").net_gap_bps)
    assert gaps[gaps.ts_available == panel.index[1]].net_gap_bps.notna().all()


def test_vectorised_inputs_and_time_preserved(fees):
    bids, asks = pd.Series([100.1, 99.9]), pd.Series([100.0, 100.0])
    assert gross_gap_bps(bids, asks).to_numpy() == pytest.approx([10.0, -10.0])
    assert gross_gap_bps(np.array([100.1]), np.array([100.0])) == pytest.approx([10.0])
    panel = quote_panel({"binance_spot": (100.0, 100.1), "okx_spot": (100.0, 100.1)}, n=3)
    gaps = compute_gaps(panel, fees)
    assert str(gaps.ts_available.dtype) == "datetime64[ns, UTC]"
    assert set(gaps.ts_available) == set(panel.index)
    assert gaps.attrs["data_kind"] == "synthetic"


def test_repo_fees_yaml_loads():
    schedule = load_fees(ROOT/"config/fees.yaml")
    for venue in ["binance_spot", "binance_perp", "okx_spot", "okx_perp", "bybit_spot"]:
        assert schedule.fee_bps(venue) > 0
