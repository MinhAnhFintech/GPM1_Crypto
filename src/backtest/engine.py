"""TV6 C6.04: backtest A, a PROXY of cross-venue arbitrage on historical 1-minute closes.

This is not an executable-price backtest: closes stand in for quotes. Each leg is priced as
    ask_buy  = close_buy  * (1 + h_buy  / 10_000)
    bid_sell = close_sell * (1 - h_sell / 10_000)
with h = assumed half spread per venue calibrated from the live D2 sample (src/backtest/calibration.py),
added exactly once per leg. Gaps then follow the single definition in src/cost_model/costs.py.

Timing (no look-ahead):
  * decision at grid time t uses only closes with ts_available == t (known at t);
  * a signal fires when the expected net_gap_bps at t exceeds entry_threshold_bps;
  * the fill happens L = latency_min minutes later, at the closes with ts_available == t + L.
    Latency is time, not a cost. If either fill close is missing, or t + L is past the panel,
    the signal is rejected with a reason; nothing is interpolated or filled forward.
  * a pair (buy_venue, sell_venue) opens no new signal until its previous fill time has passed.
Capital is pre-positioned on every venue (no transfer, extra_cost_bps from fees.yaml, default 0).
Only same-market_type pairs exist (spot-spot, perp-perp); funding never enters any gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.cost_model.costs import BPS_PER_UNIT, DEFAULT_FEE_TYPE, FeeSchedule, _finite_nonnegative, gross_gap_bps, net_gap_bps
from src.cost_model.pairs import _validate_prices, generate_pairs, split_venue
from src.utils.schema import data_kind, venues_from_panel
from src.utils.time_utils import require_utc

METHOD = "proxy_close_1m"
LEDGER_COLUMNS = ["signal_id", "ts_decision", "ts_fill", "market_type", "buy_exchange", "sell_exchange", "qty_btc",
                  "buy_px", "sell_px", "fees_usdt", "pnl_usdt", "status", "reject_reason",
                  "buy_venue", "sell_venue", "latency_min", "decision_gross_bps", "decision_net_bps", "gross_bps",
                  "net_bps", "fee_total_bps", "other_cost_usdt", "half_spread_buy_bps", "half_spread_sell_bps",
                  "method", "data_kind"]
SUMMARY_COLUMNS = ["market_type", "buy_venue", "sell_venue", "n_signals", "n_filled", "n_rejected", "n_net_positive",
                   "share_net_positive", "mean_net_bps", "median_net_bps", "total_pnl_usdt", "total_fees_usdt"]
FILLED, REJECTED = "filled", "rejected"


@dataclass(frozen=True)
class BacktestConfig:
    """Backtest A settings. Units: bps one-way per leg, minutes, BTC.

    half_spread_bps: {venue: bps} calibrated from live D2; a venue without a value is an error.
    slippage_bps / extra_cost_bps: None -> fees.yaml defaults.
    """

    fees: FeeSchedule
    half_spread_bps: dict
    latency_min: int = 1
    entry_threshold_bps: float = 0.0
    qty_btc: float = 0.01
    fee_type: str = DEFAULT_FEE_TYPE
    slippage_bps: float | None = None
    extra_cost_bps: float | None = None

    def __post_init__(self):
        if isinstance(self.latency_min, bool) or not isinstance(self.latency_min, (int, np.integer)) or self.latency_min < 0:
            raise ValueError("latency_min must be a nonnegative integer number of minutes")
        if not np.isfinite(self.entry_threshold_bps):
            raise ValueError("entry_threshold_bps must be finite")
        if not (np.isfinite(self.qty_btc) and self.qty_btc > 0):
            raise ValueError("qty_btc must be positive")
        for venue, value in self.half_spread_bps.items():
            if _finite_nonnegative(value, f"half_spread_bps[{venue}]") >= BPS_PER_UNIT:
                raise ValueError(f"half_spread_bps[{venue}] must be < 10000")


@dataclass(frozen=True)
class BacktestResult:
    """ledger: one row per signal (D8 columns + audit columns); summary: per pair; params: run settings."""

    ledger: pd.DataFrame
    summary: pd.DataFrame
    params: dict = field(default_factory=dict)


def _leg_prices(frame: pd.DataFrame, venue: str, half_spread: float):
    close = frame[f"close__{venue}"].to_numpy(dtype=float)
    return close * (1 + half_spread / BPS_PER_UNIT), close * (1 - half_spread / BPS_PER_UNIT)


def _summary(ledger: pd.DataFrame, pairs, market) -> pd.DataFrame:
    rows = []
    for buy, sell in pairs:
        part = ledger[(ledger["buy_venue"] == buy) & (ledger["sell_venue"] == sell)]
        filled = part[part["status"] == FILLED]
        rows.append({"market_type": market[buy], "buy_venue": buy, "sell_venue": sell, "n_signals": len(part),
                     "n_filled": len(filled), "n_rejected": int((part["status"] == REJECTED).sum()),
                     "n_net_positive": int((filled["net_bps"] > 0).sum()),
                     "share_net_positive": float((filled["net_bps"] > 0).mean()) if len(filled) else np.nan,
                     "mean_net_bps": float(filled["net_bps"].mean()) if len(filled) else np.nan,
                     "median_net_bps": float(filled["net_bps"].median()) if len(filled) else np.nan,
                     "total_pnl_usdt": float(filled["pnl_usdt"].sum()), "total_fees_usdt": float(filled["fees_usdt"].sum())})
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def run_backtest(panel: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    """Backtest A on a D3-style wide panel of 1-minute closes (one split; pass dev or val, never test).

    panel: index ts_available (datetime64[ns, UTC], unique, increasing), close__{venue} in USDT,
      optional missing__{venue} flags, exactly one data_kind. Other columns are ignored.
    Returns a ledger in D8 columns (qty BTC, prices/fees/PnL USDT, bps one-way) plus audit columns,
      where pnl_usdt = net_bps / 10_000 * qty_btc * buy_px and fees_usdt = fee_total_bps / 10_000 * qty_btc * buy_px.
      Rejected signals have no fill (ts_fill NaT, prices NaN, PnL and fees 0).
    """
    if panel.index.name != "ts_available":
        raise ValueError("panel must be indexed by ts_available")
    times = pd.Series(panel.index, index=panel.index)
    require_utc(times, "ts_available")
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise ValueError("ts_available must be unique and increasing")
    kind = data_kind(panel)
    venues = venues_from_panel(panel)
    pairs = generate_pairs(venues)
    market = {v: split_venue(v)[1] for v in venues}
    missing_h = sorted(v for v in venues if v not in cfg.half_spread_bps)
    if missing_h:
        raise KeyError(f"No calibrated half spread for {missing_h}; calibrate from live D2 instead of assuming 0")
    fee = {v: cfg.fees.fee_bps(v, cfg.fee_type) for v in venues}
    slippage = _finite_nonnegative(cfg.fees.slippage_bps if cfg.slippage_bps is None else cfg.slippage_bps, "slippage_bps")
    extra = _finite_nonnegative(cfg.fees.extra_cost_bps if cfg.extra_cost_bps is None else cfg.extra_cost_bps, "extra_cost_bps")
    frame = panel.copy()
    _validate_prices(frame, venues, "close")                 # missing__ flags -> NaN, bad prices raise
    legs = {v: _leg_prices(frame, v, cfg.half_spread_bps[v]) for v in venues}   # (ask, bid) per minute
    position = pd.Series(np.arange(len(frame)), index=frame.index)
    latency = pd.Timedelta(minutes=int(cfg.latency_min))
    last_time = frame.index[-1]

    records = []
    for buy, sell in pairs:
        ask_buy, bid_sell = legs[buy][0], legs[sell][1]
        decision_gross = gross_gap_bps(bid_sell, ask_buy)
        decision_net = net_gap_bps(decision_gross, fee[buy], fee[sell], slippage, extra)
        candidates = np.flatnonzero(np.nan_to_num(decision_net, nan=-np.inf) > cfg.entry_threshold_bps)
        blocked_until = None
        for i in candidates:
            t = frame.index[i]
            if blocked_until is not None and t <= blocked_until:
                continue
            t_fill = t + latency
            blocked_until = t_fill
            j = position.get(t_fill)
            record = {"ts_decision": t, "buy_venue": buy, "sell_venue": sell,
                      "decision_gross_bps": float(decision_gross[i]), "decision_net_bps": float(decision_net[i])}
            if t_fill > last_time:
                record.update(status=REJECTED, reject_reason="fill_beyond_sample")
            elif j is None or not (np.isfinite(ask_buy[j]) and np.isfinite(bid_sell[j])):
                record.update(status=REJECTED, reject_reason="missing_fill_price")
            else:
                gross = float(gross_gap_bps(bid_sell[j], ask_buy[j]))
                record.update(status=FILLED, reject_reason=None, ts_fill=t_fill, buy_px=float(ask_buy[j]),
                              sell_px=float(bid_sell[j]), gross_bps=gross,
                              net_bps=float(net_gap_bps(gross, fee[buy], fee[sell], slippage, extra)))
            records.append(record)

    ledger = pd.DataFrame.from_records(records, columns=["ts_decision", "buy_venue", "sell_venue", "decision_gross_bps",
                                                         "decision_net_bps", "status", "reject_reason", "ts_fill",
                                                         "buy_px", "sell_px", "gross_bps", "net_bps"])
    ledger["ts_decision"] = ledger["ts_decision"].astype("datetime64[ns, UTC]")
    ledger["ts_fill"] = pd.to_datetime(ledger["ts_fill"], utc=True).astype("datetime64[ns, UTC]")
    for column in ("buy_px", "sell_px", "gross_bps", "net_bps", "decision_gross_bps", "decision_net_bps"):
        ledger[column] = ledger[column].astype("float64")
    ledger["market_type"] = ledger["buy_venue"].map(market)
    ledger["buy_exchange"] = ledger["buy_venue"].map(lambda v: split_venue(v)[0])
    ledger["sell_exchange"] = ledger["sell_venue"].map(lambda v: split_venue(v)[0])
    ledger["qty_btc"] = float(cfg.qty_btc)
    ledger["latency_min"] = int(cfg.latency_min)
    ledger["fee_total_bps"] = (ledger["buy_venue"].map(fee) + ledger["sell_venue"].map(fee)).astype("float64")
    ledger["half_spread_buy_bps"] = ledger["buy_venue"].map(cfg.half_spread_bps).astype("float64")
    ledger["half_spread_sell_bps"] = ledger["sell_venue"].map(cfg.half_spread_bps).astype("float64")
    notional = ledger["qty_btc"] * ledger["buy_px"]
    filled = ledger["status"] == FILLED
    ledger["fees_usdt"] = (ledger["fee_total_bps"] / BPS_PER_UNIT * notional).where(filled, 0.0)
    ledger["other_cost_usdt"] = ((slippage + extra) / BPS_PER_UNIT * notional).where(filled, 0.0)
    ledger["pnl_usdt"] = (ledger["net_bps"] / BPS_PER_UNIT * notional).where(filled, 0.0)
    for column in ("market_type", "buy_exchange", "sell_exchange", "buy_venue", "sell_venue"):
        ledger[column] = ledger[column].astype(str)
    ledger["signal_id"] = ("A_" + ledger["market_type"] + "_" + ledger["buy_venue"] + "_" + ledger["sell_venue"] + "_"
                           + ledger["ts_decision"].dt.strftime("%Y%m%dT%H%M%SZ").astype(str) + f"_L{int(cfg.latency_min)}")
    ledger["method"] = METHOD
    ledger["data_kind"] = kind
    ledger = ledger.sort_values(["ts_decision", "buy_venue", "sell_venue"], kind="stable").reset_index(drop=True)[LEDGER_COLUMNS]
    ledger.attrs.update(data_kind=kind, method=METHOD)
    params = {"method": METHOD, "data_kind": kind, "latency_min": int(cfg.latency_min),
              "entry_threshold_bps": float(cfg.entry_threshold_bps), "qty_btc": float(cfg.qty_btc), "fee_type": cfg.fee_type,
              "fee_bps": fee, "slippage_bps": slippage, "extra_cost_bps": extra,
              "half_spread_bps": {v: float(cfg.half_spread_bps[v]) for v in venues}, "venues": venues,
              "pairs": [list(p) for p in pairs], "first_ts": str(frame.index[0]), "last_ts": str(last_time), "n_minutes": len(frame)}
    return BacktestResult(ledger=ledger, summary=_summary(ledger, pairs, market), params=params)
