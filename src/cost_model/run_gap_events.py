"""TV6 C6.03 runner: quotes/panel -> compute_gaps -> detect_gap_events -> gap_events.parquet.

Fixtures (default, synthetic, real bid/ask from the live-quotes fixture):
    python -m src.cost_model.run_gap_events
Real historical panel (analysed on the development split by default, --split val for validation; the final test is never read;
close-only proxy; the half spread is calibrated from live D2 = median spread_bps / 2 per venue,
the largest venue value is applied to every leg; fees.yaml assumed_half_spread_bps is used only if no D2 is given):
    python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet
Live D2 quotes use gap_events.live_grid_step_s (5 s) and gap_events.live_max_age_s from config or
--live-max-age-s in seconds, chosen for the actual receive cadence. Fixtures stay on the 1-minute grid.
Output: the live sample -> gap_events.parquet (D7 path), a historical panel -> gap_events_historical.parquet;
synthetic output goes to outputs/synthetic/<case>/tables/, real output to outputs/tables/.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import math
from numbers import Real

import pandas as pd

from src.utils.config import load_config
from src.utils.io_utils import read_parquet
from src.synchronization.live import align_live_quotes
from src.utils.time_utils import require_utc
from .costs import load_fees
from .gap_events import DEFAULT_GAP_EVENTS_PATH, ROOT, detect_gap_events, write_gap_events
from .pairs import compute_gaps
from src.synchronization.split import create_split_manifest, select_split

HISTORICAL_NAME = "gap_events_historical.parquet"
PAIRED_KIND = {"historical": "live", "synthetic": "synthetic"}   # panel data_kind -> D2 kind allowed to calibrate its half spread

DEFAULT_FIXTURE_CASE = "multi"


def quotes_to_wide(quotes: pd.DataFrame, grid_step_s: float, max_age_s: float | None = None) -> pd.DataFrame:
    """TV4 adapter: D2 -> causal wide quotes, ready for TV6 compute_gaps.

    Both durations are seconds. Irregular receive times require explicit max_age_s
    (from live_max_age_s in config or CLI). Without it, on-grid fixtures use exact
    receive-time matches only; no quote is silently carried forward. Source receive
    timestamps and quote ages are retained by align_live_quotes for audit.
    """
    if isinstance(grid_step_s, bool) or not isinstance(grid_step_s, Real) or not math.isfinite(grid_step_s) or grid_step_s <= 0:
        raise ValueError("grid_step_s must be finite and positive (seconds)")
    step = pd.Timedelta(seconds=grid_step_s)
    if step <= pd.Timedelta(0):
        raise ValueError("grid_step_s must be at least one nanosecond")
    times = quotes["ts_recv_utc"]
    require_utc(times, "ts_recv_utc")
    if max_age_s is None:
        if (times - times.dt.floor(step)).ne(pd.Timedelta(0)).any():
            raise ValueError("Off-grid live quotes need explicit live_max_age_s in config or --live-max-age-s")
        max_age_s = 0.0
    if isinstance(max_age_s, bool) or not isinstance(max_age_s, Real) or not math.isfinite(max_age_s) or max_age_s < 0:
        raise ValueError("live_max_age_s must be finite and nonnegative (seconds)")
    return align_live_quotes(quotes, grid_freq=step, max_age=pd.Timedelta(seconds=max_age_s))


def _select_split(panel: pd.DataFrame, manifest_path, split: str) -> pd.DataFrame:
    """Rows of one analysis split (dev or val) checked against the D4 fingerprint; the final test is never selectable.

    Synthetic fixtures have no D4 on disk, so their manifest is built in memory from the panel.
    """
    import json
    from src.utils.schema import data_kind
    if manifest_path is None and data_kind(panel) == "synthetic":
        manifest = create_split_manifest(panel)
    else:
        manifest = json.loads(Path(manifest_path or ROOT/"data/processed/split_manifest.json").read_text(encoding="utf-8"))
    return select_split(panel, manifest, split)


def _calibrated_half_spread(panel: pd.DataFrame, quotes_path, fees) -> float | None:
    """Half spread (bps per leg) for the close proxy, from D2 of the matching data_kind; None keeps fees.yaml's value.

    Per-venue median(spread_bps)/2, then the LARGEST venue value (conservative: compute_gaps takes one scalar for all legs).
    A historical panel may only be calibrated on live D2, a synthetic one on synthetic D2.
    """
    from src.backtest.calibration import estimate_half_spread_bps
    from src.utils.schema import data_kind, venues_from_panel
    if quotes_path is None:
        if fees.assumed_half_spread_bps is not None or data_kind(panel) != "historical":
            return None
        quotes_path = ROOT/"data/processed/live_quotes_long.parquet"
    quotes = read_parquet(quotes_path, "D2")
    if data_kind(quotes) != PAIRED_KIND[data_kind(panel)]:
        raise ValueError(f"A {data_kind(panel)} panel must be calibrated on {PAIRED_KIND[data_kind(panel)]} D2, got {data_kind(quotes)}")
    value = float(estimate_half_spread_bps(quotes, venues_from_panel(panel)).half_spread_bps.max())
    print(f"half spread from {Path(quotes_path).name}: {value:.4f} bps per leg (largest venue median/2)")
    return value


def main(argv=None) -> pd.DataFrame:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--quotes", type=Path, help="D2 long quotes (bid/ask)")
    source.add_argument("--panel", type=Path, help="D3 wide panel (close-only proxy)")
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"], default=DEFAULT_FIXTURE_CASE)
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--split", choices=["dev", "val"], default="dev", help="panel split to analyse (the final test is never read here)")
    parser.add_argument("--manifest", type=Path, help="D4 split manifest for a real panel (default data/processed/split_manifest.json)")
    parser.add_argument("--calibration-quotes", type=Path, help="D2 used to calibrate the close-proxy half spread (default: live D2 for a historical panel)")
    parser.add_argument("--live-max-age-s", type=float, help="Maximum quote age in seconds; overrides gap_events.live_max_age_s")
    args = parser.parse_args(argv)
    if args.panel and args.live_max_age_s is not None:
        parser.error("--live-max-age-s applies to D2 quotes, not an OHLCV panel")

    params = load_config(args.config).get("gap_events")
    if not params or "gap_threshold_bps" not in params or "grid_step_s" not in params:
        raise ValueError(f"{args.config} needs gap_events.gap_threshold_bps and gap_events.grid_step_s")
    fees = load_fees(args.fees)
    grid_step_s = params["grid_step_s"]
    if args.panel:
        panel = _select_split(read_parquet(args.panel, "D3"), args.manifest, args.split)
        gaps = compute_gaps(panel, fees, mode="close", assumed_half_spread_bps=_calibrated_half_spread(panel, args.calibration_quotes, fees))
    else:
        quotes_path = args.quotes or ROOT/"data/fixtures"/args.fixture_case/"live_quotes_long.parquet"
        max_age_s = args.live_max_age_s if args.live_max_age_s is not None else params.get("live_max_age_s")
        # Live D2 arrives every few seconds: its grid (live_grid_step_s) differs from the 1-minute historical grid.
        # Fixtures are on the 1-minute grid, so they keep grid_step_s unless live_grid_step_s is set.
        grid_step_s = params.get("live_grid_step_s", grid_step_s) if args.quotes else grid_step_s
        wide = quotes_to_wide(read_parquet(quotes_path, "D2"), grid_step_s, max_age_s)
        gaps = compute_gaps(wide, fees, mode="quotes")
    events = detect_gap_events(gaps, params["gap_threshold_bps"], grid_step_s)
    kind = events.attrs["data_kind"]
    # One data_kind per file: the live sample (bid/ask) keeps the contract path gap_events.parquet, the historical
    # close-proxy events go next to it as gap_events_historical.parquet so the two sources never overwrite each other.
    name = HISTORICAL_NAME if args.panel else Path(DEFAULT_GAP_EVENTS_PATH).name
    output = args.output or (ROOT/"outputs/synthetic"/args.fixture_case/"tables"/name if kind == "synthetic"
                             else ROOT/"outputs/tables"/name)
    write_gap_events(events, output)
    print(f"{len(events)} gap events ({kind}) -> {output}")
    return events


if __name__ == "__main__":
    main()
