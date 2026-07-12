from __future__ import annotations

from ..adapters.codex import CodexAdapter


class CodexConnector(CodexAdapter):
    """Connector wrapper around the Codex CLI adapter."""

    name = "codex"
