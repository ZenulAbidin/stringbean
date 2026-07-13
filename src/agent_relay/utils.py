from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any


SENSITIVE_PATTERNS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE")


def _redact_env_dict(env: dict[str, str]) -> dict[str, str]:
    out = {}
    for key, value in env.items():
        if any(token in key.upper() for token in SENSITIVE_PATTERNS):
            out[key] = "REDACTED"
        else:
            out[key] = value
    return out


def merged_environment(env: dict[str, str] | None) -> dict[str, str]:
    base: dict[str, str] = dict(os.environ)
    if env is not None:
        base.update(env)
    return base


def environment_redaction_values(env: dict[str, str]) -> list[str]:
    """Return secret-like environment values in longest-first replacement order."""
    values = []
    for key, value in env.items():
        if value and any(token in key.upper() for token in SENSITIVE_PATTERNS):
            values.append(value)
    return sorted(set(values), key=len, reverse=True)


def redact_environment_text(value: str, redaction_values: list[str]) -> str:
    for secret in redaction_values:
        value = value.replace(secret, "REDACTED")
    return value


def redact_environment_payload(payload: Any, redaction_values: list[str]) -> Any:
    if isinstance(payload, dict):
        return {k: redact_environment_payload(v, redaction_values) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_environment_payload(x, redaction_values) for x in payload]
    if isinstance(payload, str):
        return redact_environment_text(payload, redaction_values)
    return payload


def sanitize_environment(env: dict[str, str] | None) -> dict[str, str]:
    return _redact_env_dict(merged_environment(env))


def git_status_short(repo_root: Path) -> str:
    import subprocess

    from .policy import git_command, internal_subprocess_env

    try:
        proc = subprocess.run(
            [git_command(), "status", "--short", "--", "."],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            env=internal_subprocess_env(),
        )
        return proc.stdout
    except FileNotFoundError:
        return ""


def stable_id(prefix: str, task: str, at: str | None = None) -> str:
    from datetime import datetime

    time_part = at or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", task.strip().lower())[:28].strip("-")
    return f"{time_part}-{slug or 'task'}"
