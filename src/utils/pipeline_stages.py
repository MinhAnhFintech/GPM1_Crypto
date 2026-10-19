"""C4.12: execute team handoffs and require outputs from the current run."""
from __future__ import annotations

import math
from pathlib import Path
import subprocess
import time

from .io_utils import file_sha256

REQUIRED_STAGES = frozenset({"features", "regime_flags", "price_discovery", "cost_backtest", "evaluation"})
DEFAULT_TIMEOUT_S = 3600


def _format(value, substitutions):
    # Replace only our tokens; literal braces in Python/JSON argv stay intact.
    for key, replacement in substitutions.items():
        value = value.replace("{" + key + "}", replacement)
    return value


def _prepare(stages, root, substitutions):
    if not isinstance(stages, list):
        raise ValueError("pipeline_stages must be a list")
    prepared, names, output_owners = [], set(), {}
    for stage in stages:
        if not isinstance(stage, dict) or not isinstance(stage.get("name"), str) or not stage["name"].strip():
            raise ValueError("Each pipeline stage needs a nonempty name")
        name = stage["name"]
        if name in names:
            raise ValueError(f"Duplicate pipeline stage name: {name}")
        names.add(name)
        enabled = stage.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"Stage {name}: enabled must be boolean")
        if not enabled:
            prepared.append({"name": name, "enabled": False})
            continue
        command, outputs = stage.get("command"), stage.get("outputs")
        if (not isinstance(command, list) or not command
                or any(not isinstance(arg, str) for arg in command) or not command[0].strip()):
            raise ValueError(f"Stage {name}: command must be a nonempty argv list of strings")
        if (not isinstance(outputs, list) or not outputs
                or any(not isinstance(path, str) or not path.strip() for path in outputs)):
            raise ValueError(f"Stage {name}: declare at least one output file")
        timeout = stage.get("timeout_s", DEFAULT_TIMEOUT_S)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"Stage {name}: timeout_s must be finite and positive")
        resolved = []
        for value in outputs:
            path = Path(_format(value, substitutions))
            path = (path if path.is_absolute() else root / path).resolve()
            if path in output_owners:
                raise ValueError(f"Stage {name}: output already declared by {output_owners[path]}: {path}")
            output_owners[path] = name
            resolved.append(path)
        prepared.append({"name": name, "enabled": True, "command": [_format(arg, substitutions) for arg in command],
                         "outputs": resolved, "timeout_s": timeout})
    return prepared


def _snapshot(path):
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"Declared stage output is not a file: {path}")
    stat = path.stat()
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino, file_sha256(path))


def run_pipeline_stages(stages, *, root, data_dir, output_dir, python, on_update, foundation_only=False):
    """Run configured argv sequentially; publish running/failure/completion evidence.

    Relative paths resolve against root. Existing files remain intact, but a
    successful command must create or rewrite every declared output; stale files
    cannot count as a successful handoff. Equal bytes are fine when rewritten.
    foundation_only never launches team commands. No final-test claim is made.
    """
    root = Path(root).resolve()
    substitutions = {"python": str(python), "data_dir": str(Path(data_dir).resolve()),
                     "output_dir": str(Path(output_dir).resolve())}
    prepared = _prepare(stages, root, substitutions)
    results = []
    for stage in prepared:
        result = {"name": stage["name"], "status": "not_run"}
        results.append(result)
        if not stage["enabled"] or foundation_only:
            result.update(status="disabled" if not stage["enabled"] else "not_run",
                          reason="disabled_in_config" if not stage["enabled"] else "foundation_only")
            on_update(results)
            continue
        started = time.monotonic()
        result["status"] = "running"
        on_update(results)
        try:
            before = {path: _snapshot(path) for path in stage["outputs"]}
            subprocess.run(stage["command"], cwd=root, check=True, shell=False, timeout=stage["timeout_s"])
            signatures = {path: _snapshot(path) for path in stage["outputs"]}
            missing = [str(path) for path, signature in signatures.items() if signature is None]
            stale = [str(path) for path, signature in signatures.items() if signature is not None and signature == before[path]]
            if missing:
                raise ValueError(f"Stage {stage['name']} did not produce declared outputs: {missing}")
            if stale:
                raise ValueError(f"Stage {stage['name']} left stale outputs unchanged: {stale}")
            result.update(status="passed", outputs_sha256={str(path): signature[-1] for path, signature in signatures.items()})
        except Exception as exc:
            result.update(status="failed", error_type=type(exc).__name__)
            if isinstance(exc, subprocess.CalledProcessError):
                result["return_code"] = exc.returncode
            elif isinstance(exc, subprocess.TimeoutExpired):
                result["timeout_s"] = stage["timeout_s"]
            else:
                result["reason"] = str(exc)
            raise
        finally:
            result["duration_s"] = round(time.monotonic() - started, 6)
            on_update(results)
    return results
