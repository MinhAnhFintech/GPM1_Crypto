"""TV6: directed same-market_type venue pairs and their gross/net gaps per ts_available.

A pair (buy_venue, sell_venue) buys at the ask of buy_venue and sells at the bid of
sell_venue. Only spot-spot and perp-perp pairs exist; a venue is never paired with
itself; both directions are produced. Spot-perp pairs are rejected by construction.
"""
from __future__ import annotations

from itertools import permutations

import pandas as pd
import numpy as np
from pandas.api.types import is_bool_dtype, is_numeric_dtype

from src.utils.schema import VENUE_PATTERN, data_kind
from src.utils.time_utils import require_utc
from .costs import BPS_PER_UNIT, DEFAULT_FEE_TYPE, FeeSchedule, gross_gap_bps, net_gap_bps, _finite_nonnegative

MODES = ("auto", "quotes", "close")
GAP_COLUMNS = ["ts_available", "buy_exchange", "sell_exchange", "market_type", "buy_venue", "sell_venue",
               "ask_buy", "bid_sell", "fee_total_bps", "gross_gap_bps", "net_gap_bps"]


def split_venue(venue: str) -> tuple[str, str]:
    """Split a venue ID {exchange}_{market_type} into (exchange, market_type)."""
    if not isinstance(venue, str) or not VENUE_PATTERN.fullmatch(venue):
        raise ValueError(f"Invalid venue {venue!r}; expected {{exchange}}_{{spot|perp}}")
    exchange, market_type = venue.rsplit("_", 1)
    return exchange, market_type


def generate_pairs(venues) -> list[tuple[str, str]]:
    """All directed (buy_venue, sell_venue) pairs with the same market_type and distinct venues.

    Both directions are returned, e.g. (binance_spot, okx_spot) and (okx_spot, binance_spot).
    Duplicate venues in the input are ignored; order follows first appearance.
    """
    unique = list(dict.fromkeys(venues))
    market = {venue: split_venue(venue)[1] for venue in unique}
    return [(buy, sell) for buy, sell in permutations(unique, 2) if market[buy] == market[sell]]


def _venues_with(panel: pd.DataFrame, prefix: str) -> list[str]:
    return [str(c)[len(prefix):] for c in panel.columns if str(c).startswith(prefix)]


def _resolve_mode(panel: pd.DataFrame, venues, mode: str) -> tuple[str, list[str]]:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    quote_venues = list(dict.fromkeys(_venues_with(panel, "ask__") + _venues_with(panel, "bid__")))
    if mode == "auto":
        mode = "quotes" if quote_venues else "close"
    if venues is None:
        venues = quote_venues if mode == "quotes" else _venues_with(panel, "close__")
    venues = list(dict.fromkeys(venues))
    needed = [f"{m}__{v}" for v in venues for m in (("bid", "ask") if mode == "quotes" else ("close",))]
    missing = [c for c in needed if c not in panel.columns]
    if missing:
        raise KeyError(f"Panel lacks columns for mode={mode!r}: {missing}")
    for venue in venues:
        split_venue(venue)
    return mode, venues


def _validate_prices(frame, venues, mode):
    metrics = ("bid", "ask") if mode == "quotes" else ("close",)
    for venue in venues:
        for metric in metrics:
            column = f"{metric}__{venue}"
            prices = frame[column]
            if not is_numeric_dtype(prices) or is_bool_dtype(prices):
                raise ValueError(f"{column}: prices must be numeric")
            if np.isinf(prices.to_numpy(dtype=float, na_value=np.nan)).any() or (prices <= 0).any():
                raise ValueError(f"{column}: prices must be finite and positive where present")
        if mode == "quotes" and (frame[f"bid__{venue}"] > frame[f"ask__{venue}"]).any():
            raise ValueError(f"{venue}: crossed book (bid > ask)")
        missing = f"missing__{venue}"
        if missing in frame:
            if not is_bool_dtype(frame[missing]) or frame[missing].isna().any():
                raise ValueError(f"{missing}: must be boolean")
            frame.loc[frame[missing], [f"{metric}__{venue}" for metric in metrics]] = np.nan


