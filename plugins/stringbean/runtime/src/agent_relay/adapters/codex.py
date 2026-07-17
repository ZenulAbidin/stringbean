from __future__ import annotations

from .base import CommandAdapterMixin


class CodexAdapter(CommandAdapterMixin):
    name = "codex"
    default_executable = "codex"
