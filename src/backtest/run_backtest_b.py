"""TV6 C6.05 runner: live D2 -> aligned 5 s quotes -> backtest B -> trades ledger (D8) and summaries.

Real live sample (writes outputs/tables/trades_ledger.parquet for the configured latency):
    python -m src.backtest.run_backtest_b
    python -m src.backtest.run_backtest_b --latency-s 10          # sensitivity run, separate file names
    python -m src.backtest.run_backtest_b --market-type spot      # one contract group only
Fixtures (synthetic D2 on a 1-minute grid, outputs under outputs/synthetic/):
    python -m src.backtest.run_backtest_b --fixture-case multi --latency-s 60
Alignment uses the live keys of the gap_events config section (live_grid_step_s, live_max_age_s), so gap
events and backtest B read the live sample identically. Spot and perp are always reported separately.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.cost_model.costs import load_fees
from src.cost_model.run_gap_events import quotes_to_wide
from src.utils.config import load_config
from src.utils.io_utils import file_sha256, read_parquet, write_json, write_parquet
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind
from .engine_live import LiveBacktestConfig, market_summary, run_backtest_live, session_windows

ROOT = Path(__file__).resolve().parents[2]
CONFIG_KEY = "backtest_b"
OFFICIAL_LEDGER = "trades_ledger.parquet"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quotes", type=Path, help="D2 live quotes (default data/processed/live_quotes_long.parquet)")
    parser.add_argument("--fixture-case", choices=["multi", "spot_perp"])
    parser.add_argument("--market-type", choices=["spot", "perp", "all"], default="all")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.yaml")
    parser.add_argument("--fees", type=Path, default=ROOT/"config/fees.yaml")
    parser.add_argument("--latency-s", type=float, help="overrides backtest_b.latency_s")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    cfg_all = load_config(args.config)
    params, align = cfg_all.get(CONFIG_KEY), cfg_all.get("gap_events") or {}
    if not params:
        raise ValueError(f"{args.config} needs a {CONFIG_KEY} section")
    fixture = ROOT/"data/fixtures"/args.fixture_case if args.fixture_case else None
    quotes_path = args.quotes or (fixture/"live_quotes_long.parquet" if fixture else ROOT/"data/processed/live_quotes_long.parquet")
    quotes = read_parquet(quotes_path, "D2")
    kind = data_kind(quotes)
    if kind not in {"live", "synthetic"}:
        raise ValueError(f"Backtest B needs live (or synthetic fixture) quotes, got {kind}")
    if kind == "live":
        grid_step_s, max_age_s = float(align["live_grid_step_s"]), float(align["live_max_age_s"])
    else:                                                   # fixtures sit exactly on the 1-minute grid
        grid_step_s, max_age_s = float(align.get("grid_step_s", 60)), None
    latency_s = float(args.latency_s if args.latency_s is not None else params["latency_s"])

    # Destination check before any computation or write (shared TV4 provenance rule, sub-folders and '..' resolved).
    output = Path(args.output_dir or (ROOT/"outputs/synthetic"/(args.fixture_case or "custom")/"tables" if kind == "synthetic"
                                      else ROOT/"outputs/tables"))
    base = (kind == "live" and args.market_type == "all" and latency_s == float(params["latency_s"]))
    stem = f"backtest_b_L{latency_s:g}s" + ("" if args.market_type == "all" else f"_{args.market_type}")
    ledger_path = output/(OFFICIAL_LEDGER if base else f"{stem}_ledger.parquet")
    pair_path, market_path, params_path = output/f"{stem}_summary.csv", output/f"{stem}_by_market.csv", output/f"{stem}_params.json"
    for destination in (output, ledger_path, pair_path, market_path, params_path):
        validate_output_location(destination, root=ROOT, mode="synthetic" if kind == "synthetic" else "historical")

    if args.market_type != "all":
        quotes = quotes[quotes["market_type"] == args.market_type]
        if quotes.empty:
            raise ValueError(f"No {args.market_type} quotes in {quotes_path}")
    wide = quotes_to_wide(quotes, grid_step_s, max_age_s)
    cfg = LiveBacktestConfig(fees=load_fees(args.fees), grid_step_s=grid_step_s, latency_s=latency_s,
                             entry_threshold_bps=float(params["entry_threshold_bps"]), qty_btc=float(params["qty_btc"]),
                             fee_type=params.get("fee_type", "taker"), require_top_size=bool(params.get("require_top_size", True)))
    result = run_backtest_live(wide, cfg, session_windows(quotes))
    by_market = market_summary(result.ledger)

    write_parquet(result.ledger, ledger_path, "D8" if len(result.ledger) else None)
    result.summary.to_csv(pair_path, index=False)
    by_market.to_csv(market_path, index=False)
    write_json(dict(result.params, market_type=args.market_type, max_age_s=max_age_s, quotes=str(quotes_path),
                    quotes_sha256=file_sha256(quotes_path), ledger=ledger_path.name,
                    note="Backtest B uses real top-of-book bid/ask of the live sample; latency is simulated by filling later."),
               params_path)
    filled = result.ledger[result.ledger["status"] == "filled"]
    print(f"{stem}: {len(result.ledger)} signals, {len(filled)} filled, {int((filled['net_bps'] > 0).sum())} net>0, "
          f"PnL {filled['pnl_usdt'].sum():.2f} USDT -> {ledger_path}")
    return result


if __name__ == "__main__":
    main()
