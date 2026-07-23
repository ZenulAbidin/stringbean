from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, List

from agent_relay.capabilities import claude_max_plan_detected

from .base import CommandAdapterMixin, option_value


CLAUDE_FABLE5_MAX_PLAN_ENV = "STRINGBEAN_CLAUDE_MAX_PLAN"
CLAUDE_FABLE5_EXPLICIT_MODELS = {"fable", "claude-fable-5"}
CLAUDE_FABLE5_REJECTION = (
    "Claude Fable 5 requires detected Claude Max plan access or an explicit Claude Max plan opt-in. "
    "Stringbean did not detect Max entitlement and refused to launch explicit model {model!r} for "
    f"agent {{agent!r}}. Use the portable 'sonnet' model, or set {CLAUDE_FABLE5_MAX_PLAN_ENV}=1 only "
    "when this Claude account has Max plan access."
)

LEGACY_CLAUDE_MODEL_ALIASES = {
    # "opus-4.8" (dot separator, no "claude-" prefix) was never a real model
    # id -- only the hyphenated "claude-opus-4-8" full name is real. Repair
    # the malformed legacy string. Malformed legacy Fable 5 spellings are
    # fail-closed to Sonnet; explicit Fable 5 requests are rejected below
    # unless a Max-plan opt-in is present.
    "opus-4.8": "opus",
    "claude-fable5": "sonnet",
    "fable-5": "sonnet",
}


def normalize_claude_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    if not normalized:
        return normalized
    return LEGACY_CLAUDE_MODEL_ALIASES.get(normalized.lower(), normalized)


def claude_max_plan_enabled(
    environment_overrides: dict[str, str] | None = None,
    capability_root: Path | None = None,
) -> bool:
    configured = (environment_overrides or {}).get(CLAUDE_FABLE5_MAX_PLAN_ENV)
    value = configured if configured is not None else os.environ.get(CLAUDE_FABLE5_MAX_PLAN_ENV, "")
    if value.strip().lower() in {"1", "true", "yes", "on", "max"}:
        return True
    if capability_root is None or not capability_root.is_dir():
        return False
    return claude_max_plan_detected(capability_root)


def _is_explicit_fable5_model(model: str | None) -> bool:
    return bool(model and model.strip().lower() in CLAUDE_FABLE5_EXPLICIT_MODELS)


def claude_agent_uses_fable5(agent: Any) -> bool:
    configured_model = normalize_claude_model(getattr(agent, "model", None))
    if _is_explicit_fable5_model(configured_model):
        return True
    command = getattr(agent, "command", None) or []
    command_model = option_value(_normalize_model_option(list(command)), "--model")
    return _is_explicit_fable5_model(command_model)


def claude_agent_fable5_available(agent: Any, capability_root: Path | None = None) -> bool:
    if not claude_agent_uses_fable5(agent):
        return True
    return claude_max_plan_enabled(getattr(agent, "environment_overrides", None), capability_root)


def _reject_non_max_fable5(
    model: str | None,
    agent_name: str,
    environment_overrides: dict[str, str] | None = None,
    capability_root: Path | None = None,
) -> None:
    if _is_explicit_fable5_model(model) and not claude_max_plan_enabled(environment_overrides, capability_root):
        raise ValueError(CLAUDE_FABLE5_REJECTION.format(model=model, agent=agent_name))


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
        _reject_non_max_fable5(self._model(command), self.agent.name, self.agent.environment_overrides, repo_root)
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
