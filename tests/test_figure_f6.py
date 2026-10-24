import numpy as np
import pandas as pd
import pytest
from scripts import make_figure_f6 as f6
from src.features.features_1m import build_features_1m
from src.price_discovery.leadlag import lead_lag_xcorr
from src.synchronization.split import create_split_manifest
from src.utils.io_utils import write_parquet, write_json

VENUES = ["binance_spot", "okx_spot", "bybit_spot", "binance_perp", "okx_perp", "bybit_perp"]


@pytest.fixture
def returns():
    """Mỗi perp = spot cùng sàn trễ 1 phút, nên spot dẫn perp 1 phút (lag +1 khi spot đứng trước)."""
    rng = np.random.default_rng(1)
    n = 3000
    index = pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC").as_unit("ns").rename("ts_available")
    base = rng.normal(0, 1e-3, n)
    data = {}
    for exchange in ("binance", "okx", "bybit"):
        data[f"ret_1m__{exchange}_spot"] = base + rng.normal(0, 2e-4, n)
        data[f"ret_1m__{exchange}_perp"] = np.r_[0.0, base[:-1]] + rng.normal(0, 2e-4, n)
    frame = pd.DataFrame(data, index=index)
    frame.iloc[100:110, 0] = np.nan   # thiếu giữ NaN, không làm hỏng hình
    return frame[[f"ret_1m__{v}" for v in reversed(VENUES)]]   # cố ý xếp cột ngược để thử đổi chiều cặp


def test_pairs_are_oriented_spot_first_and_sign_flips(returns):
    oriented = f6.orient_pairs(lead_lag_xcorr(returns, 3))
    pair = oriented[(oriented.venue_a == "binance_spot") & (oriented.venue_b == "binance_perp")]
    assert len(pair) == 7
    assert f6.peak_lag(pair) == 1          # spot dẫn perp 1 phút


def test_select_pairs_has_three_groups_of_three(returns):
    groups = f6.select_pairs(f6.orient_pairs(lead_lag_xcorr(returns, 2)))
    assert [len(p) for _, p in groups] == [3, 3, 3]
    assert all(a.endswith("_spot") and b.endswith("_perp") and a.split("_")[0] == b.split("_")[0] for a, b in groups[0][1])


def test_plot_writes_png(returns, tmp_path):
    out = f6.plot_lead_lag(lead_lag_xcorr(returns, 5), "synthetic", "Sample: test", tmp_path / "F6.png")
    assert out.exists() and out.stat().st_size > 10_000


def test_peak_lag_none_when_all_nan():
    assert f6.peak_lag(pd.DataFrame({"lag": [-1, 0, 1], "correlation": [np.nan] * 3})) is None


def test_lag_summary_reports_contemporaneous_and_asymmetry(returns):
    oriented = f6.orient_pairs(lead_lag_xcorr(returns, 3))
    pair = oriented[(oriented.venue_a == "binance_spot") & (oriented.venue_b == "binance_perp")]
    text = f6.lag_summary(pair)
    assert "rho(0)" in text and "rho(+1) - rho(-1) = +" in text   # spot dẫn perp -> bất đối xứng dương


def _write_cli_inputs(root, n=30):
    index = pd.date_range("2026-01-01", periods=n, freq="min", tz="UTC").as_unit("ns").rename("ts_available")
    rng = np.random.default_rng(17)
    panel = pd.DataFrame(index=index)
    for i, venue in enumerate(VENUES):
        returns = rng.normal(0, 1e-3, n)
        prices = np.exp(10 + np.cumsum(returns + i * 1e-6))
        panel[f"close__{venue}"] = prices
        panel[f"log_close__{venue}"] = np.log(prices)
        panel[f"volume_base__{venue}"] = 1.0
        panel[f"missing__{venue}"] = False
    panel.attrs.update(data_kind="synthetic", contract_version="1.0")

    data_dir = root / "data/processed"
    panel_path = data_dir / "panel_1m_wide.parquet"
    features_path = data_dir / "features_1m.parquet"
    manifest_path = data_dir / "split_manifest.json"
    write_parquet(panel, panel_path, "D3")
    write_parquet(build_features_1m(panel), features_path, "D3b")
    manifest = create_split_manifest(panel)
    write_json(manifest, manifest_path)
    return panel, panel_path, features_path, manifest_path, manifest


def test_cli_defaults_to_development_split_from_d4(tmp_path, monkeypatch):
    panel, panel_path, features_path, manifest_path, manifest = _write_cli_inputs(tmp_path)
    captured = {}
    monkeypatch.setattr(f6, "ROOT", tmp_path)

    def capture_plot(xcorr, kind, sample, path):
        captured.update(xcorr=xcorr, kind=kind, sample=sample, path=path)
        return path

    monkeypatch.setattr(f6, "plot_lead_lag", capture_plot)
    f6.main([
        "--features", str(features_path),
        "--panel", str(panel_path),
        "--split-manifest", str(manifest_path),
        "--max-lag", "2",
    ])

    dev_start = pd.Timestamp(manifest["dev_start"])
    dev_end = pd.Timestamp(manifest["dev_end"])
    assert captured["kind"] == "synthetic"
    assert f"{dev_start:%Y-%m-%d %H:%M}" in captured["sample"]
    assert f"{dev_end:%Y-%m-%d %H:%M}" in captured["sample"]
    assert f"{len(panel) * 3 // 5:,} minutes" in captured["sample"]
    assert captured["path"] == tmp_path / "outputs/synthetic/figures/F6_leadlag_xcorr.png"


def test_synthetic_cli_cannot_write_to_research_figures(tmp_path, monkeypatch):
    _, panel_path, features_path, manifest_path, _ = _write_cli_inputs(tmp_path)
    output_path = tmp_path / "outputs/figures/F6_leadlag_xcorr.png"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"preserve historical figure")
    monkeypatch.setattr(f6, "ROOT", tmp_path)

    with pytest.raises(ValueError, match="Synthetic outputs belong under outputs/synthetic"):
        f6.main([
            "--features", str(features_path),
            "--panel", str(panel_path),
            "--split-manifest", str(manifest_path),
            "--max-lag", "2",
            "--out", str(output_path),
        ])

    assert output_path.read_bytes() == b"preserve historical figure"