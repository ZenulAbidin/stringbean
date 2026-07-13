from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


SENSITIVE_PATTERNS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "ACCESS_KEY", "PRIVATE")


def redact_text(value: str) -> str:
    if value is None:
        return value
    return "REDACTED"


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


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {k: redact_payload(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [redact_payload(x) for x in payload]
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (int, float, bool)) or payload is None:
        return payload
    try:
        json_payload = json.loads(json.dumps(payload))
        return redact_payload(json_payload)
    except Exception:
        return payload


def sanitize_environment(env: dict[str, str] | None) -> dict[str, str]:
    return _redact_env_dict(merged_environment(env))


def find_path_in_repo(path: str) -> Path:
    if path == ".":
        return Path(".").resolve()
    return Path(path).expanduser().resolve()


def git_status_short(repo_root: Path) -> str:
    import subprocess

    from .policy import git_command, internal_subprocess_env

    try:
        proc = subprocess.run(
            [git_command(), "status", "--short"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            env=internal_subprocess_env(),
        )
        return proc.stdout
    except FileNotFoundError:
        return ""


def file_status_set(status_text: str) -> set[str]:
    files: set[str] = set()
    for line in status_text.splitlines():
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) == 2:
            files.add(parts[1])
    return files


def stable_id(prefix: str, task: str, at: str | None = None) -> str:
    from datetime import datetime

    time_part = at or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", task.strip().lower())[:28].strip("-")
    return f"{time_part}-{slug or 'task'}"
