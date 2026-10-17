"""C4.03: seeded common-price fixtures with known venue leaders and lags."""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd
from src.synchronization.align import align_previous_tick
from .config import load_config
from .io_utils import file_sha256, write_json, write_parquet
from .schema import validate_frame
from .time_utils import utc_timestamp


def make_fixture(cfg: dict, case="multi") -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Return D1, D2, D3 and truth; all carry synthetic provenance.

    Each venue observes a common random walk delayed by known integer minutes,
    plus independent stationary microstructure noise. The leader has delay zero.
    D1 times are candle availability, not opening times. D2 is simulated and is
    never relabeled as live. Nothing here is an empirical market finding.
    """
    fc = cfg["fixtures"]
    case_cfg = fc[case]
    n = int(fc["n_obs"])
    lags = case_cfg["lags"]
    leader = case_cfg["leader"]
    if n < 30 or leader not in lags or lags[leader] != 0:
        raise ValueError("Fixtures need >=30 rows and a configured zero-lag leader")
    if any(not isinstance(lag, int) or lag < 0 for lag in lags.values()):
        raise ValueError("Fixture lags must be nonnegative integer minutes")
    offset = max(lags.values())
    seed = int(cfg["seed"]) + (0 if case == "multi" else 1)
    rng = np.random.default_rng(seed)
    common = np.log(float(fc["initial_price_usdt"])) + np.cumsum(rng.normal(0, float(fc["innovation_sigma"]), n+offset))
    times = pd.date_range(utc_timestamp(fc["start"]), periods=n, freq=cfg["timeframe"], name="ts_available").as_unit("ns")
    if cfg["timeframe"] != "1min":
        raise ValueError("Fixture lag truth is defined for a one-minute grid")
    candles, quotes = [], []
    for venue, lag in lags.items():
        exchange, market_type = venue.rsplit("_", 1)
        noise = fc["leader_noise_sigma"] if venue == leader else fc["follower_noise_sigma"]
        log_px = common[offset-lag:offset-lag+n] + rng.normal(0, float(noise), n)
        close = np.exp(log_px)
        opening = np.r_[close[0], close[:-1]]
        volume = rng.lognormal(1.5, 0.4, n)
        candles.append(pd.DataFrame({"ts_available": times, "exchange": exchange, "market_type": market_type, "symbol": cfg["symbol"], "open": opening, "high": np.maximum(opening, close)*1.00005, "low": np.minimum(opening, close)*0.99995, "close": close, "volume_base": volume, "volume_quote": close*volume, "data_kind": "synthetic"}))
        half_spread = rng.uniform(0.00001, 0.00004, n)
        bid, ask = close*(1-half_spread), close*(1+half_spread)
        quotes.append(pd.DataFrame({"ts_recv_utc": times, "ts_event_utc": times-pd.Timedelta(milliseconds=20), "exchange": exchange, "market_type": market_type, "symbol": cfg["symbol"], "bid": bid, "ask": ask, "bid_size": volume*0.1, "ask_size": volume*0.12, "mid": close, "spread_abs": ask-bid, "spread_bps": (ask-bid)/close*1e4, "depth_bid_10bps_usdt": volume*close, "depth_ask_10bps_usdt": volume*close*1.1, "data_kind": "synthetic", "depth_censored": False}))
    d1 = pd.concat(candles, ignore_index=True).sort_values(["ts_available", "exchange", "market_type"]).reset_index(drop=True)
    d2 = pd.concat(quotes, ignore_index=True).sort_values(["ts_recv_utc", "exchange", "market_type"]).reset_index(drop=True)
    validate_frame(d1, "D1")
    validate_frame(d2, "D2")
    d3 = align_previous_tick(d1, cfg["timeframe"], cfg["max_age"])
    truth = {"data_kind": "synthetic", "case": case, "seed": seed, "known_leader": leader, "lags_minutes": lags, "expected_cs_is_ils_leader": leader, "n_obs": n, "process": "delayed_common_random_walk_plus_stationary_venue_noise", "warning": "Known lead-lag is DGP truth; IS/CS/ILS must be tested against TV5 code, not assumed."}
    return d1, d2, d3, truth


def write_fixtures(cfg: dict, output_dir="data/fixtures") -> dict:
    """Write small reproducible D1-D3 for multi-venue and spot/perp cases."""
    output = Path(output_dir)
    result = {}
    for case in ("multi", "spot_perp"):
        d1, d2, d3, truth = make_fixture(cfg, case)
        folder = output/case
        for frame, name, schema in [(d1, "ohlcv_1m_long", "D1"), (d2, "live_quotes_long", "D2"), (d3, "panel_1m_wide", "D3")]:
            write_parquet(frame, folder/f"{name}.parquet", schema)
        truth["files_sha256"] = {p.name: file_sha256(p) for p in sorted(folder.glob("*.parquet"))}
        write_json(truth, folder/"truth.json")
        result[case] = truth
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/config.example.yaml"))
    parser.add_argument("--out", type=Path, default=Path("data/fixtures"))
    args = parser.parse_args()
    write_fixtures(load_config(args.config), args.out)


if __name__ == "__main__":
    main()
