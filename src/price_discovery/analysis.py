"""C5.05-C5.09: specification, static/pairwise/rolling IS-CS-ILS and RQ3 tables.

All functions take an already-selected dev or val slice of D3; none reads the
final test. Only incomplete edges may be trimmed. An internal hole fails the
window rather than compressing elapsed time into a shorter statistical lag.
The cointegration rank comes from Johansen on dev; IS/CS/ILS are computed only
where Johansen supports exactly one common trend, otherwise rows fail with a reason.
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from src.cointegration.johansen import DET_ORDER, johansen_test, select_lag_order
from src.cointegration.stationarity import adf_test, integration_order, kpss_test
from src.utils.schema import venues_from_panel
from src.utils.time_utils import require_utc, utc_timestamp
from .info_leadership import information_leadership
from .info_share import component_share, hasbrouck_is
from .vecm import fit_vecm, vecm_diagnostics

SPEC_VERSION = 3
SHARE_COLUMNS = ["is_lower", "is_upper", "is_mid", "component_share", "ils"]


def log_prices(panel: pd.DataFrame, venues=None, max_missing_frac: float = 0.05) -> pd.DataFrame:
    """Trim missing edges only; retain the original UTC grid inside the sample."""
    if not 0 <= max_missing_frac < 1:
        raise ValueError("max_missing_frac must be in [0, 1)")
    require_utc(panel.index, "D3 index")
    if not panel.index.is_unique or not panel.index.is_monotonic_increasing:
        raise ValueError("missing_prices: index must be unique and increasing")
    if len(panel) > 2 and len(set(np.diff(panel.index.asi8))) != 1:
        raise ValueError("missing_prices: keep a regular time grid")
    venues = list(venues or venues_from_panel(panel))
    logs = panel[[f"log_close__{v}" for v in venues]].copy()
    logs.columns = venues
    complete = logs.notna().all(axis=1)
    if len(logs) == 0 or (1 - complete.mean()) > max_missing_frac:
        raise ValueError(f"missing_prices: {1 - complete.mean() if len(logs) else 1:.1%} incomplete rows > {max_missing_frac:.0%}")
    positions = np.flatnonzero(complete.to_numpy())
    if not len(positions):
        raise ValueError("missing_prices: no complete rows")
    first, last = positions[0], positions[-1]
    if not complete.iloc[first:last+1].all():
        raise ValueError("missing_prices: internal gaps cannot be dropped or interpolated")
    return logs.iloc[first:last+1]


def stationarity_table(logs: pd.DataFrame, alpha: float = 0.05) -> pd.DataFrame:
    """T2: ADF (lag by BIC) and KPSS on log levels and first differences."""
    rows = []
    for venue in logs.columns:
        result = integration_order(logs[venue], max_order=1, alpha=alpha)
        # C5.01 requires both levels and first differences even if levels already
        # reject a unit root; stopping early must not hide the second table row.
        if len(result["history"]) == 1:
            diff = logs[venue].diff().iloc[1:]
            result["history"].append({"d": 1, "adf": adf_test(diff), "kpss": kpss_test(diff)})
        row = {"venue": venue, "integration_order": result["order"], "alpha": alpha}
        for step in result["history"]:
            tag = "level" if step["d"] == 0 else "diff1"
            adf, kpss = step["adf"], step["kpss"]
            row.update({f"adf_stat_{tag}": adf["statistic"], f"adf_p_{tag}": adf["pvalue"],
                        f"adf_lag_{tag}": adf["used_lag"], f"adf_nobs_{tag}": adf["nobs"],
                        f"kpss_stat_{tag}": kpss["statistic"], f"kpss_p_{tag}": kpss["pvalue"],
                        f"kpss_lag_{tag}": kpss["used_lag"], f"kpss_nobs_{tag}": kpss["nobs"]})
        row.update(adf_h0="unit root (non-stationary)", kpss_h0="stationary")
        rows.append(row)
    return pd.DataFrame(rows)


def johansen_table(logs: pd.DataFrame, det_order: int, k_ar_diff: int, significance: int) -> tuple[dict, pd.DataFrame]:
    """T2: trace and max-eigen statistics next to their critical values."""
    joh = johansen_test(logs, det_order=det_order, k_ar_diff=k_ar_diff, significance=significance)
    table = pd.DataFrame({"rank_h0": range(len(joh["venues"])), "trace_stat": joh["trace_stat"],
                          "trace_crit": joh["trace_crit"], "max_eig_stat": joh["max_eig_stat"],
                          "max_eig_crit": joh["max_eig_crit"], "eigenvalue": joh["eigenvalues"]})
    table["trace_reject_h0"] = table.trace_stat > table.trace_crit
    table["max_eig_reject_h0"] = table.max_eig_stat > table.max_eig_crit
    table = table.assign(rank_trace=joh["rank_trace"], rank_max_eig=joh["rank_max_eig"], k_ar_diff=k_ar_diff,
                         det_order=det_order, significance=significance, nobs=joh["nobs"])
    return joh, table


def _pair_key(a, b) -> str:
    return "|".join(sorted((a, b)))


def select_spec(logs: pd.DataFrame, max_k_ar_diff: int = 10, deterministic: str = "co",
                criterion: str = "bic", significance: int = 95, seed=None) -> tuple[dict, pd.DataFrame]:
    """Choose k_ar_diff (criterion) and the Johansen rank on dev, for the system and every pair."""
    k_ar_diff = select_lag_order(logs, max_k_ar_diff, deterministic, criterion)
    det_order = DET_ORDER[deterministic]
    joh, table = johansen_table(logs, det_order, k_ar_diff, significance)
    n = logs.shape[1]
    pair_ranks = {_pair_key(a, b): johansen_test(logs[[a, b]], det_order, k_ar_diff, significance)["coint_rank"]
                  for a, b in combinations(logs.columns, 2)}
    spec = {"spec_version": SPEC_VERSION, "venues": list(logs.columns), "k_ar_diff": k_ar_diff,
            "deterministic": deterministic, "det_order": det_order, "criterion": criterion,
            "max_k_ar_diff": max_k_ar_diff, "significance": significance,
            "johansen_rank_trace": joh["rank_trace"], "johansen_rank_max_eig": joh["rank_max_eig"],
            "coint_rank": joh["rank_trace"], "system_supported": joh["rank_trace"] == n - 1,
            "pair_ranks": pair_ranks, "seed": seed,
            "selected_available_at": utc_timestamp(logs.index.max()).isoformat()}
    return spec, table


def _system_reason(spec):
    n = len(spec["venues"])
    if spec["coint_rank"] != n - 1:
        return f"johansen_rank={spec['coint_rank']}: IS/CS need one common trend (rank {n - 1})"
    return None


def _pair_reason(spec, a, b):
    rank = spec["pair_ranks"].get(_pair_key(a, b))
    return None if rank == 1 else f"johansen_rank={rank} for pair: ILS needs rank 1"


def _fit_system(logs, spec):
    return fit_vecm(logs, spec["k_ar_diff"], spec["coint_rank"], spec["deterministic"])


def static_shares(logs: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """T3: multivariate IS bounds and CS for all venues in one system."""
    reason = _system_reason(spec)
    if reason:
        return pd.DataFrame({"venue": list(logs.columns), "status": "failed", "reason": reason, "n_obs": len(logs)})
    result = _fit_system(logs, spec)
    shares = hasbrouck_is(result, seed=spec.get("seed")).drop(columns="exchange")
    shares["component_share"] = shares.venue.map(component_share(result))
    return shares.assign(n_obs=len(logs), status="ok", reason="")


def diagnostics_table(logs: pd.DataFrame, spec: dict) -> pd.DataFrame:
    """T3: residual autocorrelation and stability for the system and each supported pair."""
    models = [("system", list(logs.columns), spec["coint_rank"], _system_reason(spec))]
    models += [(_pair_key(a, b), [a, b], 1, _pair_reason(spec, a, b)) for a, b in combinations(logs.columns, 2)]
    rows = []
    for name, cols, rank, reason in models:
        row = {"model": name, "coint_rank": rank, "k_ar_diff": spec["k_ar_diff"], "n_obs": len(logs)}
        if reason:
            rows.append({**row, "status": "skipped", "reason": reason})
            continue
        try:
            diag = vecm_diagnostics(fit_vecm(logs[cols], spec["k_ar_diff"], rank, spec["deterministic"]))
            rows.append({**row, **diag, "status": "ok", "reason": ""})
        except (ValueError, np.linalg.LinAlgError) as exc:
            rows.append({**row, "status": "failed", "reason": str(exc)})
    return pd.DataFrame(rows)


def pairwise_ils(logs: pd.DataFrame, spec: dict, pairs=None) -> pd.DataFrame:
    """T3d: per pair, bivariate IS bounds, CS and ILS side by side (one row per venue)."""
    rows = []
    for a, b in pairs or combinations(logs.columns, 2):
        base = {"pair": f"{a}|{b}", "venue_a": a, "venue_b": b, "n_obs": len(logs)}
        try:
            reason = _pair_reason(spec, a, b)
            if reason:
                raise ValueError(reason)
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
    end_time = times[end - 1]
    available = max(end_time, utc_timestamp(spec["selected_available_at"]))
    base = {"window_start": times[start], "window_end": end_time, "available_at": available}
    venues = spec["venues"]
    try:
        logs = log_prices(panel.iloc[start:end], venues, max_missing_frac)
        system = static_shares(logs, spec).set_index("venue")
        if system.status.ne("ok").any():
            raise ValueError(system.reason.iloc[0])
        pairs = pairwise_ils(logs, spec)
        ils = pairs[pairs.status.eq("ok")].groupby("venue").ils.mean()
        leader = _leader(system)
        rows = []
        for v in venues:
            row = {**base, "venue": v, **{c: system.loc[v, c] for c in SHARE_COLUMNS[:4]}, "leader": leader}
            if v in ils.index:
                rows.append({**row, "ils": float(ils[v]), "status": "ok", "reason": "" if leader else "overlapping_bounds"})
            else:
                rows.append({**row, "ils": np.nan, "status": "failed", "reason": "ils_failed: no estimable pair"})
        return rows
    except (ValueError, np.linalg.LinAlgError) as exc:
        return [{**base, "venue": v, **{c: np.nan for c in SHARE_COLUMNS}, "leader": None,
                 "status": "failed", "reason": str(exc)} for v in venues]


def rolling_shares(panel: pd.DataFrame, spec: dict, window_obs: int, step_obs: int,
                   max_missing_frac: float = 0.05) -> pd.DataFrame:
    """D6: refit the locked spec on each past window; failed windows keep a reason.

    `ils` is the mean of the venue's pairwise ILS inside the window. A window
    uses prices up to window_end. Availability also waits for dev specification
    selection; retrospective dev windows cannot masquerade as live signals.
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
    """T3b: change one of window/step/lag at a time around the locked baseline (rank stays locked)."""
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


