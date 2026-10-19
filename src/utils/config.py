"""Read explicit YAML configuration relative to the caller-selected file."""
from pathlib import Path
import yaml


def load_config(path) -> dict:
    """Load a YAML mapping; never load code or guess missing production values."""
    result = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return result


def configured_venue_ids(cfg) -> list[str]:
    """Require finalized, unique {exchange}_{spot|perp} IDs for production."""
    from .schema import VENUE_PATTERN
    venues = cfg.get("venues")
    if (not isinstance(venues, list) or not venues
            or not all(isinstance(v, str) and VENUE_PATTERN.fullmatch(v) for v in venues)
            or len(venues) != len(set(venues))):
        raise ValueError("Production venues must be a nonempty list of unique venue ID strings")
    return venues
