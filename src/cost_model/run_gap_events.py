"""TV6 C6.03 runner: quotes/panel -> compute_gaps -> detect_gap_events -> gap_events.parquet.

Fixtures (default, synthetic, real bid/ask from the live-quotes fixture):
    python -m src.cost_model.run_gap_events
Real historical panel (close-only proxy; needs assumed_half_spread_bps in fees.yaml):
    python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet
Irregular D2 quotes require gap_events.live_max_age_s in config or
--live-max-age-s in seconds, chosen for the actual receive cadence.
Synthetic output goes to outputs/synthetic/<case>/tables/, real output to outputs/tables/.
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


def main(argv=None) -> pd.DataFrame:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--quotes", type=Path, help="D2 long quotes (bid/ask)")
    source.add_argument("--panel", type=Path, help="D3 wide panel (close-only proxy)")
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"], default=DEFAULT_FIXTURE_CASE)
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--live-max-age-s", type=float, help="Maximum quote age in seconds; overrides gap_events.live_max_age_s")
    args = parser.parse_args(argv)
    if args.panel and args.live_max_age_s is not None:
        parser.error("--live-max-age-s applies to D2 quotes, not an OHLCV panel")

    params = load_config(args.config).get("gap_events")
    if not params or "gap_threshold_bps" not in params or "grid_step_s" not in params:
        raise ValueError(f"{args.config} needs gap_events.gap_threshold_bps and gap_events.grid_step_s")
    fees = load_fees(args.fees)
    if args.panel:
        gaps = compute_gaps(read_parquet(args.panel, "D3"), fees, mode="close")
    else:
        quotes_path = args.quotes or ROOT/"data/fixtures"/args.fixture_case/"live_quotes_long.parquet"
        max_age_s = args.live_max_age_s if args.live_max_age_s is not None else params.get("live_max_age_s")
        wide = quotes_to_wide(read_parquet(quotes_path, "D2"), params["grid_step_s"], max_age_s)
        gaps = compute_gaps(wide, fees, mode="quotes")
    events = detect_gap_events(gaps, params["gap_threshold_bps"], params["grid_step_s"])
    kind = events.attrs["data_kind"]
    output = args.output or (ROOT/"outputs/synthetic"/args.fixture_case/"tables/gap_events.parquet" if kind == "synthetic"
                             else ROOT/DEFAULT_GAP_EVENTS_PATH)
    write_gap_events(events, output)
    print(f"{len(events)} gap events ({kind}) -> {output}")
    return events


if __name__ == "__main__":
    main()
