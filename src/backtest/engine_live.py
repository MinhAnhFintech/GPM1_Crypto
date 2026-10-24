"""TV6 C6.05: backtest B on the live sample, with real top-of-book bid/ask.

Input is the live D2 aligned to a fixed grid by TV4's align_live_quotes (quotes known at receive
time; older than max_age -> missing, never carried forward). Prices are executable quotes:
    gross_gap_bps = (bid_sell - ask_buy) / ask_buy * 10_000   (no spread added: it is inside bid/ask)
    net_gap_bps   = gross - fee_buy - fee_sell - slippage - extra_cost        (src/cost_model/costs.py)

Timing (no look-ahead):
  * decision at grid time t uses only quotes received at or before t;
  * a signal fires when the decision net_gap_bps exceeds entry_threshold_bps;
  * the order fills L = latency_s seconds later at the quotes known at t + L (latency is time, not a fee);
  * a pair (buy_venue, sell_venue) opens no new signal until its previous fill time has passed.
A signal is rejected, with a reason, when t + L is past the data, falls after the end of the decision's
collection session (sessions are never joined), a fill quote is missing/stale, or the top-of-book size
on either leg is smaller than qty_btc (we only observe the touch, so a larger fill would cost more than
measured). Capital is pre-positioned on every venue. Only same-market_type pairs exist.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from src.cost_model.costs import BPS_PER_UNIT, DEFAULT_FEE_TYPE, FeeSchedule, _finite_nonnegative, gross_gap_bps, net_gap_bps
from src.cost_model.pairs import generate_pairs, split_venue
from src.utils.schema import data_kind
from src.utils.time_utils import require_utc
from .engine import FILLED, REJECTED, BacktestResult, _summary

METHOD = "live_bidask"
LEDGER_COLUMNS = ["signal_id", "ts_decision", "ts_fill", "market_type", "buy_exchange", "sell_exchange", "qty_btc",
                  "buy_px", "sell_px", "fees_usdt", "pnl_usdt", "status", "reject_reason",
                  "buy_venue", "sell_venue", "session_id", "latency_s", "decision_mid_gap_bps", "decision_gross_bps", "decision_net_bps", "gross_bps",
                  "net_bps", "fee_total_bps", "other_cost_usdt", "buy_top_size_btc", "sell_top_size_btc",
                  "buy_quote_age_s", "sell_quote_age_s", "method", "data_kind"]
REASONS = ("fill_beyond_sample", "fill_after_session_end", "missing_fill_quote", "insufficient_top_size")


@dataclass(frozen=True)
class LiveBacktestConfig:
    """Backtest B settings. Units: seconds, bps one-way per leg, BTC.

    latency_s must be a whole number of grid steps (grid_step_s) so the fill lands on the aligned grid.
    slippage_bps / extra_cost_bps: None -> fees.yaml defaults. require_top_size: reject fills larger
    than the touch size on either leg.
    """

    fees: FeeSchedule
    grid_step_s: float = 5.0
    latency_s: float = 5.0
    entry_threshold_bps: float = 0.0
    qty_btc: float = 0.1
    fee_type: str = DEFAULT_FEE_TYPE
    slippage_bps: float | None = None
    extra_cost_bps: float | None = None
    require_top_size: bool = True

    def __post_init__(self):
        if not (np.isfinite(self.grid_step_s) and self.grid_step_s > 0):
            raise ValueError("grid_step_s must be positive (seconds)")
        if not (np.isfinite(self.latency_s) and self.latency_s >= 0):
            raise ValueError("latency_s must be a nonnegative number of seconds")
        steps = self.latency_s / self.grid_step_s
        if abs(steps - round(steps)) > 1e-9:
            raise ValueError(f"latency_s={self.latency_s} must be a multiple of grid_step_s={self.grid_step_s}")
        if not np.isfinite(self.entry_threshold_bps):
            raise ValueError("entry_threshold_bps must be finite")
        if not (np.isfinite(self.qty_btc) and self.qty_btc > 0):
            raise ValueError("qty_btc must be positive")


def session_windows(quotes: pd.DataFrame) -> pd.DataFrame:
    """[start, end] of each collection session from D2 receive times (one window if D2 has no session_id)."""
    frame = quotes if "session_id" in quotes.columns else quotes.assign(session_id="all")
    windows = frame.groupby("session_id")["ts_recv_utc"].agg(start="min", end="max").reset_index()
    return windows.sort_values("start").reset_index(drop=True)


def _session_of(times: pd.DatetimeIndex, windows: pd.DataFrame):
    """(session_id, session_end) for each grid time; NaN/NaT outside every session."""
    naive = lambda s: pd.DatetimeIndex(s).tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    starts, end_values, grid = naive(windows["start"]), naive(windows["end"]), naive(times)
    k = np.searchsorted(starts, grid, side="right") - 1
    ids = np.full(len(times), None, dtype=object)
    ends = np.full(len(times), np.datetime64("NaT"), dtype="datetime64[ns]")
    ok = k >= 0
    inside = ok.copy()
    inside[ok] = grid[ok] <= end_values[k[ok]]
    ids[inside] = windows["session_id"].to_numpy()[k[inside]]
    ends[inside] = end_values[k[inside]]
    return ids, pd.DatetimeIndex(ends).tz_localize("UTC")


def run_backtest_live(wide: pd.DataFrame, cfg: LiveBacktestConfig, sessions: pd.DataFrame) -> BacktestResult:
    """Backtest B on aligned live quotes (output of align_live_quotes / quotes_to_wide).

    wide: index ts_available (UTC ns grid of grid_step_s), bid__/ask__/bid_size__/ask_size__/quote_age_s__{venue},
      optional missing__{venue}; one data_kind. sessions: session_windows(D2) of the same D2.
    Returns a ledger in D8 columns (qty BTC, prices/fees/PnL USDT, bps one-way) plus audit columns, with
      pnl_usdt = net_bps / 10_000 * qty_btc * buy_px; rejected signals have no fill (prices NaN, PnL and fees 0).
    """
    if wide.index.name != "ts_available":
        raise ValueError("wide quotes must be indexed by ts_available")
    require_utc(pd.Series(wide.index), "ts_available")
    if not wide.index.is_unique or not wide.index.is_monotonic_increasing:
        raise ValueError("ts_available must be unique and increasing")
    kind = data_kind(wide)
    venues = [str(c)[len("ask__"):] for c in wide.columns if str(c).startswith("ask__")]
    for venue in venues:
        missing = [f"{m}__{venue}" for m in ("bid", "bid_size", "ask_size") if f"{m}__{venue}" not in wide.columns]
        if missing:
            raise KeyError(f"wide quotes lack {missing}")
    pairs = generate_pairs(venues)
    market = {v: split_venue(v)[1] for v in venues}
    fee = {v: cfg.fees.fee_bps(v, cfg.fee_type) for v in venues}
    slippage = _finite_nonnegative(cfg.fees.slippage_bps if cfg.slippage_bps is None else cfg.slippage_bps, "slippage_bps")
    extra = _finite_nonnegative(cfg.fees.extra_cost_bps if cfg.extra_cost_bps is None else cfg.extra_cost_bps, "extra_cost_bps")

    def column(metric, venue):
        values = wide[f"{metric}__{venue}"].to_numpy(dtype=float, na_value=np.nan)
        flag = f"missing__{venue}"
        if flag in wide.columns:                                   # never use a quote flagged unusable
            values = np.where(wide[flag].to_numpy(dtype=bool), np.nan, values)
        return values

    quote = {v: {m: column(m, v) for m in ("bid", "ask", "bid_size", "ask_size")} for v in venues}
    age = {v: (wide[f"quote_age_s__{v}"].to_numpy(dtype=float) if f"quote_age_s__{v}" in wide.columns
               else np.full(len(wide), np.nan)) for v in venues}
    for v in venues:
        bad = (quote[v]["bid"] <= 0) | (quote[v]["ask"] < quote[v]["bid"])
        if np.nan_to_num(bad, nan=False).astype(bool).any():
            raise ValueError(f"{v}: invalid quotes (bid <= 0 or ask < bid)")
    times = wide.index
    position = pd.Series(np.arange(len(times)), index=times)
    session_id, session_end = _session_of(times, sessions)
    latency = pd.Timedelta(seconds=float(cfg.latency_s))
    last_time = times[-1]

    records = []
    for buy, sell in pairs:
        ask_buy, bid_sell = quote[buy]["ask"], quote[sell]["bid"]
        decision_gross = gross_gap_bps(bid_sell, ask_buy)
        mid_buy = (quote[buy]["bid"] + quote[buy]["ask"]) / 2
        mid_sell = (quote[sell]["bid"] + quote[sell]["ask"]) / 2
        decision_mid = gross_gap_bps(mid_sell, mid_buy)          # mid-to-mid gap before crossing the spread
        decision_net = net_gap_bps(decision_gross, fee[buy], fee[sell], slippage, extra)
        candidates = np.flatnonzero(np.nan_to_num(decision_net, nan=-np.inf) > cfg.entry_threshold_bps)
        blocked_until = None
        for i in candidates:
            t = times[i]
            if blocked_until is not None and t <= blocked_until:
                continue
            t_fill = t + latency
            blocked_until = t_fill
            record = {"ts_decision": t, "buy_venue": buy, "sell_venue": sell, "session_id": session_id[i],
                      "decision_mid_gap_bps": float(decision_mid[i]), "decision_gross_bps": float(decision_gross[i]), "decision_net_bps": float(decision_net[i])}
            j = position.get(t_fill)
            if t_fill > last_time:
                record.update(status=REJECTED, reject_reason="fill_beyond_sample")
            elif session_id[i] is None or t_fill > session_end[i]:
                record.update(status=REJECTED, reject_reason="fill_after_session_end")
            elif j is None or not (np.isfinite(ask_buy[j]) and np.isfinite(bid_sell[j])):
                record.update(status=REJECTED, reject_reason="missing_fill_quote")
            else:
                buy_size, sell_size = quote[buy]["ask_size"][j], quote[sell]["bid_size"][j]
                record.update(buy_top_size_btc=float(buy_size), sell_top_size_btc=float(sell_size),
                              buy_quote_age_s=float(age[buy][j]), sell_quote_age_s=float(age[sell][j]))
                if cfg.require_top_size and not (buy_size >= cfg.qty_btc and sell_size >= cfg.qty_btc):
                    record.update(status=REJECTED, reject_reason="insufficient_top_size")
                else:
                    gross = float(gross_gap_bps(bid_sell[j], ask_buy[j]))
                    record.update(status=FILLED, reject_reason=None, ts_fill=t_fill, buy_px=float(ask_buy[j]),
                                  sell_px=float(bid_sell[j]), gross_bps=gross,
                                  net_bps=float(net_gap_bps(gross, fee[buy], fee[sell], slippage, extra)))
            records.append(record)

    base = ["ts_decision", "buy_venue", "sell_venue", "session_id", "decision_mid_gap_bps", "decision_gross_bps", "decision_net_bps", "status",
            "reject_reason", "ts_fill", "buy_px", "sell_px", "gross_bps", "net_bps", "buy_top_size_btc", "sell_top_size_btc",
            "buy_quote_age_s", "sell_quote_age_s"]
    ledger = pd.DataFrame.from_records(records, columns=base)
    ledger["ts_decision"] = pd.to_datetime(ledger["ts_decision"], utc=True).astype("datetime64[ns, UTC]")
    ledger["ts_fill"] = pd.to_datetime(ledger["ts_fill"], utc=True).astype("datetime64[ns, UTC]")
    for c in base[4:7] + base[10:]:
        ledger[c] = ledger[c].astype("float64")
    for c in ("buy_venue", "sell_venue", "status"):
        ledger[c] = ledger[c].astype(str)
    ledger["session_id"] = ledger["session_id"].astype(object)
    ledger["market_type"] = ledger["buy_venue"].map(market).astype(str)
    ledger["buy_exchange"] = ledger["buy_venue"].map(lambda v: split_venue(v)[0]).astype(str)
    ledger["sell_exchange"] = ledger["sell_venue"].map(lambda v: split_venue(v)[0]).astype(str)
    ledger["qty_btc"] = float(cfg.qty_btc)
    ledger["latency_s"] = float(cfg.latency_s)
    ledger["fee_total_bps"] = (ledger["buy_venue"].map(fee) + ledger["sell_venue"].map(fee)).astype("float64")
    notional = ledger["qty_btc"] * ledger["buy_px"]
    filled = ledger["status"] == FILLED
    ledger["fees_usdt"] = (ledger["fee_total_bps"] / BPS_PER_UNIT * notional).where(filled, 0.0)
    ledger["other_cost_usdt"] = ((slippage + extra) / BPS_PER_UNIT * notional).where(filled, 0.0)
    ledger["pnl_usdt"] = (ledger["net_bps"] / BPS_PER_UNIT * notional).where(filled, 0.0)
    ledger["signal_id"] = ("B_" + ledger["market_type"] + "_" + ledger["buy_venue"] + "_" + ledger["sell_venue"] + "_"
                           + ledger["ts_decision"].dt.strftime("%Y%m%dT%H%M%SZ").astype(str) + f"_L{cfg.latency_s:g}s")
    ledger["method"] = METHOD
    ledger["data_kind"] = kind
    ledger = ledger.sort_values(["ts_decision", "buy_venue", "sell_venue"], kind="stable").reset_index(drop=True)[LEDGER_COLUMNS]
    ledger.attrs.update(data_kind=kind, method=METHOD)
    summary = _summary(ledger, pairs, market)
    reasons = ledger[ledger["status"] == REJECTED].groupby(["buy_venue", "sell_venue", "reject_reason"]).size()
    for reason in REASONS:
        summary[f"rejected_{reason}"] = [int(reasons.get((b, s, reason), 0)) for b, s in zip(summary.buy_venue, summary.sell_venue)]
    params = {"method": METHOD, "data_kind": kind, "grid_step_s": float(cfg.grid_step_s), "latency_s": float(cfg.latency_s),
              "entry_threshold_bps": float(cfg.entry_threshold_bps), "qty_btc": float(cfg.qty_btc), "fee_type": cfg.fee_type,
              "fee_bps": fee, "slippage_bps": slippage, "extra_cost_bps": extra, "require_top_size": bool(cfg.require_top_size),
              "venues": venues, "pairs": [list(p) for p in pairs], "sessions": sessions["session_id"].astype(str).tolist(),
              "first_ts": str(times[0]), "last_ts": str(last_time), "n_grid": len(times)}
    return BacktestResult(ledger=ledger, summary=summary, params=params)


def market_summary(ledger: pd.DataFrame) -> pd.DataFrame:
    """One row per market_type (spot, perp): the two groups are reported separately, never pooled across types."""
    rows = []
    for market_type, part in ledger.groupby("market_type"):
        filled = part[part["status"] == FILLED]
        rows.append({"market_type": market_type, "n_signals": len(part), "n_filled": len(filled),
                     "n_rejected": int((part["status"] == REJECTED).sum()), "n_net_positive": int((filled["net_bps"] > 0).sum()),
                     "mean_net_bps": float(filled["net_bps"].mean()) if len(filled) else np.nan,
                     "total_pnl_usdt": float(filled["pnl_usdt"].sum()), "total_fees_usdt": float(filled["fees_usdt"].sum()),
                     **{f"rejected_{r}": int((part["reject_reason"] == r).sum()) for r in REASONS}})
    return pd.DataFrame(rows)
