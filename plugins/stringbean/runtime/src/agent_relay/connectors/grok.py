from __future__ import annotations

from ..adapters.grok import GrokAdapter


class GrokConnector(GrokAdapter):
    """Connector wrapper around the Grok CLI adapter."""

    name = "grok"
