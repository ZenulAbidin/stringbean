from __future__ import annotations

from pathlib import Path
from typing import List

from .base import Adapter, AdapterCapabilities


class GenericCLIAdapter(Adapter):
    name = "generic"

    async def detect(self, repo_root: Path) -> AdapterCapabilities:
        command = self.agent.command or []
        if not command:
            return AdapterCapabilities(executable="", available=False, error="No command configured")

        executable = command[0]
        from shutil import which
        if not which(executable):
            return AdapterCapabilities(executable=executable, available=False, error="Executable not in PATH")

        return AdapterCapabilities(executable=executable, available=True, probe_output="generic adapter")

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        return self.agent.command or []

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable]]

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "argv", "file"}
