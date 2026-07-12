from __future__ import annotations

from ..adapters.claude import ClaudeAdapter


class ClaudeConnector(ClaudeAdapter):
    """Connector wrapper around the Claude CLI adapter."""

    name = "claude"
