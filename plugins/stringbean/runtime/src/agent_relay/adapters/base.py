from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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

    def normalize_stdout(self, stdout: str) -> str:
        """Return provider response text suitable for structured parsing."""
        return stdout

    def uses_structured_stream(self, command: List[str]) -> bool:
        """Whether live stdout is an event stream that must be formatted."""
        return False


class CommandAdapterMixin(Adapter, ABC):
    default_executable: Optional[str] = None

    async def detect(self, repo_root: Path) -> AdapterCapabilities:
        command = self._base_command()
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
            except Exception:
                # Keep probing; CLIs vary in which help/version flags they accept.
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
        command = self._base_command()
        if command is None:
            raise ValueError("no executable")
        return list(command)

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "argv", "file"}

    def _base_command(self) -> List[str] | None:
        if self.agent.command:
            return list(self.agent.command)
        if self.default_executable:
            return [self.default_executable]
        return None


def option_value(command: List[str], option: str) -> str | None:
    """Return a CLI option value from either `--name value` or `--name=value`."""
    prefix = f"{option}="
    for index, part in enumerate(command):
        if part == option and index + 1 < len(command):
            return command[index + 1]
        if part.startswith(prefix):
            return part.split("=", 1)[1]
    return None
