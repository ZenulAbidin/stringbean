from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .base import CommandAdapterMixin


class ClaudeAdapter(CommandAdapterMixin):
    name = "claude"
    default_executable = "claude"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        command = super().build_command(prompt, repo_root)
        if self.agent.model and self._model(command) is None:
            command = command + ["--model", self.agent.model]
        if "-p" not in command and "--print" not in command:
            command = command + ["--print"]

        output_format = self._output_format(command)
        if output_format is None:
            command = command + ["--output-format", "stream-json"]
            output_format = "stream-json"
        if output_format == "stream-json" and "--verbose" not in command:
            command = command + ["--verbose"]
        return command

    def default_probe_commands(self, executable: str) -> List[List[str]]:
        return [[executable, "--help"], [executable, "-h"], [executable, "--version"]]

    def supports_prompt_transport(self, transport: str) -> bool:
        return transport in {"stdin", "argv"}

    def normalize_stdout(self, stdout: str) -> str:
        if not stdout.strip():
            return stdout

        final_results: list[str] = []
        assistant_text: list[str] = []
        saw_stream_event = False
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not event.get("type"):
                continue
            saw_stream_event = True
            event_type = str(event.get("type")).lower()
            if event_type == "result" and isinstance(event.get("result"), str):
                final_results.append(event["result"])
            elif event_type == "assistant":
                assistant_text.extend(self._assistant_text(event))

        if final_results:
            return final_results[-1]
        if saw_stream_event and assistant_text:
            return "\n".join(assistant_text)
        return stdout

    def uses_structured_stream(self, command: List[str]) -> bool:
        return self._output_format(command) == "stream-json"

    @staticmethod
    def _assistant_text(event: dict[str, object]) -> list[str]:
        message = event.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            return []
        text: list[str] = []
        for block in content:
            if not isinstance(block, dict) or str(block.get("type")).lower() != "text":
                continue
            value = block.get("text")
            if isinstance(value, str):
                text.append(value)
        return text

    @staticmethod
    def _output_format(command: List[str]) -> str | None:
        for index, part in enumerate(command):
            if part == "--output-format" and index + 1 < len(command):
                return command[index + 1]
            if part.startswith("--output-format="):
                return part.split("=", 1)[1]
        return None

    @staticmethod
    def _model(command: List[str]) -> str | None:
        for index, part in enumerate(command):
            if part == "--model" and index + 1 < len(command):
                return command[index + 1]
            if part.startswith("--model="):
                return part.split("=", 1)[1]
        return None
