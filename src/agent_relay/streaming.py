from __future__ import annotations

import json
import re
from threading import Lock
from typing import Any, Callable, Iterable


_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


class LiveStreamFormatter:
    """Line-oriented formatter for provider subprocess output.

    Raw stdout/stderr is still captured by the runner. This class only affects
    the live console stream: it buffers partial chunks, decodes visible escape
    sequences, and turns common JSON event records into readable log lines.
    """

    def __init__(self, write_line: Callable[[str], None]) -> None:
        self._write_line = write_line
        self._buffer = ""
        self._suppress_prompt_echo = False
        self._capturing_tool_output = False
        self._tool_output_remaining = 0
        self._suppress_next_token_count = False
        self._json_buffer: list[str] | None = None
        self._fenced_json_buffer: list[str] | None = None
        self._streaming_text_chunks: list[str] = []
        self._recent_output: list[str] = []
        self._lock = Lock()

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            text = chunk.replace("\r\n", "\n").replace("\r", "\n")
            self._buffer += text
            self._drain_complete_lines()

    def flush(self, *, final: bool = True) -> None:
        with self._lock:
            if not final:
                return
            if self._buffer:
                line = self._buffer
                self._buffer = ""
                self._emit(line)
            self._flush_json_buffers()
            self._flush_streaming_text()

    def _drain_complete_lines(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)

    def _emit(self, line: str) -> None:
        for formatted in self._format_stateful_line(line):
            if self._is_duplicate(formatted):
                continue
            self._write_line(formatted)

    def _is_duplicate(self, line: str) -> bool:
        if line in self._recent_output:
            return True
        self._recent_output.append(line)
        if len(self._recent_output) > 80:
            self._recent_output = self._recent_output[-80:]
        return False

    def _format_stateful_line(self, line: str) -> list[str]:
        stripped = line.strip()

        streaming_event = _parse_json_object(stripped) if stripped.startswith("{") else None
        if streaming_event is not None:
            event_type = str(streaming_event.get("type") or "").strip().lower()
            if event_type == "text" and isinstance(streaming_event.get("data"), str):
                self._streaming_text_chunks.append(streaming_event["data"])
                return []
            if event_type == "thought":
                return []
            if event_type == "end":
                return self._take_streaming_text()

        if self._suppress_next_token_count:
            self._suppress_next_token_count = False
            if re.fullmatch(r"[0-9][0-9,._]*", stripped):
                return []

        if self._suppress_prompt_echo:
            if stripped in {"codex", "assistant"}:
                self._suppress_prompt_echo = False
            return []

        if _starts_prompt_echo(stripped):
            self._suppress_prompt_echo = True
            return []

        if self._capturing_tool_output:
            if stripped in {"codex", "assistant"}:
                self._capturing_tool_output = False
                self._tool_output_remaining = 0
                return []
            if stripped.startswith(("{", "[")) or stripped.lower() in {"```json", "```jsonc"}:
                self._capturing_tool_output = False
                self._tool_output_remaining = 0
            else:
                if self._tool_output_remaining <= 0 or not stripped:
                    return []
                self._tool_output_remaining -= 1
                return [f"  {decode_visible_escapes(_shorten_line(stripped))}"]

        if self._fenced_json_buffer is not None:
            if stripped == "```":
                payload = "\n".join(self._fenced_json_buffer)
                self._fenced_json_buffer = None
                return format_json_text(payload)
            self._fenced_json_buffer.append(line)
            return []

        if stripped.lower() in {"```json", "```jsonc"}:
            self._fenced_json_buffer = []
            return []

        if self._json_buffer is not None:
            self._json_buffer.append(line)
            formatted = self._try_finish_json_buffer()
            return formatted or []

        if stripped == "tokens used":
            self._suppress_next_token_count = True
            return []

        if _should_suppress_noise(stripped):
            return []

        tool_command = _format_tool_command(stripped)
        if tool_command:
            return [tool_command]

        tool_result = _format_tool_result(stripped)
        if tool_result:
            self._capturing_tool_output = True
            self._tool_output_remaining = 3
            return [tool_result]

        if stripped.startswith(("{", "[")):
            self._json_buffer = [line]
            formatted = self._try_finish_json_buffer()
            return formatted or []

        return format_stream_line(line)

    def _try_finish_json_buffer(self) -> list[str] | None:
        if self._json_buffer is None:
            return None
        payload = "\n".join(self._json_buffer)
        try:
            json.loads(payload)
        except Exception:
            if len(self._json_buffer) > 400:
                lines = []
                for item in self._json_buffer:
                    lines.extend(format_stream_line(item))
                self._json_buffer = None
                return lines
            return None
        self._json_buffer = None
        return format_json_text(payload)

    def _flush_json_buffers(self) -> None:
        for attr in ("_json_buffer", "_fenced_json_buffer"):
            buffer = getattr(self, attr)
            if buffer is None:
                continue
            setattr(self, attr, None)
            payload = "\n".join(buffer)
            for formatted in format_json_text(payload):
                self._write_line(formatted)

    def _take_streaming_text(self) -> list[str]:
        if not self._streaming_text_chunks:
            return []
        text = "".join(self._streaming_text_chunks).strip()
        self._streaming_text_chunks = []
        if not text:
            return []
        fenced = re.fullmatch(r"```(?:json|jsonc)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        try:
            value = json.loads(text)
        except Exception:
            return [f"assistant: {_shorten_line(line)}" for line in text.splitlines() if line.strip()]
        return list(_format_json_value(value))

    def _flush_streaming_text(self) -> None:
        for formatted in self._take_streaming_text():
            self._write_line(formatted)


def format_stream_line(line: str) -> list[str]:
    """Format a single provider output line for the live console stream."""
    stripped = line.strip()
    if stripped:
        event = _parse_json_object(stripped)
        if event is not None:
            return list(_format_json_event(event))

    decoded = decode_visible_escapes(line)
    return _split_formatted_lines(decoded)


def format_json_text(text: str) -> list[str]:
    try:
        value = json.loads(text)
    except Exception:
        lines: list[str] = []
        for line in text.splitlines():
            lines.extend(format_stream_line(line))
        return lines
    return list(_format_json_value(value))


def decode_visible_escapes(text: str) -> str:
    """Decode common escapes that providers expose inside streamed payloads."""
    if "\\" not in text:
        return text
    text = text.replace("\\r\\n", "\n")
    text = text.replace("\\n", "\n")
    text = text.replace("\\r", "\n")
    text = text.replace("\\t", "\t")
    return _UNICODE_ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 16)), text)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except Exception:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        return {"message": value}
    return {"value": value}


