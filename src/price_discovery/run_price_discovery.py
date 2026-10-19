"""TV5 runner (C5.05-C5.09): D3 + D4 -> T2/T3 tables and D6 rolling_is.parquet.

Synthetic, after `python run_all.py --mode synthetic` (small windows: 720-row fixture):
    python -m src.price_discovery.run_price_discovery --data-dir outputs/synthetic/multi/data --output-dir outputs/synthetic/multi --window-obs 240 --step-obs 60
Real data (pipeline stage "price_discovery"):
    python -m src.price_discovery.run_price_discovery --data-dir {data_dir} --output-dir {output_dir} --config config/config.yaml

The spec (lag by BIC, Johansen rank for the system and each pair) is chosen on
dev only, written to T2_vecm_spec.json and reused on later runs (locked).
Validation reuses it; the final test is never read.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from src.synchronization.split import select_split
from src.utils.config import load_config
from src.utils.io_utils import read_parquet, write_json, write_parquet
from src.utils.schema import data_kind, validate_split_manifest
from . import analysis as A

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {"deterministic": "co", "criterion": "bic", "max_k_ar_diff": 10, "significance": 95, "max_missing_frac": 0.05}


def locked_spec(path: Path, dev_logs: pd.DataFrame, manifest: dict, kind: str, params: dict) -> dict:
    """Reuse an existing spec for the same panel; otherwise select it on dev and lock it."""
    if path.exists():
        spec = json.loads(path.read_text(encoding="utf-8"))
        if spec.get("panel_fingerprint") != manifest["fingerprint"]:
            raise ValueError(f"{path} was locked on a different panel; use a new output directory")
        if spec.get("spec_version") != A.SPEC_VERSION:
            raise ValueError(f"{path} uses an old spec format; delete it to re-select on dev")
        return spec
    spec, _ = A.select_spec(dev_logs, params["max_k_ar_diff"], params["deterministic"],
                            params["criterion"], params["significance"], params["seed"])
    spec.update(selected_on="dev", panel_fingerprint=manifest["fingerprint"], data_kind=kind,
                max_missing_frac=params["max_missing_frac"])
    write_json(spec, path)
    return spec


def run(data_dir: Path, output_dir: Path, cfg: dict, window_obs=None, step_obs=None) -> dict:
    panel = read_parquet(data_dir/"panel_1m_wide.parquet", "D3")
    manifest = validate_split_manifest(json.loads((data_dir/"split_manifest.json").read_text(encoding="utf-8")))
    kind = data_kind(panel)
    if kind == "synthetic" and output_dir.resolve() == (ROOT/"outputs").resolve():
        raise ValueError("Keep synthetic outputs in a separate subdirectory")
    params = {**DEFAULTS, "seed": cfg.get("seed"), **(cfg.get("price_discovery") or {})}
    window_obs = int(window_obs or cfg["rolling"]["window_obs"])
    step_obs = int(step_obs or cfg["rolling"]["step_obs"])
    tables = output_dir/"tables"
    tables.mkdir(parents=True, exist_ok=True)
    frac = params["max_missing_frac"]

    samples = {name: select_split(panel, manifest, name) for name in ("dev", "val")}
    logs = {name: A.log_prices(sample, max_missing_frac=frac) for name, sample in samples.items()}
    spec = locked_spec(tables/"T2_vecm_spec.json", logs["dev"], manifest, kind, params)

    static, diagnostics, pairs, rq3 = [], [], [], []
    for name, sample_logs in logs.items():
        A.stationarity_table(sample_logs).assign(split=name).to_csv(tables/f"T2_stationarity_{name}.csv", index=False)
        _, joh = A.johansen_table(sample_logs, spec["det_order"], spec["k_ar_diff"], spec["significance"])
        joh.assign(locked_rank=spec["coint_rank"], split=name).to_csv(tables/f"T2_johansen_{name}.csv", index=False)
        static.append(A.static_shares(sample_logs, spec).assign(split=name))
        diagnostics.append(A.diagnostics_table(sample_logs, spec).assign(split=name))
        pairs.append(A.pairwise_ils(sample_logs, spec).assign(split=name))
        rq3.append(A.spot_vs_perp(sample_logs, spec, name))
    pd.concat(static).to_csv(tables/"T3_static_is_cs.csv", index=False)
    pd.concat(diagnostics).to_csv(tables/"T3_vecm_diagnostics.csv", index=False)
    pairs = pd.concat(pairs)
    pairs.to_csv(tables/"T3d_ils.csv", index=False)
    pd.concat(A.summarize_ils(g).assign(split=s) for s, g in pairs.groupby("split", sort=False)
              ).to_csv(tables/"T3d_ils_summary.csv", index=False)
    pd.concat(rq3).to_csv(tables/"T3c_spot_perp.csv", index=False)

    rolling_sample = pd.concat([samples["dev"], samples["val"]])
    rolling = A.rolling_shares(rolling_sample, spec, window_obs, step_obs, frac)
    rolling.attrs.update(data_kind=kind, window_obs=window_obs, step_obs=step_obs, k_ar_diff=spec["k_ar_diff"])
    write_parquet(rolling, tables/"rolling_is.parquet", "D6")
    A.sensitivity(rolling_sample, spec, window_obs, step_obs, max_missing_frac=frac
                  ).to_csv(tables/"T3b_sensitivity.csv", index=False)
    return {"spec": spec, "n_windows": int(rolling.window_start.nunique()),
            "n_failed_windows": int(rolling[rolling.status.ne("ok")].window_start.nunique())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=ROOT/"data/processed")
    parser.add_argument("--output-dir", type=Path, default=ROOT/"outputs")
    parser.add_argument("--config", type=Path, default=ROOT/"config/config.example.yaml")
    parser.add_argument("--window-obs", type=int, help="Override rolling.window_obs (grid rows)")
    parser.add_argument("--step-obs", type=int, help="Override rolling.step_obs (grid rows)")
    args = parser.parse_args(argv)
    summary = run(args.data_dir, args.output_dir, load_config(args.config), args.window_obs, args.step_obs)
    print(f"price discovery: k_ar_diff={summary['spec']['k_ar_diff']}, johansen_rank={summary['spec']['coint_rank']} "
          f"(system_supported={summary['spec']['system_supported']}), "
          f"{summary['n_windows']} windows ({summary['n_failed_windows']} failed) -> {args.output_dir/'tables'}")
    return summary


if __name__ == "__main__":
    main()
