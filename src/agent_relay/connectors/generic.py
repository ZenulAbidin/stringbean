from __future__ import annotations

from ..adapters.generic import GenericCLIAdapter


class GenericConnector(GenericCLIAdapter):
    """Generic connector shim for custom command-line agents."""

    name = "generic"