def compute_gaps(panel: pd.DataFrame, fees: FeeSchedule, venues=None, mode: str = "auto",
                 fee_type: str = DEFAULT_FEE_TYPE, slippage_bps: float | None = None,
                 extra_cost_bps: float | None = None, assumed_half_spread_bps: float | None = None) -> pd.DataFrame:
    """Long table of gross/net gaps for every same-market_type directed pair at each ts_available.

    panel: wide table indexed by (or with a column) ts_available, UTC. Columns
      quotes mode: bid__{venue}, ask__{venue} (real top of book, USDT) - no spread added;
      close mode:  close__{venue} (USDT); legs become ask_buy = close*(1+h/1e4) and
                   bid_sell = close*(1-h/1e4), h = assumed_half_spread_bps, once per leg.
      auto picks quotes if any venue has bid/ask columns, else close. Incomplete books
      raise; missing__ flags suppress unavailable prices. Funding never enters net_gap_bps.
    fees: FeeSchedule from load_fees; fee_type taker (default) or maker. Unset slippage /
      extra cost come from fees defaults; unset half spread in close mode raises.
    Units: prices USDT, costs and gaps bps one-way. Time: each row uses only quotes
      at that ts_available (no shift, no fill); NaN prices give NaN gaps.
    Returns columns GAP_COLUMNS; buy_exchange/sell_exchange hold exchange IDs and
      market_type the shared contract type. attrs data_kind is copied from the panel.
    """
    if not panel.columns.is_unique:
        raise ValueError("Panel columns must be unique")
    kind = data_kind(panel)
    if "data_kind" in panel.attrs and panel.attrs["data_kind"] != kind:
        raise ValueError("data_kind: column and metadata disagree")
    if "ts_available" in panel.columns and panel.index.name == "ts_available":
        raise ValueError("Ambiguous ts_available: use either index or column")
    frame = panel.reset_index() if "ts_available" not in panel.columns else panel.copy()
    if "ts_available" not in frame.columns:
        raise KeyError("Panel needs ts_available as index or column")
    require_utc(frame["ts_available"], "ts_available")
    if not frame.ts_available.is_unique or not frame.ts_available.is_monotonic_increasing:
        raise ValueError("ts_available must be sorted and unique")
    mode, venues = _resolve_mode(frame, venues, mode)
    _validate_prices(frame, venues, mode)
    slippage = _finite_nonnegative(fees.slippage_bps if slippage_bps is None else slippage_bps, "slippage_bps")
    extra_cost = _finite_nonnegative(fees.extra_cost_bps if extra_cost_bps is None else extra_cost_bps, "extra_cost_bps")
    if mode == "close":
        half_spread = fees.assumed_half_spread_bps if assumed_half_spread_bps is None else assumed_half_spread_bps
        if half_spread is None:
            raise ValueError("Close-only mode needs assumed_half_spread_bps (fees.yaml defaults or argument), from the live sample")
        half_spread = _finite_nonnegative(half_spread, "assumed_half_spread_bps")
        if half_spread >= BPS_PER_UNIT:
            raise ValueError("assumed_half_spread_bps must be < 10000")
    fee = {venue: fees.fee_bps(venue, fee_type) for venue in venues}
    blocks = []
    for buy, sell in generate_pairs(venues):
        if mode == "quotes":
            ask_buy = frame[f"ask__{buy}"].astype(float)
            bid_sell = frame[f"bid__{sell}"].astype(float)
        else:
            ask_buy = frame[f"close__{buy}"].astype(float) * (1 + half_spread / BPS_PER_UNIT)
            bid_sell = frame[f"close__{sell}"].astype(float) * (1 - half_spread / BPS_PER_UNIT)
        gross = gross_gap_bps(bid_sell, ask_buy)
        (buy_exchange, market_type), (sell_exchange, _) = split_venue(buy), split_venue(sell)
        blocks.append(pd.DataFrame({
            "ts_available": frame["ts_available"].to_numpy(), "buy_exchange": buy_exchange, "sell_exchange": sell_exchange,
            "market_type": market_type, "buy_venue": buy, "sell_venue": sell,
            "ask_buy": ask_buy.to_numpy(dtype=float), "bid_sell": bid_sell.to_numpy(dtype=float),
            "fee_total_bps": fee[buy] + fee[sell], "gross_gap_bps": gross.to_numpy(dtype=float),
            "net_gap_bps": net_gap_bps(gross, fee[buy], fee[sell], slippage, extra_cost).to_numpy(dtype=float),
        }))
    if blocks:
        result = pd.concat(blocks, ignore_index=True).sort_values(["ts_available", "buy_venue", "sell_venue"], kind="stable")
        result = result.reset_index(drop=True)
    else:
        dtypes = {"ts_available": frame["ts_available"].dtype} | {c: object for c in GAP_COLUMNS[1:6]}
        result = pd.DataFrame({c: pd.Series(dtype=dtypes.get(c, float)) for c in GAP_COLUMNS})
    result.attrs["data_kind"] = kind
    return result[GAP_COLUMNS]
