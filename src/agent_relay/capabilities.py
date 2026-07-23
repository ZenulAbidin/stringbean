from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


CLI_CAPABILITIES_FILE = "cli-capabilities.json"
CLAUDE_MAX_PLAN_SIGNAL_SOURCE = "claude-cli-account-status"
CLAUDE_MAX_PLAN_STALE_AFTER = timedelta(days=7)


def local_capabilities_path(root: Path) -> Path:
    return Path(root).resolve() / ".stringbean" / CLI_CAPABILITIES_FILE


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fresh(timestamp: datetime, now: datetime) -> bool:
    now = now.astimezone(timezone.utc)
    if timestamp > now + timedelta(minutes=5):
        return False
    return now - timestamp <= CLAUDE_MAX_PLAN_STALE_AFTER


def claude_max_plan_detected_from_payload(payload: Any, *, now: datetime | None = None) -> bool:
    if not isinstance(payload, dict):
        return False

    provider_capabilities = payload.get("provider_capabilities")
    if not isinstance(provider_capabilities, dict):
        return False
    if set(provider_capabilities) != {"claude"}:
        return False

    claude = provider_capabilities.get("claude")
    if not isinstance(claude, dict):
        return False
    if set(claude) != {"max_plan"}:
        return False

    signal = claude.get("max_plan")
    if not isinstance(signal, dict):
        return False
    if set(signal) != {"detected", "source", "detected_at"}:
        return False
    if signal.get("detected") is not True:
        return False
    if signal.get("source") != CLAUDE_MAX_PLAN_SIGNAL_SOURCE:
        return False

    detected_at = _parse_timestamp(signal.get("detected_at"))
    if detected_at is None:
        return False

    return _fresh(detected_at, now or datetime.now(timezone.utc))


def claude_max_plan_detected(root: Path, *, now: datetime | None = None) -> bool:
    path = local_capabilities_path(root)
    try:
        if path.is_symlink() or not path.is_file():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return claude_max_plan_detected_from_payload(payload, now=now)
