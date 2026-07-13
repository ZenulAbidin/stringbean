from __future__ import annotations

from pathlib import Path
from typing import List

from .base import CommandAdapterMixin


class GrokAdapter(CommandAdapterMixin):
    name = "grok"
    default_executable = "grok"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        command = super().build_command(prompt, repo_root)
        if self.agent.prompt_transport == "argv":
            if "-p" not in command and "--single" not in command:
                command = command + ["-p"]
        elif self.agent.prompt_transport == "file":
            if "--prompt-file" not in command:
                command = command + ["--prompt-file"]
        return command

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable, "--help"], [executable, "-h"], [executable, "--version"]]

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"file", "argv"}
