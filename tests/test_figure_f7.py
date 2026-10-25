import io
import json

import pandas as pd
import pytest

from scripts import make_figure_f7 as f7

CSV = """split,exchange,venue_spot,venue_perp,n_obs,k_ar_diff,is_lower_spot,is_upper_spot,is_mid_spot,component_share_spot,ils_spot,is_lower_perp,is_upper_perp,is_mid_perp,component_share_perp,ils_perp,leader,conclusion,status,reason
dev,okx,okx_spot,okx_perp,10,10,0.0,1.0,0.5,0.5,0.44,0.0,1.0,0.5,0.5,0.56,,chua_phan_biet_duoc,ok,
val,okx,okx_spot,okx_perp,10,10,0.0,1.0,0.5,0.8,0.03,0.0,1.0,0.5,0.2,0.97,,chua_phan_biet_duoc,ok,
dev,bybit,bybit_spot,bybit_perp,10,10,0.0,1.0,0.5,0.5,0.01,0.0,1.0,0.5,0.5,0.99,,chua_phan_biet_duoc,ok,
val,bybit,bybit_spot,bybit_perp,10,10,0.0,1.0,0.5,0.5,0.24,0.0,1.0,0.5,0.5,0.76,,chua_phan_biet_duoc,ok,
"""


@pytest.fixture
def frame():
    df = pd.read_csv(io.StringIO(CSV))
    df["data_kind"] = "historical"
    return df


def test_ils_flip_detects_sign_change(frame):
    flips = f7.ils_flip(frame)
    assert flips["okx"]["flip"] is False  # 0.44 và 0.03 đều dưới 0.5
    assert flips["bybit"]["flip"] is False
    frame.loc[(frame.exchange == "okx") & (frame.split == "val"), "ils_spot"] = 0.9
    assert f7.ils_flip(frame)["okx"]["flip"] is True


def test_overlap(frame):
    assert f7.overlap(frame.iloc[0]) is True
    row = frame.iloc[0].copy()
    row["is_upper_spot"], row["is_lower_perp"] = 0.2, 0.6
    assert f7.overlap(row) is False


def test_load_requires_columns(tmp_path, frame):
    path = tmp_path / "t.csv"
    frame.drop(columns=["ils_spot"]).to_csv(path, index=False)
    with pytest.raises(ValueError, match="thiếu cột"):
        f7.load_t3c(path)


def test_synthetic_source_cannot_overwrite_historical_figure(tmp_path, frame, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(f7, "ROOT", root)
    source = root / "outputs" / "synthetic" / "tables" / "T3c_spot_perp.csv"
    source.parent.mkdir(parents=True)
    frame["data_kind"] = "synthetic"
    frame.to_csv(source, index=False)
    old_spec = source.parent / "T2_vecm_spec.json"
    old_spec.write_text(json.dumps({"data_kind": "synthetic"}), encoding="utf-8")

    historical_output = root / "outputs" / "figures" / "F7_spot_perp_is_ils.png"
    historical_output.parent.mkdir(parents=True)
    previous_contents = b"existing historical figure"
    historical_output.write_bytes(previous_contents)

    with pytest.raises(ValueError, match="Synthetic outputs belong"):
        f7.main(["--t3c", str(source), "--out", str(historical_output)])
    assert historical_output.read_bytes() == previous_contents


def test_load_rejects_conflicting_t3c_and_spec_provenance(tmp_path, frame):
    path = tmp_path / "T3c_spot_perp.csv"
    frame.to_csv(path, index=False)
    (tmp_path / "T2_vecm_spec.json").write_text(json.dumps({"data_kind": "synthetic"}), encoding="utf-8")
    with pytest.raises(ValueError, match="không khớp"):
        f7.load_t3c(path)


def test_legacy_t3c_uses_standard_historical_output_location(tmp_path, frame, monkeypatch):
    monkeypatch.setattr(f7, "ROOT", tmp_path)
    path = tmp_path / "outputs" / "tables" / "T3c_spot_perp.csv"
    path.parent.mkdir(parents=True)
    frame.drop(columns=["data_kind"]).to_csv(path, index=False)
    assert f7.load_t3c(path).attrs["data_kind"] == "historical"


def test_png_written(tmp_path, frame):
    out = f7.plot_spot_perp(frame, tmp_path / "f7.png")
    assert out.exists() and out.stat().st_size > 5000