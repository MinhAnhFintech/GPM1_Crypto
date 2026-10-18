"""TV6 C6.03 runner: quotes/panel -> compute_gaps -> detect_gap_events -> gap_events.parquet.

Fixtures (default, synthetic, real bid/ask from the live-quotes fixture):
    python -m src.cost_model.run_gap_events
Real historical panel (close-only proxy; needs assumed_half_spread_bps in fees.yaml):
    python -m src.cost_model.run_gap_events --panel data/processed/panel_1m_wide.parquet
Synthetic output goes to outputs/synthetic/<case>/tables/, real output to outputs/tables/.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.utils.config import load_config
from src.utils.io_utils import read_parquet
from src.utils.schema import data_kind as frame_data_kind
from .costs import load_fees
from .gap_events import DEFAULT_GAP_EVENTS_PATH, ROOT, detect_gap_events, write_gap_events
from .pairs import compute_gaps

DEFAULT_FIXTURE_CASE = "multi"


def quotes_to_wide(quotes: pd.DataFrame, grid_step_s: float) -> pd.DataFrame:
    """Pivot D2 long quotes into bid__{venue}/ask__{venue}, indexed by ts_available = ts_recv_utc.

    Time: a live quote is known at ts_recv_utc (DATA_CONTRACT), never ts_event_utc. Only
    on-grid, one-quote-per-venue data (the fixtures) is accepted; irregular real live quotes
    must first be aligned previous-tick by TV4's synchronization, not pivoted here.
    """
    step = pd.Timedelta(seconds=grid_step_s)
    times = quotes["ts_recv_utc"]
    if (times - times.dt.floor(step)).ne(pd.Timedelta(0)).any():
        raise ValueError("Quotes are off the grid; align them previous-tick (TV4) before gap detection")
    venue = quotes["exchange"] + "_" + quotes["market_type"]
    if pd.concat([times, venue], axis=1).duplicated().any():
        raise ValueError("Several quotes per venue and grid time; align them (TV4) before gap detection")
    wide = quotes.assign(venue=venue).pivot(index="ts_recv_utc", columns="venue", values=["bid", "ask"])
    wide.columns = [f"{side}__{v}" for side, v in wide.columns]
    wide.index.name = "ts_available"
    wide.attrs["data_kind"] = frame_data_kind(quotes)
    return wide.sort_index()


def main(argv=None) -> pd.DataFrame:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--quotes", type=Path, help="D2 long quotes (bid/ask)")
    source.add_argument("--panel", type=Path, help="D3 wide panel (close-only proxy)")
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"], default=DEFAULT_FIXTURE_CASE)
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    params = load_config(args.config).get("gap_events")
    if not params or "gap_threshold_bps" not in params or "grid_step_s" not in params:
        raise ValueError(f"{args.config} needs gap_events.gap_threshold_bps and gap_events.grid_step_s")
    fees = load_fees(args.fees)
    if args.panel:
        gaps = compute_gaps(read_parquet(args.panel, "D3"), fees, mode="close")
    else:
        quotes_path = args.quotes or ROOT/"data/fixtures"/args.fixture_case/"live_quotes_long.parquet"
        gaps = compute_gaps(quotes_to_wide(read_parquet(quotes_path, "D2"), params["grid_step_s"]), fees, mode="quotes")
    events = detect_gap_events(gaps, params["gap_threshold_bps"], params["grid_step_s"])
    kind = events.attrs["data_kind"]
    output = args.output or (ROOT/"outputs/synthetic"/args.fixture_case/"tables/gap_events.parquet" if kind == "synthetic"
                             else ROOT/DEFAULT_GAP_EVENTS_PATH)
    write_gap_events(events, output)
    print(f"{len(events)} gap events ({kind}) -> {output}")
    return events


if __name__ == "__main__":
    main()
