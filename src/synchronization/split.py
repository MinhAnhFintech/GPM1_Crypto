"""C4.09: chronological 60/20/20 split with auditable final-test consumption."""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd
from src.utils.io_utils import file_sha256, frame_fingerprint, write_json
from src.utils.schema import data_kind, validate_frame, validate_split_manifest


def create_split_manifest(panel, ratios=(0.6, 0.2, 0.2), *, path=None) -> dict:
    """Split grid rows, including missing rows, without shuffle or future fitting.

    Boundaries are inclusive and disjoint. Exact counts floor dev/val and assign
    the remainder to test. Existing identical manifests keep their test lock.
    """
    validate_frame(panel, "D3")
    ratios = np.asarray(ratios, dtype=float)
    if ratios.shape != (3,) or not np.isfinite(ratios).all() or (ratios <= 0).any() or not np.isclose(ratios.sum(), 1):
        raise ValueError("Three positive split ratios must sum to one")
    n = len(panel)
    a, b = int(n*ratios[0]), int(n*ratios[1])
    if min(a, b, n-a-b) < 1:
        raise ValueError("Not enough grid rows for three nonempty chronological splits")
    index = panel.index
    manifest = {"contract_version": "1.0", "data_kind": data_kind(panel), "fingerprint": frame_fingerprint(panel), "fingerprint_method": "sha256:pandas_hash_values+schema+index+attrs", "n_obs": n, "ratios": ratios.tolist(), "counts": {"dev": a, "val": b, "test": n-a-b}, "test_policy": "single_final_evaluation", "test_consumed": False}
    for name, start, end in [("dev", 0, a-1), ("val", a, a+b-1), ("test", a+b, n-1)]:
        manifest[f"{name}_start"] = index[start].isoformat()
        manifest[f"{name}_end"] = index[end].isoformat()
    if path is not None:
        path = Path(path)
        if path.exists():
            existing = validate_split_manifest(json.loads(path.read_text(encoding="utf-8")))
            if existing["fingerprint"] != manifest["fingerprint"] or existing["ratios"] != manifest["ratios"]:
                raise ValueError("Existing split differs; choose a new version/output directory")
            return existing
        write_json(validate_split_manifest(manifest), path)
    return validate_split_manifest(manifest)


def select_split(panel, manifest, split="dev") -> pd.DataFrame:
    """Select dev/val only; final-test access requires the explicit lock below."""
    validate_split_manifest(manifest)
    if split not in {"dev", "val"}:
        raise ValueError("Use claim_final_test for one-time final-test access")
    if frame_fingerprint(panel) != manifest["fingerprint"]:
        raise ValueError("Panel fingerprint differs from frozen split")
    return panel.loc[manifest[f"{split}_start"]:manifest[f"{split}_end"]].copy()


def claim_final_test(panel, manifest_path, frozen_config_path) -> pd.DataFrame:
    """Claim final-test rows ONCE, recording the SHA-256 of frozen settings.

    O_EXCL protects concurrent runs; a failed evaluation keeps the lock for audit.
    This controls the project API, not direct reads of the underlying Parquet.
    """
    path = Path(manifest_path)
    manifest = validate_split_manifest(json.loads(path.read_text(encoding="utf-8")))
    if frame_fingerprint(panel) != manifest["fingerprint"]:
        raise ValueError("Panel fingerprint differs from frozen split")
    if manifest.get("test_consumed"):
        raise ValueError("Final test has already been consumed")
    frozen_sha = file_sha256(frozen_config_path)
    lock = path.with_suffix(path.suffix+".test.lock")
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(frozen_sha+"\n")
    except FileExistsError as exc:
        raise ValueError("Final test has already been claimed") from exc
    manifest.update(test_consumed=True, frozen_config_sha256=frozen_sha)
    write_json(manifest, path)
    return panel.loc[manifest["test_start"]:manifest["test_end"]].copy()
