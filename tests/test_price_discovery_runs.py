"""C5.05-C5.09: spec locking, static/pairwise/rolling shares, sensitivity and RQ3."""
import copy
import json
import numpy as np
import pandas as pd
import pytest

from src.price_discovery import analysis as A
from src.price_discovery.run_price_discovery import run
from src.synchronization.split import create_split_manifest
from src.utils.io_utils import write_parquet
from src.utils.schema import validate_frame
from src.utils.synthetic import make_fixture


def _fixture(cfg, case="multi"):
    cfg = copy.deepcopy(cfg)
    cfg["fixtures"]["follower_noise_sigma"] = 3e-4  # identified DGP, see test_vecm_is_cs.py
    _, _, panel, truth = make_fixture(cfg, case)
    return panel, truth


@pytest.fixture(scope="module")
def multi_panel(cfg):
    return _fixture(cfg, "multi")


@pytest.fixture(scope="module")
def spec(multi_panel):
    panel, _ = multi_panel
    return A.select_spec(A.log_prices(panel.iloc[:432]), max_k_ar_diff=4)[0]


def test_log_prices_drops_few_missing_rows_but_never_fills(multi_panel):
    panel, _ = multi_panel
    holed = panel.copy()
    holed.loc[holed.index[5], "log_close__okx_spot"] = np.nan
    logs = A.log_prices(holed, max_missing_frac=0.05)
    assert len(logs) == len(panel) - 1 and logs.notna().all().all()
    with pytest.raises(ValueError, match="missing_prices"):
        A.log_prices(holed.iloc[:10], max_missing_frac=0.05)


def test_select_spec_reports_johansen_and_imposes_one_common_trend(spec):
    assert 0 <= spec["k_ar_diff"] <= 4
    assert spec["coint_rank"] == len(spec["venues"]) - 1
    assert spec["johansen_rank"] == spec["coint_rank"] and spec["rank_matches_theory"]


def test_static_shares_find_leader(multi_panel, spec):
    panel, truth = multi_panel
    shares = A.static_shares(A.log_prices(panel.iloc[:432]), spec).set_index("venue")
    assert shares.is_mid.idxmax() == truth["known_leader"]
    assert shares.component_share.sum() == pytest.approx(1)


