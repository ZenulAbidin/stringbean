from __future__ import annotations

from pathlib import Path
from typing import List

from .base import CommandAdapterMixin


class CodexAdapter(CommandAdapterMixin):
    name = "codex"
    default_executable = "codex"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        command = super().build_command(prompt, repo_root)
        return command

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable, "--help"], [executable, "-h"], [executable, "--version"]]

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "file", "argv"}
