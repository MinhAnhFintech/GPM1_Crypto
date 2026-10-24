"""C6.07: T5b cost sensitivity. Grid mechanics, and the one-time final-test claim: preflight before the claim,
frozen inputs checked, a second evaluation refused, the validation preview never locks anything."""
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from src.backtest import run_t5b
from src.backtest.sensitivity import scale_fees, validate_frozen
from src.cost_model.costs import FeeSchedule
from src.synchronization.split import create_split_manifest
from src.utils.io_utils import file_sha256

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT/"data/fixtures/multi"
FEES_YAML = """binance:
  spot: {taker_bps: 10.0}
  perp: {taker_bps: 5.0}
okx:
  spot: {taker_bps: 10.0}
bybit:
  spot: {taker_bps: 10.0}
defaults: {slippage_bps: 0.0, extra_cost_bps: 0.0}
"""


def setup(tmp_path, *, b_latency=(5, 10), fees_hash=None):
    panel = pd.read_parquet(FIX/"panel_1m_wide.parquet")
    (tmp_path/"panel.parquet").write_bytes((FIX/"panel_1m_wide.parquet").read_bytes())
    manifest = create_split_manifest(panel, path=tmp_path/"split_manifest.json")
    (tmp_path/"fees.yaml").write_text(FEES_YAML, encoding="utf-8")
    frozen = {"inputs": {"fees_sha256": fees_hash or file_sha256(tmp_path/"fees.yaml"),
                         "live_quotes_sha256": file_sha256(FIX/"live_quotes_long.parquet"), "panel_fingerprint": manifest["fingerprint"]},
              "base": {"entry_threshold_bps": 0.0, "fee_type": "taker", "half_spread_statistic": "median", "live_grid_step_s": 5,
                       "live_max_age_s": 6, "base_latency_min": 1, "base_latency_s": 5, "base_qty_btc": 0.1},
              "grid": {"fee_multipliers": [1.0, 0.0], "backtest_a": {"latency_min": [1, 2], "slippage_bps": [0.0, 1.0], "qty_btc": 0.1},
                       "backtest_b": {"latency_s": list(b_latency), "qty_btc": [0.1], "require_top_size": True}}}
    (tmp_path/"frozen.yaml").write_text(json.dumps(frozen), encoding="utf-8")       # JSON is valid YAML
    return ["--frozen", str(tmp_path/"frozen.yaml"), "--panel", str(tmp_path/"panel.parquet"),
            "--manifest", str(tmp_path/"split_manifest.json"), "--live-quotes", str(FIX/"live_quotes_long.parquet"),
            "--fees", str(tmp_path/"fees.yaml"), "--output-dir", str(tmp_path/"out")]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lock_of(tmp_path):
    return tmp_path/"split_manifest.json.test.lock"


def test_scale_fees_and_frozen_validation():
    fees = FeeSchedule(venues={"binance_spot": {"taker_bps": 10.0, "maker_bps": 2.0}})
    assert scale_fees(fees, 0.5).fee_bps("binance_spot") == 5.0 and scale_fees(fees, 0.0).fee_bps("binance_spot") == 0.0
    with pytest.raises(ValueError, match="lacks"):
        validate_frozen({"base": {}, "grid": {}})


def test_validation_preview_never_locks_the_test(tmp_path):
    args = setup(tmp_path)
    before = digest(tmp_path/"split_manifest.json")
    table = run_t5b.main(args + ["--split", "val"])
    assert digest(tmp_path/"split_manifest.json") == before and not lock_of(tmp_path).exists()
    assert set(table[table.backtest.str.startswith("A")]["sample"]) == {"val"}
    assert len(table) == (2*2*2 + 2*2*1)*2                            # (A: fees x latency x slippage + B: fees x latency x size) x spot/perp
    assert table.is_base_scenario.sum() == 4                           # one base scenario per backtest and group
    assert (tmp_path/"out/T5b_cost_sensitivity_val_preview.csv").is_file()


def test_lower_fees_never_give_fewer_signals(tmp_path):
    table = run_t5b.main(setup(tmp_path) + ["--split", "val"])
    a = table[table.backtest.str.startswith("A") & (table.slippage_bps == 0)]
    for (market, latency), group in a.groupby(["market_type", "latency"]):
        by_fee = group.set_index("fee_multiplier").n_signals
        assert by_fee[0.0] >= by_fee[1.0]


def test_final_test_is_claimed_exactly_once(tmp_path):
    args = setup(tmp_path)
    table = run_t5b.main(args + ["--split", "test"])
    manifest = json.loads((tmp_path/"split_manifest.json").read_text(encoding="utf-8"))
    assert manifest["test_consumed"] is True and lock_of(tmp_path).exists()
    assert manifest["frozen_config_sha256"] == file_sha256(tmp_path/"frozen.yaml")
    assert set(table[table.backtest.str.startswith("A")]["sample"]) == {"test"}
    sources = json.loads((tmp_path/"out/T5b_cost_sensitivity_sources.json").read_text(encoding="utf-8"))
    assert sources["test_consumed_after_run"] is True and sources["frozen_settings_sha256"] == file_sha256(tmp_path/"frozen.yaml")
    with pytest.raises(ValueError, match="already been consumed"):
        run_t5b.main(args + ["--split", "test"])


def test_a_failing_preflight_does_not_consume_the_test(tmp_path):
    args = setup(tmp_path, b_latency=(7,))                             # 7 s is not a multiple of the 5 s live grid
    before = digest(tmp_path/"split_manifest.json")
    with pytest.raises(ValueError, match="multiple"):
        run_t5b.main(args + ["--split", "test"])
    assert digest(tmp_path/"split_manifest.json") == before and not lock_of(tmp_path).exists()


def test_inputs_that_differ_from_the_frozen_file_are_refused_before_the_claim(tmp_path):
    args = setup(tmp_path, fees_hash="0"*64)
    with pytest.raises(ValueError, match="differ from the frozen"):
        run_t5b.main(args + ["--split", "test"])
    assert not lock_of(tmp_path).exists()


def test_a_bad_destination_is_refused_before_the_claim(tmp_path):
    args = setup(tmp_path)
    args[args.index("--output-dir") + 1] = str(ROOT/"outputs/tables/probe_t5b")      # synthetic into the research zone
    with pytest.raises(ValueError, match="Synthetic"):
        run_t5b.main(args + ["--split", "test"])
    assert not lock_of(tmp_path).exists() and not (ROOT/"outputs/tables/probe_t5b").exists()