def test_pairwise_ils_side_by_side_and_summary(multi_panel, spec):
    panel, truth = multi_panel
    pairs = A.pairwise_ils(A.log_prices(panel.iloc[:432]), spec)
    n = len(spec["venues"])
    assert len(pairs) == n * (n - 1) and pairs.status.eq("ok").all()
    assert {"is_lower", "is_upper", "is_mid", "component_share", "ils"} <= set(pairs.columns)
    assert pairs.groupby("pair").ils.sum().to_numpy() == pytest.approx(np.ones(n * (n - 1) // 2))
    summary = A.summarize_ils(pairs).set_index("venue")
    assert summary.mean_ils.idxmax() == truth["known_leader"]
    assert summary.loc[truth["known_leader"], "n_pairs_led"] == n - 1


def test_leader_left_empty_when_bounds_overlap():
    shares = pd.DataFrame({"is_lower": [0.3, 0.2], "is_upper": [0.8, 0.7]}, index=["a_spot", "b_spot"])
    assert A._leader(shares) is None


def test_rolling_is_matches_d6_and_finds_leader(multi_panel, spec):
    panel, truth = multi_panel
    rolled = A.rolling_shares(panel.iloc[:576], spec, window_obs=240, step_obs=60)
    validate_frame(rolled, "D6")
    assert rolled.window_start.nunique() == (576 - 240) // 60 + 1
    assert (rolled.available_at >= rolled.window_end).all()
    assert rolled[rolled.status.eq("ok")].leader.eq(truth["known_leader"]).all()


def test_rolling_window_uses_no_future_rows(multi_panel, spec):
    panel, _ = multi_panel
    sample = panel.iloc[:400]
    first = A.rolling_shares(sample, spec, 240, 160)
    changed = sample.copy()
    future = changed.index[240:]
    changed.loc[future, changed.columns.str.startswith("log_close__")] += 0.05
    again = A.rolling_shares(changed, spec, 240, 160)
    window = first.window_end.eq(sample.index[239])
    pd.testing.assert_frame_equal(first[window].reset_index(drop=True), again[window].reset_index(drop=True))


def test_rolling_failed_window_keeps_reason(multi_panel, spec):
    panel, _ = multi_panel
    holed = panel.iloc[:300].copy()
    holed.iloc[:60, holed.columns.get_loc("log_close__bybit_spot")] = np.nan
    rolled = A.rolling_shares(holed, spec, 240, 60)
    failed = rolled[rolled.window_start.eq(holed.index[0])]
    assert failed.status.eq("failed").all() and failed.reason.str.startswith("missing_prices").all()
    validate_frame(rolled, "D6")


def test_sensitivity_changes_one_setting_at_a_time(multi_panel, spec):
    panel, _ = multi_panel
    table = A.sensitivity(panel.iloc[:576], spec, window_obs=240, step_obs=120)
    settings = table[["window_obs", "step_obs", "k_ar_diff"]].drop_duplicates()
    baseline = (240, 120, spec["k_ar_diff"])
    assert table[table.is_baseline][["window_obs", "step_obs", "k_ar_diff"]].drop_duplicates().apply(tuple, axis=1).tolist() == [baseline]
    assert (settings.ne(pd.Series(dict(zip(["window_obs", "step_obs", "k_ar_diff"], baseline)))).sum(axis=1) <= 1).all()


def test_spot_vs_perp_pairs_only_same_exchange():
    assert A.spot_perp_pairs(["binance_spot", "binance_perp", "okx_spot", "bybit_perp"]) == [("binance_spot", "binance_perp")]


def test_spot_vs_perp_finds_perp_leader(cfg):
    panel, truth = _fixture(cfg, "spot_perp")
    logs = A.log_prices(panel.iloc[:432])
    spec = A.select_spec(logs, max_k_ar_diff=4)[0]
    out = A.spot_vs_perp(logs, spec, "dev").iloc[0]
    assert truth["known_leader"] == "binance_perp"
    assert out.leader == "binance_perp" and out.conclusion == "perp_leads"


def test_runner_writes_tables_locks_spec_and_never_reads_test(cfg, multi_panel, tmp_path):
    panel, _ = multi_panel
    data, out = tmp_path/"data", tmp_path/"out"
    manifest = create_split_manifest(panel, cfg["split_ratios"], path=data/"split_manifest.json")
    write_parquet(panel, data/"panel_1m_wide.parquet", "D3")
    run(data, out, cfg, window_obs=240, step_obs=60)
    tables = out/"tables"
    for name in ["T2_stationarity_dev.csv", "T2_johansen_val.csv", "T3_static_is_cs.csv", "T3d_ils.csv",
                 "T3d_ils_summary.csv", "T3b_sensitivity.csv", "T3c_spot_perp.csv"]:
        assert not pd.read_csv(tables/name).empty, name
    validate_frame(pd.read_parquet(tables/"rolling_is.parquet"), "D6")
    assert pd.read_parquet(tables/"rolling_is.parquet").window_end.max() <= pd.Timestamp(manifest["val_end"])
    spec = json.loads((tables/"T2_vecm_spec.json").read_text(encoding="utf-8"))
    assert spec["selected_on"] == "dev" and spec["panel_fingerprint"] == manifest["fingerprint"]
    (tables/"T2_vecm_spec.json").write_text(json.dumps({**spec, "k_ar_diff": 3}), encoding="utf-8")
    assert run(data, out, cfg, window_obs=240, step_obs=60)["spec"]["k_ar_diff"] == 3
    assert not json.loads((data/"split_manifest.json").read_text(encoding="utf-8"))["test_consumed"]
    assert not list(data.glob("*.test.lock"))


def test_runner_rejects_spec_locked_on_other_panel(cfg, multi_panel, tmp_path):
    panel, _ = multi_panel
    create_split_manifest(panel, cfg["split_ratios"], path=tmp_path/"split_manifest.json")
    write_parquet(panel, tmp_path/"panel_1m_wide.parquet", "D3")
    (tmp_path/"out/tables").mkdir(parents=True)
    (tmp_path/"out/tables/T2_vecm_spec.json").write_text(json.dumps({"panel_fingerprint": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="different panel"):
        run(tmp_path, tmp_path/"out", cfg, window_obs=240, step_obs=60)
