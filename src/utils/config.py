"""Read explicit YAML configuration relative to the caller-selected file."""
from pathlib import Path
import yaml


def load_config(path) -> dict:
    """Load a YAML mapping; never load code or guess missing production values."""
    result = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return result
