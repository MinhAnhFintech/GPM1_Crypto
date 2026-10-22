"""Generate TV2 C2.05 figures F1 and F2 from a synchronized D3 panel."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from notebooks.templates.plot_template import plot_prices
from src.utils.io_utils import read_parquet
from src.utils.pipeline_stages import validate_output_location
from src.utils.schema import data_kind, validate_frame, venues_from_panel

ROOT = Path(__file__).resolve().parents[2]


def dispersion_bps(panel: pd.DataFrame, market_type: str) -> pd.Series | None:
    """Return cross-venue range / mean in bps for one market type.

    A timestamp is included only when every venue of the requested market type
    has a price. Return None if fewer than two venues are available.
    """
    if market_type not in {"spot", "perp"}:
        raise ValueError("market_type must be 'spot' or 'perp'")

    venues = [v for v in venues_from_panel(panel) if v.endswith(f"_{market_type}")]
    if len(venues) < 2:
        return None

    prices = panel[[f"close__{venue}" for venue in venues]]
    dispersion = (prices.max(axis=1) - prices.min(axis=1)) / prices.mean(axis=1) * 10_000
    return dispersion.where(prices.notna().all(axis=1)).rename(f"dispersion_bps_{market_type}")


def plot_dispersion_bps(panel: pd.DataFrame, out_path: Path) -> Path:
    """F2: plot spot and perp cross-venue price dispersion in bps."""
    import matplotlib.pyplot as plt

    validate_frame(panel, "D3")
    kind = data_kind(panel)
    fig, axes = plt.subplots(2, 1, figsize=(11, 7), sharex=True, layout="constrained")
    plotted = False
    for ax, (market_type, linestyle) in zip(axes, (("spot", "-"), ("perp", "--"))):
        series = dispersion_bps(panel, market_type)
        if series is None:
            ax.text(0.5, 0.5, f"Fewer than two {market_type} venues", ha="center", va="center",
                    transform=ax.transAxes)
            continue
        ax.plot(panel.index, series, linestyle=linestyle, linewidth=1, label=market_type)
        ax.legend(title="Market type")
        ax.grid(alpha=0.2)
        plotted = True

    if not plotted:
        plt.close(fig)
        raise ValueError("F2 requires at least two venues in spot or perp")

    axes[0].set_title(f"Cross-exchange price dispersion | {kind}")
    axes[0].set_ylabel("Spot dispersion (bps)")
    axes[1].set_ylabel("Perpetual dispersion (bps)")
    axes[1].set_xlabel("Availability time (UTC)")
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel", type=Path, default=ROOT / "data/processed/panel_1m_wide.parquet")
    parser.add_argument("--out-dir", type=Path, help="Figure directory; default depends on panel provenance")
    args = parser.parse_args(argv)

    panel = read_parquet(args.panel, "D3")
    kind = data_kind(panel)
    output_dir = args.out_dir or (
        ROOT / "outputs/synthetic/figures" if kind == "synthetic" else ROOT / "outputs/figures"
    )
    validate_output_location(
        output_dir,
        root=ROOT,
        mode="synthetic" if kind == "synthetic" else "historical",
    )
    f1 = plot_prices(panel, output_dir / "F1_synchronized_prices.png")
    f2 = plot_dispersion_bps(panel, output_dir / "F2_price_dispersion.png")
    print(f"Generated {f1} and {f2} from {kind} panel ({len(panel):,} rows)")


if __name__ == "__main__":
    main()