def rolling_price_discovery(df_log, window_obs, step_obs, **kw) -> pd.DataFrame:
    """PDF public API: venue log prices plus an explicit dev-frozen ``spec``.

    This adapter never estimates a specification from future rolling windows.
    Select it on dev with select_spec first; all windows retain their UTC grid.
    """
    spec = kw.pop("spec", None)
    if spec is None:
        raise ValueError("rolling_price_discovery requires a dev-frozen spec")
    max_missing_frac = kw.pop("max_missing_frac", 0.05)
    if kw:
        raise ValueError(f"Unknown rolling options: {sorted(kw)}")
    panel = df_log.rename(columns=lambda v: f"log_close__{v}")
    return rolling_shares(panel, spec, window_obs, step_obs, max_missing_frac)


def spot_perp_discovery(df_log_pair, k_ar_diff, coint_rank) -> dict:
    """PDF public API: fit exactly one same-exchange spot/perp pair."""
    cols = list(df_log_pair.columns)
    if len(cols) != 2 or len(spot_perp_pairs(cols)) != 1:
        raise ValueError("spot_perp_discovery requires exactly one same-exchange spot/perp pair")
    panel = df_log_pair.rename(columns=lambda v: f"log_close__{v}")
    logs = log_prices(panel, venues=cols, max_missing_frac=0)
    result = fit_vecm(logs, k_ar_diff, coint_rank)
    shares = hasbrouck_is(result)
    cs = component_share(result)
    ils = information_leadership(shares, cs)
    return {"is": shares, "cs": cs, "ils": ils,
            "leader": _leader(shares.set_index("venue")),
            "diagnostics": vecm_diagnostics(result), "n_obs": len(logs)}
