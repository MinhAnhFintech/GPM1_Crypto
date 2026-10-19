"""TV6: gross/net cross-venue gap in bps and the fee schedule from config/fees.yaml.

Single definition (DATA_CONTRACT 2.5), all costs in bps one-way per leg:
    gross_gap_bps = (bid_sell - ask_buy) / ask_buy * 10_000
    fee_total_bps = fee_buy_bps + fee_sell_bps
    net_gap_bps   = gross_gap_bps - fee_total_bps - slippage_bps - extra_cost_bps
Real bid/ask already contain the spread, so no spread term exists here. Latency is
simulated by execution time in the backtest, and funding_rate never enters net_gap_bps.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.utils.config import load_config
from src.utils.schema import VENUE_PATTERN

BPS_PER_UNIT = 10_000.0
FEE_TYPES = ("taker", "maker")
DEFAULT_FEE_TYPE = "taker"
DEFAULTS_KEY = "defaults"
MARKET_TYPES = ("spot", "perp")


def _scalar_or_array(value):
    """Return a Python float for 0-d results, otherwise the array/Series unchanged."""
    return float(value) if np.ndim(value) == 0 else value


def _numeric_values(value, name: str, *, positive=False, nullable=False):
    """Check numeric operands without changing Series alignment or filling gaps."""
    values = np.asarray(value)
    if values.dtype.kind not in "iuf":
        raise ValueError(f"{name} must contain numeric values, not booleans or strings")
    if np.isinf(values).any() or (not nullable and np.isnan(values).any()):
        raise ValueError(f"{name} must contain finite values")
    if positive and (values <= 0).any():
        raise ValueError(f"{name} must be positive where present")
    if not positive and not nullable and (values < 0).any():
        raise ValueError(f"{name} must be nonnegative")


def gross_gap_bps(sell_bid, buy_ask) -> float:
    """Gross gap of buying at buy_ask and selling at sell_bid, in bps of buy_ask.

    Units: prices in USDT (same quote currency on both legs); result in bps.
    Time: both prices must be known at the same ts_available; no later quote is used.
    Accepts scalars, numpy arrays or pandas Series (Series align on index). NaN
    prices give NaN, never an error or a filled value.
    """
    _numeric_values(sell_bid, "sell_bid", positive=True, nullable=True)
    _numeric_values(buy_ask, "buy_ask", positive=True, nullable=True)
    return _scalar_or_array((sell_bid - buy_ask) / buy_ask * BPS_PER_UNIT)


def net_gap_bps(gross_bps, fee_buy_bps, fee_sell_bps, slippage_bps=0.0, extra_cost_bps=0.0) -> float:
    """Net gap after costs: gross_bps - (fee_buy_bps + fee_sell_bps) - slippage_bps - extra_cost_bps.

    Units: every argument and the result are bps one-way. Fees are charged once per
    leg; spread is not subtracted (already in bid/ask, or in the close proxy). Funding
    and latency are not costs here. extra_cost_bps = transfer/rebalancing, default 0.
    Time: pure arithmetic on values known at the decision time.
    """
    _numeric_values(gross_bps, "gross_bps", nullable=True)
    for name, value in (("fee_buy_bps", fee_buy_bps), ("fee_sell_bps", fee_sell_bps),
                        ("slippage_bps", slippage_bps), ("extra_cost_bps", extra_cost_bps)):
        _numeric_values(value, name)
    fee_total_bps = fee_buy_bps + fee_sell_bps
    return _scalar_or_array(gross_bps - fee_total_bps - slippage_bps - extra_cost_bps)


def _finite_nonnegative(value, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{where} must be a finite number >= 0 (bps), got {value!r}")
    return float(value)


@dataclass(frozen=True)
class FeeSchedule:
    """Per-venue one-way fees in bps plus cost-model defaults from fees.yaml.

    venues maps venue ID ({exchange}_{market_type}) to {"taker_bps": .., "maker_bps": ..}.
    assumed_half_spread_bps is None until set from the live sample (close-only mode
    then refuses to run rather than assume 0).
    """

    venues: dict = field(default_factory=dict)
    slippage_bps: float = 0.0
    extra_cost_bps: float = 0.0
    assumed_half_spread_bps: float | None = None

    def __post_init__(self):
        for venue, spec in self.venues.items():
            if not isinstance(venue, str) or not VENUE_PATTERN.fullmatch(venue):
                raise ValueError(f"Invalid fee venue {venue!r}")
            if not isinstance(spec, dict) or "taker_bps" not in spec:
                raise ValueError(f"{venue} needs taker_bps")
            _finite_nonnegative(spec["taker_bps"], f"{venue}.taker_bps")
            for fee_type in FEE_TYPES:
                value = spec.get(f"{fee_type}_bps")
                if value is not None:
                    _finite_nonnegative(value, f"{venue}.{fee_type}_bps")
        _finite_nonnegative(self.slippage_bps, "slippage_bps")
        _finite_nonnegative(self.extra_cost_bps, "extra_cost_bps")
        if self.assumed_half_spread_bps is not None:
            half_spread = _finite_nonnegative(self.assumed_half_spread_bps, "assumed_half_spread_bps")
            if half_spread >= BPS_PER_UNIT:
                raise ValueError("assumed_half_spread_bps must be < 10000 to keep sell prices positive")

    def fee_bps(self, venue: str, fee_type: str = DEFAULT_FEE_TYPE) -> float:
        """One-way fee of one leg on venue, in bps; KeyError if venue/fee is not configured."""
        if fee_type not in FEE_TYPES:
            raise ValueError(f"fee_type must be one of {FEE_TYPES}, got {fee_type!r}")
        if venue not in self.venues:
            raise KeyError(f"Venue {venue!r} has no fee in fees.yaml (configured: {sorted(self.venues)}); add it instead of assuming 0")
        key = f"{fee_type}_bps"
        if self.venues[venue].get(key) is None:
            raise KeyError(f"Venue {venue!r} has no {key} in fees.yaml")
        return _finite_nonnegative(self.venues[venue][key], f"{venue}.{key}")


def load_fees(path) -> FeeSchedule:
    """Read fees.yaml ({exchange}: {market_type}: {taker_bps, maker_bps?, ...}, plus defaults).

    Units: bps one-way per leg. Exchange IDs must be lowercase CCXT-style and
    market_type spot/perp; taker_bps is required, maker_bps optional. Invalid or
    missing values raise ValueError; nothing silently becomes a zero fee.
    """
    raw = load_config(Path(path))
    defaults = raw.get(DEFAULTS_KEY, {})
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise ValueError(f"fees.yaml: {DEFAULTS_KEY} must be a mapping")
    venues = {}
    for exchange, markets in raw.items():
        if exchange == DEFAULTS_KEY:
            continue
        if not isinstance(exchange, str) or not isinstance(markets, dict):
            raise ValueError(f"fees.yaml: {exchange!r} must map market_type to fees")
        for market_type, spec in markets.items():
            venue = f"{exchange}_{market_type}"
            if market_type not in MARKET_TYPES or not VENUE_PATTERN.fullmatch(venue):
                raise ValueError(f"fees.yaml: invalid venue {venue!r}; use lowercase exchange and market_type in {MARKET_TYPES}")
            if not isinstance(spec, dict) or "taker_bps" not in spec:
                raise ValueError(f"fees.yaml: {exchange}.{market_type} needs taker_bps")
            _finite_nonnegative(spec["taker_bps"], f"{exchange}.{market_type}.taker_bps")
            venues[venue] = {f"{t}_bps": _finite_nonnegative(spec[f"{t}_bps"], f"{exchange}.{market_type}.{t}_bps")
                             for t in FEE_TYPES if spec.get(f"{t}_bps") is not None}
    if not venues:
        raise ValueError(f"fees.yaml: no venue fees in {path}")
    half_spread = defaults.get("assumed_half_spread_bps")
    return FeeSchedule(
        venues=venues,
        slippage_bps=_finite_nonnegative(defaults.get("slippage_bps", 0.0), "defaults.slippage_bps"),
        extra_cost_bps=_finite_nonnegative(defaults.get("extra_cost_bps", 0.0), "defaults.extra_cost_bps"),
        assumed_half_spread_bps=None if half_spread is None else _finite_nonnegative(half_spread, "defaults.assumed_half_spread_bps"),
    )
