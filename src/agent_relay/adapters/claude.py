from __future__ import annotations

from pathlib import Path
from typing import List

from .base import CommandAdapterMixin


class ClaudeAdapter(CommandAdapterMixin):
    name = "claude"
    default_executable = "claude"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        return super().build_command(prompt, repo_root)

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable, "--help"], [executable, "-h"], [executable, "--version"]]

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "file", "argv"}
