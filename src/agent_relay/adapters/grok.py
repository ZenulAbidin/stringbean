from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .base import CommandAdapterMixin


class GrokAdapter(CommandAdapterMixin):
    name = "grok"
    default_executable = "grok"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        command = super().build_command(prompt, repo_root)
        if self._output_format(command) is None:
            command = command + ["--output-format", "streaming-json"]
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

    def normalize_stdout(self, stdout: str) -> str:
        if not stdout.strip():
            return stdout

        text_chunks: list[str] = []
        saw_stream_event = False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not event.get("type"):
                continue
            saw_stream_event = True
            if str(event.get("type")).lower() != "text":
                continue
            data = event.get("data")
            if isinstance(data, str):
                text_chunks.append(data)

        if saw_stream_event and text_chunks:
            return "".join(text_chunks)
        return stdout

    def uses_structured_stream(self, command: List[str]) -> bool:
        return self._output_format(command) == "streaming-json"

    @staticmethod
    def _output_format(command: List[str]) -> str | None:
        for index, part in enumerate(command):
            if part == "--output-format" and index + 1 < len(command):
                return command[index + 1]
            if part.startswith("--output-format="):
                return part.split("=", 1)[1]
        return None
