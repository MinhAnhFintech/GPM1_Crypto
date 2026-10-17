"""UTC logging shared by offline stages and collectors."""
import logging
import time
from pathlib import Path


def configure_logging(log_file: str | Path | None = None, level: int = logging.INFO):
    """Install UTC timestamps; optionally append a UTF-8 log in the given path."""
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = time.gmtime
    handlers = [logging.StreamHandler()]
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    return logging.getLogger("gpm1")
