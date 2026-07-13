from __future__ import annotations

import json
from pathlib import Path

from agent_relay import __version__


ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    in_project = False
    for line in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project and stripped.startswith("version = "):
            return stripped.split("=", 1)[1].strip().strip('"')
    raise AssertionError("Missing [project] version in pyproject.toml")


def _manifest_version(path: str) -> str:
    data = json.loads((ROOT / path).read_text(encoding="utf-8"))
    return data["version"].split("+", 1)[0]


def test_release_versions_stay_in_sync():
    expected = _pyproject_version()

    assert __version__ == expected
    assert _manifest_version("plugins/stringbean/.codex-plugin/plugin.json") == expected
    assert _manifest_version("plugins/claude-stringbean/.claude-plugin/plugin.json") == expected
    assert _manifest_version("plugins/grok-stringbean/.grok-plugin/plugin.json") == expected
