from __future__ import annotations

import json
from pathlib import Path
from typing import List

from .base import CommandAdapterMixin, option_value


LEGACY_CLAUDE_MODEL_ALIASES = {
    # "opus-4.8" (dot separator, no "claude-" prefix) was never a real model
    # id -- only the hyphenated "claude-opus-4-8" full name is real. Repair
    # the malformed legacy string; real full names (claude-opus-4-8,
    # claude-sonnet-5, claude-fable-5, claude-haiku-4-5-...) pass straight
    # through to the CLI, which accepts them natively alongside the stable
    # opus/sonnet/haiku/fable aliases.
    "opus-4.8": "opus",
}


def normalize_claude_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    if not normalized:
        return normalized
    return LEGACY_CLAUDE_MODEL_ALIASES.get(normalized.lower(), normalized)


def _normalize_model_option(command: List[str]) -> List[str]:
    normalized = list(command)
    for index, part in enumerate(normalized):
        if part == "--model" and index + 1 < len(normalized):
            normalized[index + 1] = normalize_claude_model(normalized[index + 1]) or normalized[index + 1]
        elif part.startswith("--model="):
            value = part.split("=", 1)[1]
            normalized[index] = f"--model={normalize_claude_model(value) or value}"
    return normalized


class ClaudeAdapter(CommandAdapterMixin):
    name = "claude"
    default_executable = "claude"

    def build_command(self, prompt: str, repo_root: Path) -> List[str]:
        command = _normalize_model_option(super().build_command(prompt, repo_root))
        configured_model = normalize_claude_model(self.agent.model)
        if configured_model and self._model(command) is None:
            command = command + ["--model", configured_model]
        if "-p" not in command and "--print" not in command:
            command = command + ["--print"]

        output_format = option_value(command, "--output-format")
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
        return option_value(command, "--output-format") == "stream-json"

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
    def _model(command: List[str]) -> str | None:
        return option_value(command, "--model")
