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
        self._lock = Lock()

    def feed(self, chunk: str) -> None:
        if not chunk:
            return
        with self._lock:
            text = chunk.replace("\r\n", "\n").replace("\r", "\n")
            self._buffer += text
            self._drain_complete_lines()

    def flush(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            line = self._buffer
            self._buffer = ""
            self._emit(line)

    def _drain_complete_lines(self) -> None:
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._emit(line)

    def _emit(self, line: str) -> None:
        for formatted in format_stream_line(line):
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
    event_type = str(event.get("type") or event.get("event") or "").strip()
    label = _event_label(event_type)
    text = _extract_event_text(event)

    if text:
        lines = _split_formatted_lines(decode_visible_escapes(text))
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


def _event_label(event_type: str) -> str:
    normalized = event_type.replace("-", "_").lower()
    labels = {
        "agent_message": "assistant",
        "assistant_message": "assistant",
        "message": "message",
        "reasoning": "reasoning",
        "reasoning_summary": "reasoning",
        "tool_call": "tool",
        "function_call": "tool",
        "exec_command": "tool",
        "exec_command_begin": "tool",
        "exec_command_output": "tool output",
        "tool_result": "tool output",
        "error": "error",
        "session_config": "session",
        "token_count": "tokens",
    }
    return labels.get(normalized, event_type)


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
