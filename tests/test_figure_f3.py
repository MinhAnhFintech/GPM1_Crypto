import numpy as np
import pandas as pd
import pytest
from scripts import make_figure_f3 as f3

VENUES = ["binance_spot", "okx_spot", "bybit_spot", "binance_perp", "okx_perp", "bybit_perp"]


@pytest.fixture
def d6():
    """6 venue x 120 cửa sổ theo giờ; dải rộng và chồng nhau; vài cửa sổ lỗi (status != ok, IS = NaN)."""
    rng = np.random.default_rng(2)
    ends = pd.date_range("2026-03-01", periods=120, freq="60min", tz="UTC").as_unit("ns")
    lock = ends[40]
    rows = []
    for i, end in enumerate(ends):
        for v in VENUES:
            lower = float(rng.uniform(0.0, 0.05)); upper = float(rng.uniform(0.9, 0.99))
            rows.append(dict(window_start=end - pd.Timedelta(minutes=1439), window_end=end, available_at=max(end, lock),
                             venue=v, is_lower=lower, is_upper=upper, is_mid=(lower + upper) / 2,
                             component_share=float(rng.normal(0.16, 0.3)), ils=float(rng.uniform(0.3, 0.7)),
                             leader=None, status="ok", reason=""))
    frame = pd.DataFrame(rows)
    bad = (frame.venue == "okx_perp") & (frame.window_end.isin(ends[70:75]))
    frame.loc[bad, ["status", "reason"]] = ["failed", "singular"]
    return frame


def test_prepare_blanks_failed_windows_without_dropping_rows(d6):
    out = f3.prepare(d6)
    assert len(out) == len(d6)
    bad = out[~out["ok"]]
    assert len(bad) == 5 and bad[["is_lower", "is_upper", "is_mid"]].isna().all().all()


def test_band_summary_counts_ok_and_median_width(d6):
    summary = f3.band_summary(f3.prepare(d6))
    assert summary["okx_perp"]["ok"] == 115 and summary["okx_perp"]["total"] == 120
    assert 0.8 < summary["binance_spot"]["median_width"] < 1.0


def test_lock_time_found_when_published_together(d6):
    frame = f3.prepare(d6)
    assert f3.lock_time(frame) == sorted(frame["window_end"].unique())[40]


def test_prepare_requires_d6_columns(d6):
    with pytest.raises(ValueError, match="thiếu cột"):
        f3.prepare(d6.drop(columns=["is_mid"]))


def test_plot_writes_png(d6, tmp_path):
    out = f3.plot_rolling_is(d6, "synthetic", tmp_path / "F3.png")
    assert out.exists() and out.stat().st_size > 10_000


def test_rolling_median_keeps_index_and_smooths():
    from scripts.make_figure_f3 import rolling_median

    part = pd.DataFrame({"window_end": pd.date_range("2026-01-01", periods=60, freq="h", tz="UTC"),
                         "is_mid": np.tile([0.2, 0.6], 30)})
    out = rolling_median(part, 24)
    assert out.index.equals(part.index)
    assert out.iloc[-1] == 0.4 or abs(out.iloc[-1] - 0.4) < 1e-9