from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..config import AgentConfig


@dataclass
class AdapterCapabilities:
    executable: str
    available: bool
    probe_output: Optional[str] = None
    error: Optional[str] = None


class Adapter(ABC):
    name = "adapter"

    def __init__(self, agent: AgentConfig) -> None:
        self.agent = agent

    @abstractmethod
    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def default_probe_commands(self, executable: str) -> List[List[str]]:
        raise NotImplementedError

    @abstractmethod
    async def detect(self, repo_root: Path) -> AdapterCapabilities:
        raise NotImplementedError

    @abstractmethod
    def supports_prompt_transport(self, transport: str) -> bool:
        raise NotImplementedError


class CommandAdapterMixin(Adapter, ABC):
    default_executable: Optional[str] = None
    probe_suffixes = ["--help", "-h", "--version"]

    async def detect(self, repo_root: Path) -> AdapterCapabilities:
        command = self.agent.command or [self.default_executable] if self.default_executable else self.agent.command
        if not command:
            return AdapterCapabilities(executable="", available=False, error="No command configured")

        executable = command[0]
        if not shutil.which(executable):
            return AdapterCapabilities(executable=executable, available=False, error="Executable not in PATH")

        for probe in self.default_probe_commands(executable):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *probe,
                    cwd=str(repo_root),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                out = (stderr or b"").decode(errors="replace").strip()
                if proc.returncode == 0:
                    return AdapterCapabilities(
                        executable=executable,
                        available=True,
                        probe_output=out or None,
                    )
            except FileNotFoundError:
                return AdapterCapabilities(executable=executable, available=False, error="Executable not in PATH")
            except Exception as exc:
                err = str(exc)
                # keep searching; some commands exit non-zero and still valid
                continue

        return AdapterCapabilities(
            executable=executable,
            available=False,
            error="No probe command succeeded",
            probe_output=None,
        )

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable, "--help"], [executable, "-h"], [executable, "--version"]]

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        base = self.agent.command or [self.default_executable]
        if base is None:
            raise ValueError("no executable")
        return base

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "argv", "file"}
