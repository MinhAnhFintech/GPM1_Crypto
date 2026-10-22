"""Build and validate the D3b 1-minute feature handoff from a D3 panel."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.features_1m import build_features_1m
from src.utils.io_utils import read_parquet, write_parquet
from src.utils.schema import data_kind, validate_frame
from src.utils.pipeline_stages import validate_output_location


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=ROOT / "data/processed/panel_1m_wide.parquet")
    parser.add_argument("--out", type=Path, help="Output Parquet (default depends on panel provenance)")
    parser.add_argument("--rv-window", type=int, default=60)
    parser.add_argument("--rv-min-periods", type=int, default=60)
    args = parser.parse_args(argv)

    panel = read_parquet(args.panel, "D3")
    kind = data_kind(panel)
    features = build_features_1m(panel, args.rv_window, args.rv_min_periods)
    validate_frame(features, "D3b")
    default_out = (
        ROOT / "outputs/synthetic/features_1m.parquet"
        if kind == "synthetic"
        else ROOT / "data/processed/features_1m.parquet"
    )
    output_path = args.out or default_out
    mode = "synthetic" if kind == "synthetic" else "historical"
    validate_output_location(output_path, root=ROOT, mode=mode)
    write_parquet(features, output_path, "D3b")

    print(
        f"Built {output_path}: {len(features):,} rows, {len(features.columns)} columns, "
        f"data_kind={kind}"
    )


if __name__ == "__main__":
    main()
