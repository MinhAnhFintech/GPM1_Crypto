"""Atomic Parquet/JSON I/O; preserve provenance and validate before publishing."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
import pandas as pd
from .schema import validate_frame


def write_parquet(df: pd.DataFrame, path, schema: str | None = None) -> Path:
    """Write a Parquet atomically, including index and pandas attrs metadata."""
    if schema:
        validate_frame(df, schema)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        df.to_parquet(tmp, engine="pyarrow", index=True)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def read_parquet(path, schema: str | None = None) -> pd.DataFrame:
    """Read a local Parquet and reject incompatible handoffs when schema is set."""
    df = pd.read_parquet(path, engine="pyarrow")
    return validate_frame(df, schema) if schema else df


def write_json(value, path) -> Path:
    """Atomically save UTF-8 JSON; NaN and Infinity are forbidden."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name+".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    return path


def file_sha256(path) -> str:
    """SHA-256 of exact file bytes for handoff checksums."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frame_fingerprint(df: pd.DataFrame) -> str:
    """SHA-256 of schema, UTC index, values and provenance in pinned pandas."""
    canonical = df.reindex(sorted(df.columns), axis=1)
    digest = hashlib.sha256()
    digest.update(json.dumps({"columns": list(canonical.columns), "dtypes": [str(d) for d in canonical.dtypes], "index_dtype": str(df.index.dtype), "attrs": df.attrs}, sort_keys=True, default=str).encode())
    digest.update(pd.util.hash_pandas_object(canonical, index=True).to_numpy().tobytes())
    return digest.hexdigest()