def _format_json_event(event: dict[str, Any]) -> Iterable[str]:
    if _is_reasoning_event(event):
        return

    structured = _format_structured_payload(event)
    if structured is not None:
        yield from structured
        return

    event_type = str(event.get("type") or event.get("event") or "").strip()
    label = _event_label(event_type)
    text = _extract_event_text(event)

    if text:
        lines = _split_formatted_lines(decode_visible_escapes(text))
        if label == "Tool Call":
            lines = lines[:1]
        elif label == "Executed":
            lines = lines[:3]
        if label:
            for part in lines:
                yield f"{label}: {part}" if part else label
        else:
            yield from lines
        return

    compact = _compact_event(event)
    if label and compact:
        yield f"{label}: {compact}"
    elif label:
        yield label
    elif compact:
        yield compact
    else:
        yield "{}"


def _format_json_value(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        yield from _format_json_event(value)
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield from _format_json_event(item)
            else:
                yield decode_visible_escapes(str(item))
        return
    yield decode_visible_escapes(str(value))


def _format_structured_payload(payload: dict[str, Any]) -> list[str] | None:
    if "type" in payload or "event" in payload:
        return None

    summary = str(payload.get("summary") or "").strip()

    if "tasks" in payload and isinstance(payload.get("tasks"), list):
        lines = [f"Plan: {summary}" if summary else "Plan:"]
        for task in payload.get("tasks", [])[:8]:
            if not isinstance(task, dict):
                continue
            title = str(task.get("title") or task.get("id") or "").strip()
            if title:
                lines.append(f"  - {title}")
        return lines

    if "status" in payload:
        status = str(payload.get("status") or "").strip()
        head = f"Result: {status}" if status else "Result:"
        if summary:
            head = f"{head} — {summary}"
        lines = [head]
        for key, label in (
            ("files_changed", "files"),
            ("tests", "tests"),
            ("remaining_issues", "remaining"),
            ("handoff_notes", "notes"),
        ):
            values = payload.get(key)
            if isinstance(values, list) and values:
                lines.append(f"  {label}: {_join_preview(values)}")
        return lines

    if "verdict" in payload:
        verdict = str(payload.get("verdict") or "").strip()
        head = f"Review: {verdict}" if verdict else "Review:"
        if summary:
            head = f"{head} — {summary}"
        lines = [head]
        required = payload.get("required_fixes")
        if isinstance(required, list) and required:
            lines.append(f"  required: {_join_preview(required)}")
        blocking = payload.get("blocking_issues")
        if isinstance(blocking, list) and blocking:
            lines.append(f"  blocking: {_join_preview(blocking)}")
        return lines

    if summary:
        return [f"Response: {summary}"]

    return None


def _join_preview(values: list[Any], limit: int = 4) -> str:
    rendered = []
    for item in values[:limit]:
        if isinstance(item, dict):
            text = str(item.get("summary") or item.get("title") or item.get("issue") or item)
        else:
            text = str(item)
        rendered.append(decode_visible_escapes(text))
    if len(values) > limit:
        rendered.append(f"+{len(values) - limit} more")
    return "; ".join(rendered)


def _event_label(event_type: str) -> str:
    normalized = event_type.replace("-", "_").lower()
    labels = {
        "agent_message": "assistant",
        "assistant_message": "assistant",
        "text": "assistant",
        "message": "message",
        "reasoning": "reasoning",
        "reasoning_summary": "reasoning",
        "tool_call": "Tool Call",
        "tool_use": "Tool Call",
        "tool_start": "Tool Call",
        "function_call": "Tool Call",
        "exec_command": "Tool Call",
        "exec_command_begin": "Tool Call",
        "exec_command_output": "Executed",
        "tool_result": "Executed",
        "tool_end": "Executed",
        "error": "error",
        "session_config": "session",
        "token_count": "tokens",
        "end": "complete",
    }
    return labels.get(normalized, event_type)


def _is_reasoning_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type") or event.get("event") or "").strip()
    normalized = event_type.replace("-", "_").replace(".", "_").lower()
    if (
        "reasoning" in normalized
        or "thought" in normalized
        or "chain_of_thought" in normalized
        or "scratchpad" in normalized
    ):
        return True
    if event_type:
        return False
    reasoning_keys = {"reasoning", "chain_of_thought", "scratchpad", "scratch_thoughts"}
    return any(key in event for key in reasoning_keys)


def _extract_event_text(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_extract_event_text(item) for item in value]
        joined = "\n".join(part for part in parts if part)
        return joined or None
    if not isinstance(value, dict):
        return None

    for key in (
        "message",
        "text",
        "summary",
        "content",
        "delta",
        "output",
        "stdout",
        "stderr",
        "error",
        "command",
        "cmd",
    ):
        if key in value:
            extracted = _extract_event_text(value[key])
            if extracted:
                return extracted

    for key in ("item", "payload", "data", "result"):
        if key in value:
            extracted = _extract_event_text(value[key])
            if extracted:
                return extracted

    return None


def _compact_event(event: dict[str, Any]) -> str:
    ignored = {"type", "event"}
    parts: list[str] = []
    for key, value in event.items():
        if key in ignored or value in (None, "", [], {}):
            continue
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(value)
        rendered = decode_visible_escapes(rendered)
        if "\n" in rendered:
            rendered = " ".join(part.strip() for part in rendered.splitlines() if part.strip())
        parts.append(f"{key}={rendered}")
        if len(parts) >= 5:
            break
    return " ".join(parts)


def _split_formatted_lines(text: str) -> list[str]:
    lines = text.splitlines()
    if lines:
        return lines
    return [""]


def _format_tool_command(stripped: str) -> str | None:
    if " in " not in stripped:
        return None
    if not (
        stripped.startswith("/")
        or stripped.startswith("python")
        or stripped.startswith("node")
        or stripped.startswith("bash")
        or stripped.startswith("zsh")
        or stripped.startswith("sh ")
    ):
        return None
    command = stripped.split(" in ", 1)[0]
    return f"Tool Call: {command}"


def _format_tool_result(stripped: str) -> str | None:
    match = re.match(r"^(succeeded|failed) in ([^:]+):$", stripped)
    if not match:
        return None
    return f"Executed: {match.group(1)} in {match.group(2)}"


def _starts_prompt_echo(stripped: str) -> bool:
    return stripped == "user"


def _should_suppress_noise(stripped: str) -> bool:
    if not stripped:
        return True
    if stripped in {
        "codex",
        "assistant",
        "exec",
        "--------",
        "tokens used",
    }:
        return True
    prefixes = (
        "Reading prompt from stdin",
        "OpenAI Codex v",
        "workdir:",
        "model:",
        "provider:",
        "approval:",
        "sandbox:",
        "reasoning effort:",
        "reasoning summaries:",
        "session id:",
        "hook:",
    )
    return stripped.startswith(prefixes)


def _shorten_line(line: str, limit: int = 240) -> str:
    if len(line) <= limit:
        return line
    return f"{line[: limit - 1]}…"
