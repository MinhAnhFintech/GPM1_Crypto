"""Matplotlib helpers for TV2; preserve missing timestamps in line plots."""
from pathlib import Path
import os
import numpy as np
import pandas as pd


def _pyplot():
    """Use a writable project cache and a noninteractive rendering backend."""
    os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[2]/"tmp/matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def plot_prices(panel, out_path):
    """F1: plot synchronized USDT close prices; perpetual uses dashed lines."""
    plt = _pyplot()
    from src.utils.schema import data_kind, validate_frame, venues_from_panel
    validate_frame(panel, "D3")
    fig, ax = plt.subplots(figsize=(11, 4.5), layout="constrained")
    for venue in venues_from_panel(panel):
        ax.plot(panel.index, panel[f"close__{venue}"], linestyle="--" if venue.endswith("_perp") else "-", linewidth=1, label=venue)
    ax.set(title=f"Synchronized prices | {data_kind(panel)}", xlabel="Availability time (UTC)", ylabel="USDT")
    ax.legend(ncol=2, fontsize=8)
    ax.grid(alpha=0.2)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output


def plot_leadlag(xcorr, out_path, *, data_kind):
    """F6: plot correlation by lag; positive lag means first venue leads."""
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(11, 4.5), layout="constrained")
    for (a, b), group in xcorr.groupby(["venue_a", "venue_b"]):
        ax.plot(group.lag, group.correlation, marker=".", label=f"{a} → {b}")
    ax.set(title=f"Lead-lag cross-correlation | {data_kind}", xlabel="Lag (minutes): positive = first venue leads", ylabel="Correlation", ylim=(-1.05, 1.05))
    ax.axvline(0, color="grey", linewidth=0.7)
    ax.grid(alpha=0.2)
    ax.legend(ncol=2, fontsize=7)
    output = Path(out_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output
