"""C5.05-C5.09: specification, static/pairwise/rolling IS-CS-ILS and RQ3 tables.

All functions take an already-selected dev or val slice of D3; none reads the
final test. Missing prices are never filled: incomplete rows are dropped only
when their share is at most `max_missing_frac`, otherwise the sample fails.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.vector_ar.vecm import select_order

from src.cointegration.johansen import johansen_test
from src.cointegration.stationarity import integration_order
from src.utils.schema import venues_from_panel
from .info_leadership import information_leadership
from .info_share import component_share, hasbrouck_is
from .vecm import fit_vecm

SHARE_COLUMNS = ["is_lower", "is_upper", "is_mid", "component_share", "ils"]


def log_prices(panel: pd.DataFrame, venues=None, max_missing_frac: float = 0.05) -> pd.DataFrame:
    """Venue-named log prices with incomplete rows dropped (never filled)."""
    venues = list(venues or venues_from_panel(panel))
    logs = panel[[f"log_close__{v}" for v in venues]].copy()
    logs.columns = venues
    complete = logs.notna().all(axis=1)
    if len(logs) == 0 or (1 - complete.mean()) > max_missing_frac:
        raise ValueError(f"missing_prices: {1 - complete.mean() if len(logs) else 1:.1%} incomplete rows > {max_missing_frac:.0%}")
    return logs[complete]


def stationarity_table(logs: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """T2: ADF/KPSS on levels and first differences, and the integration order."""
    rows = []
    for venue in logs.columns:
        result = integration_order(logs[venue], max_order=1, alpha=alpha)
        row = {"venue": venue, "integration_order": result["order"], "alpha": alpha}
        for step in result["history"]:
            tag = "level" if step["d"] == 0 else "diff1"
            row.update({f"adf_stat_{tag}": step["adf"]["statistic"], f"adf_p_{tag}": step["adf"]["pvalue"],
                        f"kpss_stat_{tag}": step["kpss"]["statistic"], f"kpss_p_{tag}": step["kpss"]["pvalue"]})
        rows.append(row)
    return pd.DataFrame(rows)


def select_spec(logs: pd.DataFrame, max_k_ar_diff: int = 10, deterministic: str = "co",
                criterion: str = "bic", significance: int = 95) -> tuple[dict, pd.DataFrame]:
    """Pick k_ar_diff by information criterion and test the Johansen rank on dev.

    IS/CS assume one common efficient price (same BTC/USDT on every venue), so
    the model rank is imposed at n-1; the Johansen rank is reported next to it.
    """
    order = select_order(logs.to_numpy(dtype=float), maxlags=max_k_ar_diff, deterministic=deterministic)
    k_ar_diff = int(getattr(order, criterion))
    det_order = 0 if deterministic in {"co", "ci"} else (1 if deterministic in {"lo", "li"} else -1)
    joh = johansen_test(logs, det_order=det_order, k_ar_diff=k_ar_diff, significance=significance)
    n = logs.shape[1]
    spec = {"venues": list(logs.columns), "k_ar_diff": k_ar_diff, "deterministic": deterministic,
            "criterion": criterion, "max_k_ar_diff": max_k_ar_diff, "significance": significance,
            "johansen_rank": joh["coint_rank"], "coint_rank": n - 1,
            "rank_matches_theory": joh["coint_rank"] == n - 1}
    table = pd.DataFrame({"rank_h0": range(n), "trace_stat": joh["trace_stat"], "trace_crit": joh["trace_crit"],
                          "max_eig_stat": joh["max_eig_stat"], "max_eig_crit": joh["max_eig_crit"],
                          "eigenvalue": joh["eigenvalues"]})
    table["reject_h0"] = table.trace_stat > table.trace_crit
    table["k_ar_diff"], table["significance"] = k_ar_diff, significance
    return spec, table


def static_shares(logs: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """T3: multivariate IS bounds and CS for all venues in one system."""
    result = fit_vecm(logs, spec["k_ar_diff"], logs.shape[1] - 1, spec["deterministic"])
    shares = hasbrouck_is(result).drop(columns="exchange")
    shares["component_share"] = shares.venue.map(component_share(result))
    shares["n_obs"] = len(logs)
    return shares


def pairwise_ils(logs: pd.DataFrame, spec: dict, pairs=None) -> pd.DataFrame:
    """T3d: per pair, bivariate IS bounds, CS and ILS side by side (one row per venue)."""
    rows = []
    for a, b in pairs or combinations(logs.columns, 2):
        base = {"pair": f"{a}|{b}", "venue_a": a, "venue_b": b, "n_obs": len(logs)}
        try:
            result = fit_vecm(logs[[a, b]], spec["k_ar_diff"], 1, spec["deterministic"])
            shares = hasbrouck_is(result).drop(columns="exchange").set_index("venue")
            ils = information_leadership(shares.reset_index(), component_share(result)).set_index("venue")
            for venue in (a, b):
                rows.append({**base, "venue": venue, **shares.loc[venue].to_dict(),
                             "component_share": ils.loc[venue, "component_share"], "il": ils.loc[venue, "il"],
                             "ils": ils.loc[venue, "ils"], "leader": _leader(shares), "status": "ok", "reason": ""})
        except (ValueError, np.linalg.LinAlgError) as exc:
            for venue in (a, b):
                rows.append({**base, "venue": venue, "status": "failed", "reason": str(exc)})
    return pd.DataFrame(rows)


def summarize_ils(pairs: pd.DataFrame) -> pd.DataFrame:
    """Aggregate pairwise ILS per venue: mean ILS and how many pairs it leads."""
    ok = pairs[pairs.status.eq("ok")]
    if ok.empty:
        return pd.DataFrame(columns=["venue", "n_pairs", "mean_ils", "mean_is_mid", "n_pairs_led"])
    ok = ok.assign(led=ok.leader.eq(ok.venue))
    return ok.groupby("venue").agg(n_pairs=("pair", "nunique"), mean_ils=("ils", "mean"),
                                   mean_is_mid=("is_mid", "mean"), n_pairs_led=("led", "sum")).reset_index()


def _leader(shares: pd.DataFrame):
    """Venue whose IS lower bound exceeds every other upper bound, else None."""
    for venue in shares.index:
        if shares.loc[venue, "is_lower"] > shares.drop(index=venue).is_upper.max():
            return venue
    return None


def _window_rows(panel, spec, start, end, max_missing_frac):
    times = panel.index
    base = {"window_start": times[start], "window_end": times[end - 1], "available_at": times[end - 1]}
    venues = spec["venues"]
    try:
        logs = log_prices(panel.iloc[start:end], venues, max_missing_frac)
        system = static_shares(logs, spec).set_index("venue")
        pairs = pairwise_ils(logs, spec)
        failed = pairs[pairs.status.ne("ok")]
        if not failed.empty:
            raise ValueError(f"ils_failed: {failed.reason.iloc[0]}")
        ils = pairs.groupby("venue").ils.mean()
        leader = _leader(system)
        return [{**base, "venue": v, **{c: system.loc[v, c] for c in SHARE_COLUMNS[:4]}, "ils": float(ils[v]),
                 "leader": leader, "status": "ok", "reason": "" if leader else "overlapping_bounds"} for v in venues]
    except (ValueError, np.linalg.LinAlgError) as exc:
        return [{**base, "venue": v, **{c: np.nan for c in SHARE_COLUMNS}, "leader": None,
                 "status": "failed", "reason": str(exc)} for v in venues]


def rolling_shares(panel: pd.DataFrame, spec: dict, window_obs: int, step_obs: int,
                   max_missing_frac: float = 0.05) -> pd.DataFrame:
    """D6: refit the locked spec on each past window; failed windows keep a reason.

    `ils` is the mean of the venue's pairwise ILS inside the window. A window
    uses only rows up to window_end, so available_at = window_end.
    """
    if window_obs < 2 or step_obs < 1:
        raise ValueError("window_obs must be >= 2 and step_obs >= 1")
    if len(panel) < window_obs:
        raise ValueError(f"sample has {len(panel)} rows, fewer than window_obs={window_obs}")
    rows = []
    for start in range(0, len(panel) - window_obs + 1, step_obs):
        rows += _window_rows(panel, spec, start, start + window_obs, max_missing_frac)
    out = pd.DataFrame(rows)
    out["leader"] = out.leader.astype(object).where(out.leader.notna(), None)
    return out


def sensitivity(panel: pd.DataFrame, spec: dict, window_obs: int, step_obs: int,
                windows=None, steps=None, lags=None, max_missing_frac: float = 0.05) -> pd.DataFrame:
    """T3b: change one of window/step/lag at a time around the locked baseline."""
    windows = windows or [window_obs // 2, window_obs, window_obs * 2]
    steps = steps or [step_obs, step_obs * 2]
    lags = lags or [max(spec["k_ar_diff"] - 1, 0), spec["k_ar_diff"], spec["k_ar_diff"] + 1]
    runs = {(window_obs, step_obs, spec["k_ar_diff"])}
    runs |= {(w, step_obs, spec["k_ar_diff"]) for w in windows}
    runs |= {(window_obs, s, spec["k_ar_diff"]) for s in steps}
    runs |= {(window_obs, step_obs, k) for k in lags}
    rows = []
    for w, s, k in sorted(runs):
        setting = {"window_obs": w, "step_obs": s, "k_ar_diff": k,
                   "is_baseline": (w, s, k) == (window_obs, step_obs, spec["k_ar_diff"])}
        try:
            rolled = rolling_shares(panel, {**spec, "k_ar_diff": k}, w, s, max_missing_frac)
        except ValueError as exc:
            rows.append({**setting, "venue": None, "status": "failed", "reason": str(exc)})
            continue
        n_windows = rolled.window_start.nunique()
        ok = rolled[rolled.status.eq("ok")]
        for venue in spec["venues"]:
            mine = ok[ok.venue.eq(venue)]
            rows.append({**setting, "venue": venue, "n_windows": n_windows, "n_ok": len(mine),
                         "mean_is_mid": mine.is_mid.mean(), "mean_component_share": mine.component_share.mean(),
                         "mean_ils": mine.ils.mean(),
                         "lead_share": float((mine.leader == venue).mean()) if len(mine) else np.nan,
                         "status": "ok", "reason": ""})
    return pd.DataFrame(rows)


def spot_perp_pairs(venues) -> list[tuple[str, str]]:
    """(exchange_spot, exchange_perp) pairs present among the venues."""
    venues = set(venues)
    return [(v, v.replace("_spot", "_perp")) for v in sorted(venues)
            if v.endswith("_spot") and v.replace("_spot", "_perp") in venues]


def spot_vs_perp(logs: pd.DataFrame, spec: dict, split: str) -> pd.DataFrame:
    """T3c (RQ3): separate bivariate spot/perp systems per exchange, same sample."""
    rows = []
    for spot, perp in spot_perp_pairs(logs.columns):
        pair = pairwise_ils(logs, spec, pairs=[(spot, perp)])
        row = {"split": split, "exchange": spot.rsplit("_", 1)[0], "venue_spot": spot, "venue_perp": perp,
               "n_obs": len(logs), "k_ar_diff": spec["k_ar_diff"]}
        if pair.status.ne("ok").any():
            rows.append({**row, "status": "failed", "reason": pair.reason.iloc[0], "conclusion": "failed"})
            continue
        by = pair.set_index("venue")
        for tag, venue in (("spot", spot), ("perp", perp)):
            row.update({f"{c}_{tag}": by.loc[venue, c] for c in SHARE_COLUMNS})
        leader = by.leader.iloc[0]
        conclusion = {spot: "spot_leads", perp: "perp_leads"}.get(leader, "chua_phan_biet_duoc")
        rows.append({**row, "leader": leader, "conclusion": conclusion, "status": "ok", "reason": ""})
    return pd.DataFrame(rows)
