#!/usr/bin/env python3
"""Dependency-free stdio MCP bridge for the bundled Stringbean plugin runtime."""

from __future__ import annotations

import atexit
import codecs
from collections import OrderedDict
import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
from threading import Event, RLock, Thread
import time
from typing import Any
from urllib.parse import unquote, urlparse
import uuid


PROTOCOL_VERSION = "2025-06-18"
MAX_TASK_CHARS = 100_000
MAX_OUTPUT_BYTES = 64 * 1024
MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_WAIT_SECONDS = 5.0
MAX_TERMINAL_RESULTS = 8
VALID_PROFILES = {"ro", "rw"}
VALID_MODES = {"auto", "low", "medium", "high"}
UNTRUSTED_CHILD_ENV = {
    "BASH_ENV",
    "BASHOPTS",
    "CDPATH",
    "ENV",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_SYSTEM",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "PYTHONWARNINGS",
    "SHELLOPTS",
    "STRINGBEAN_ENTRYPOINT",
    "STRINGBEAN_PYTHON",
    "STRINGBEAN_ROOT",
    "STRINGBEAN_SBX",
    "STRINGBEAN_NO_RUNTIME_BOOTSTRAP",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
    "_OLD_VIRTUAL_PATH",
}
SANDBOX_META_CAPABILITY = "codex/sandbox-state-meta"


@dataclass
class Run:
    process: subprocess.Popen[bytes]
    output_path: Path
    working_directory: Path
    thread_id: str
    cancel_requested: bool = False
    output_overflow: bool = False
    capture_error: str = ""
    capture_done: Event = field(default_factory=Event)
    capture_thread: Thread | None = None
    stop_lock: RLock = field(default_factory=RLock)


@dataclass(frozen=True)
class TerminalResult:
    thread_id: str
    request_cursor: int
    payload: dict[str, Any]


RUNS: dict[str, Run] = {}
TERMINAL_RESULTS: OrderedDict[str, TerminalResult] = OrderedDict()
SERVER_TEMP = Path(tempfile.mkdtemp(prefix="stringbean-codex-mcp-"))
_CLEANING_UP = False
_LIBC = ctypes.CDLL(None, use_errno=True) if sys.platform.startswith("linux") else None


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runtime_runner() -> Path:
    plugin_root = _plugin_root()
    runner = (plugin_root / "runtime" / "scripts" / "sbx").resolve()
    try:
        runner.relative_to(plugin_root)
    except ValueError as exc:
        raise RuntimeError("Bundled Stringbean runner escapes the installed plugin") from exc
    if not runner.is_file():
        raise RuntimeError("Bundled Stringbean plugin runtime is incomplete")
    return runner


def _working_directory(value: Any) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("working_directory must be a non-empty absolute path")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ValueError("working_directory must be an absolute path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"working_directory is unavailable: {exc}") from exc
    if not resolved.is_dir():
        raise ValueError("working_directory must identify a directory")
    return resolved


def _sandbox_working_directory(call_meta: Any) -> Path:
    if not isinstance(call_meta, dict):
        raise ValueError("Codex sandbox metadata is required to start Stringbean")
    sandbox_state = call_meta.get(SANDBOX_META_CAPABILITY)
    if not isinstance(sandbox_state, dict):
        raise ValueError("Codex sandbox metadata is required to start Stringbean")
    sandbox_cwd = sandbox_state.get("sandboxCwd")
    if not isinstance(sandbox_cwd, str):
        raise ValueError("Codex sandbox metadata does not identify the current workspace")
    parsed = urlparse(sandbox_cwd)
    if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
        raise ValueError("Codex sandbox workspace must be a local file URI")
    if parsed.query or parsed.fragment:
        raise ValueError("Codex sandbox workspace URI must not contain a query or fragment")
    return _working_directory(unquote(parsed.path))


def _thread_id(call_meta: Any) -> str:
    if not isinstance(call_meta, dict):
        raise ValueError("Codex call metadata is required")
    value = call_meta.get("threadId")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Codex call metadata does not identify the current thread")
    return value


def _safe_path(working_directory: Path, value: str | None) -> str:
    """Discard relative and workspace-owned PATH entries before provider launch."""
    safe: list[str] = []
    for raw_entry in (value or "").split(os.pathsep):
        if not raw_entry:
            continue
        entry = Path(raw_entry)
        if not entry.is_absolute():
            continue
        lexical = Path(os.path.normpath(str(entry)))
        try:
            lexical.relative_to(working_directory)
            continue
        except ValueError:
            pass
        resolved = entry.resolve(strict=False)
        try:
            resolved.relative_to(working_directory)
        except ValueError:
            safe.append(str(resolved))
    return os.pathsep.join(dict.fromkeys(safe))


def _configure_child_process() -> None:
    """Make an sbx child receive SIGINT even if the MCP server is killed."""
    if _LIBC is None:
        return
    # Linux prctl(PR_SET_PDEATHSIG, SIGINT). Normal Codex shutdown is also
    # covered by the server's explicit SIGTERM handler below.
    if _LIBC.prctl(1, signal.SIGINT, 0, 0, 0) != 0:
        os._exit(126)


def _capture_output(run: Run) -> None:
    stream = run.process.stdout
    marker = (
        b"\nSTRINGBEAN_INTERMEDIATE: Failure: plugin output exceeded the 16 MiB safety limit; "
        b"the run is being stopped.\n"
    )
    retained = 0
    stop_started = False
    try:
        if stream is None:
            raise OSError("Stringbean output pipe was not created")
        read_available = getattr(stream, "read1", stream.read)
        with run.output_path.open("ab", buffering=0) as output:
            while True:
                chunk = read_available(64 * 1024)
                if not chunk:
                    break
                if run.output_overflow:
                    continue
                available = max(0, MAX_LOG_BYTES - len(marker) - retained)
                if len(chunk) <= available:
                    output.write(chunk)
                    retained += len(chunk)
                    continue
                if available:
                    output.write(chunk[:available])
                    retained += available
                output.write(marker)
                retained += len(marker)
                run.output_overflow = True
                if not stop_started:
                    stop_started = True
                    Thread(target=_stop_run, args=(run,), daemon=True).start()
    except OSError as exc:
        run.capture_error = f"failed to retain Stringbean output: {exc}"
        if not stop_started:
            Thread(target=_stop_run, args=(run,), daemon=True).start()
    finally:
        if stream is not None:
            stream.close()
        run.capture_done.set()


def _integer_option(arguments: dict[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _start_sbx(arguments: dict[str, Any], call_meta: Any) -> dict[str, Any]:
    task = arguments.get("task")
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    if len(task) > MAX_TASK_CHARS:
        raise ValueError(f"task must not exceed {MAX_TASK_CHARS} characters")

    working_directory = _sandbox_working_directory(call_meta)
    thread_id = _thread_id(call_meta)
    profile = arguments.get("execution_profile", "rw")
    mode = arguments.get("mode", "auto")
    if profile not in VALID_PROFILES:
        raise ValueError("execution_profile must be ro or rw")
    if mode not in VALID_MODES:
        raise ValueError("mode must be auto, low, medium, or high")

    if RUNS:
        run_id = next(iter(RUNS))
        raise ValueError(f"Stringbean run {run_id} must be fully polled before starting another")

    command = [
        "/bin/bash",
        str(_runtime_runner()),
        f"--{profile}",
        "--mode",
        mode,
        "--plugin-compact-output",
        "--codex-progress-interval",
        "5",
    ]
    if arguments.get("dry_run", False) is True:
        command.append("--dry-run")
    elif arguments.get("dry_run", False) is not False:
        raise ValueError("dry_run must be a boolean")
    if arguments.get("no_advisor", False) is True:
        command.append("--no-advisor")
    elif arguments.get("no_advisor", False) is not False:
        raise ValueError("no_advisor must be a boolean")

    max_review_rounds = _integer_option(arguments, "max_review_rounds")
    if max_review_rounds is not None:
        command.extend(["--max-review-rounds", str(max_review_rounds)])
    policy_retries = _integer_option(arguments, "policy_retries")
    if policy_retries is not None:
        command.extend(["--policy-retries", str(policy_retries)])
    # Everything after ``--`` is task text, so task strings that begin with a
    # dash cannot smuggle additional Stringbean CLI flags.
    command.extend(["--", task])

    run_id = uuid.uuid4().hex
    output_path = SERVER_TEMP / f"{run_id}.log"
    child_env = os.environ.copy()
    for name in tuple(child_env):
        if (
            name in UNTRUSTED_CHILD_ENV
            or name.startswith(("DYLD_", "GIT_", "LD_", "STRINGBEAN_POLICY_"))
        ):
            child_env.pop(name, None)
    child_env["PATH"] = _safe_path(working_directory, child_env.get("PATH"))
    child_env["STRINGBEAN_PYTHON"] = sys.executable
    child_env["STRINGBEAN_NO_RUNTIME_BOOTSTRAP"] = "1"
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"

    output_path.touch(mode=0o600, exist_ok=False)
    try:
        process = subprocess.Popen(
            command,
            cwd=working_directory,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=(os.name == "posix"),
            preexec_fn=_configure_child_process if sys.platform.startswith("linux") else None,
        )
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
    run = Run(
        process=process,
        output_path=output_path,
        working_directory=working_directory,
        thread_id=thread_id,
    )
    RUNS[run_id] = run
    run.capture_thread = Thread(target=_capture_output, args=(run,), daemon=True)
    run.capture_thread.start()
    return {
        "run_id": run_id,
        "status": "running",
        "cursor": 0,
        "working_directory": str(working_directory),
        "runtime_python": sys.executable,
        "message": "Stringbean was started from the bundled versioned plugin runtime; call poll_sbx until complete.",
    }


def _decode_chunk(data: bytes, *, at_eof: bool) -> tuple[str, int]:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    text = decoder.decode(data, final=at_eof)
    pending, _ = decoder.getstate()
    return text, len(data) - len(pending)


def _final_workflow_status(output_path: Path) -> str | None:
    size = output_path.stat().st_size
    with output_path.open("rb") as output:
        output.seek(max(0, size - (256 * 1024)))
        tail = output.read().decode("utf-8", errors="replace")
    start = tail.rfind("STRINGBEAN_FINAL_START")
    if start < 0:
        return None
    end = tail.find("STRINGBEAN_FINAL_END", start)
    if end < 0:
        return None
    for line in tail[start:end].splitlines():
        if line.startswith("Status: "):
            value = line.removeprefix("Status: ").strip()
            return value or None
    return None


def _poll_sbx(arguments: dict[str, Any], call_meta: Any) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str):
        raise ValueError("run_id does not identify a Stringbean run from this session")
    cursor = arguments.get("cursor", 0)
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ValueError("cursor must be a non-negative integer")
    wait_seconds = arguments.get("wait_seconds", 5.0)
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)):
        raise ValueError("wait_seconds must be a number")
    wait_seconds = min(max(float(wait_seconds), 0.0), MAX_WAIT_SECONDS)

    terminal = TERMINAL_RESULTS.get(run_id)
    if terminal is not None:
        if terminal.thread_id != _thread_id(call_meta):
            raise ValueError("run_id belongs to a different Codex thread")
        terminal_cursor = int(terminal.payload["cursor"])
        if cursor == terminal.request_cursor:
            return dict(terminal.payload)
        if cursor == terminal_cursor:
            replay = dict(terminal.payload)
            replay["output"] = ""
            return replay
        raise ValueError("cursor does not match the retained terminal result")
    if run_id not in RUNS:
        raise ValueError("run_id does not identify a Stringbean run from this session")

    run = RUNS[run_id]
    if run.thread_id != _thread_id(call_meta):
        raise ValueError("run_id belongs to a different Codex thread")
    deadline = time.monotonic() + wait_seconds
    while True:
        size = run.output_path.stat().st_size
        exit_code = run.process.poll()
        if (
            size > cursor
            or (exit_code is not None and run.capture_done.is_set())
            or time.monotonic() >= deadline
        ):
            break
        time.sleep(0.1)

    if cursor > size:
        raise ValueError("cursor is beyond the available output")
    with run.output_path.open("rb") as output:
        output.seek(cursor)
        data = output.read(MAX_OUTPUT_BYTES)
    raw_end = cursor + len(data)
    exit_code = run.process.poll()
    # Once the process has exited, its writer is closed and a fresh stat is the
    # authoritative final size. The process can append and exit between the
    # first stat and read, so the earlier size must not mark a partial chunk as
    # complete.
    available_size = run.output_path.stat().st_size
    capture_done = run.capture_done.is_set()
    at_eof = raw_end >= available_size and exit_code is not None and capture_done
    text, consumed = _decode_chunk(data, at_eof=at_eof)
    next_cursor = cursor + consumed

    terminal_ready = exit_code is not None and capture_done and next_cursor >= available_size
    workflow_status = _final_workflow_status(run.output_path) if terminal_ready else None
    if exit_code is None:
        status = "running"
    elif not capture_done or next_cursor < available_size:
        status = "draining"
    else:
        if run.cancel_requested:
            status = "cancelled"
        elif run.output_overflow or run.capture_error:
            status = "failed"
        elif exit_code == 0 and workflow_status is None:
            status = "failed"
        elif workflow_status is not None and workflow_status.upper() == "FAILED":
            status = "failed"
        else:
            status = "completed" if exit_code == 0 else "failed"
    payload = {
        "run_id": run_id,
        "status": status,
        "cursor": next_cursor,
        "output": text,
        "exit_code": exit_code,
        "working_directory": str(run.working_directory),
    }
    if workflow_status is not None:
        payload["workflow_status"] = workflow_status
    if run.output_overflow:
        payload["error"] = "Stringbean output exceeded the 16 MiB safety limit"
    elif run.capture_error:
        payload["error"] = run.capture_error
    elif terminal_ready and exit_code == 0 and workflow_status is None:
        payload["error"] = "Stringbean exited without a complete final sentinel"
    elif workflow_status is not None and workflow_status.upper() == "FAILED":
        payload["error"] = "Stringbean reported workflow status FAILED"
    if status in {"completed", "failed", "cancelled"}:
        run.output_path.unlink(missing_ok=True)
        RUNS.pop(run_id, None)
        TERMINAL_RESULTS[run_id] = TerminalResult(
            thread_id=run.thread_id,
            request_cursor=cursor,
            payload=dict(payload),
        )
        TERMINAL_RESULTS.move_to_end(run_id)
        while len(TERMINAL_RESULTS) > MAX_TERMINAL_RESULTS:
            TERMINAL_RESULTS.popitem(last=False)
    return payload


def _signal_run(run: Run, sig: int) -> None:
    if run.process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(run.process.pid, sig)
            return
        except ProcessLookupError:
            return
    run.process.send_signal(sig)


def _stop_run(run: Run) -> None:
    with run.stop_lock:
        for sig, timeout in ((signal.SIGINT, 5.0), (signal.SIGTERM, 2.0)):
            _signal_run(run, sig)
            try:
                run.process.wait(timeout=timeout)
                return
            except subprocess.TimeoutExpired:
                pass
        _signal_run(run, signal.SIGKILL)
        try:
            run.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


def _cancel_sbx(arguments: dict[str, Any], call_meta: Any) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or run_id not in RUNS:
        raise ValueError("run_id does not identify a Stringbean run from this session")
    if arguments.get("confirmed_by_user") is not True:
        raise ValueError("confirmed_by_user must be true before cancelling Stringbean")
    run = RUNS[run_id]
    if run.thread_id != _thread_id(call_meta):
        raise ValueError("run_id belongs to a different Codex thread")
    if run.cancel_requested:
        return {
            "run_id": run_id,
            "status": "already_cancelled",
            "exit_code": run.process.poll(),
            "message": "Poll the run to drain its final output.",
        }
    if run.process.poll() is not None:
        return {
            "run_id": run_id,
            "status": "already_finished",
            "exit_code": run.process.returncode,
            "message": "Poll the run to read its natural completion status.",
        }
    run.cancel_requested = True
    _stop_run(run)
    return {
        "run_id": run_id,
        "status": "cancelled" if run.process.poll() is not None else "cancellation_failed",
        "exit_code": run.process.poll(),
        "message": "Poll the run to drain its final output.",
    }


TOOLS = [
    {
        "name": "start_sbx",
        "title": "Start Stringbean sbx",
        "description": (
            "Start one Stringbean orchestration from the plugin's bundled versioned runtime. This invokes "
            "configured hosted providers with the task text and non-excluded context from the "
            "current Codex workspace. Call only for an explicit $sbx/stringbean:sbx request."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {
                "task": {"type": "string", "minLength": 1, "maxLength": MAX_TASK_CHARS},
                "execution_profile": {"type": "string", "enum": ["ro", "rw"], "default": "rw"},
                "mode": {
                    "type": "string",
                    "enum": ["auto", "low", "medium", "high"],
                    "default": "auto",
                },
                "dry_run": {"type": "boolean", "default": False},
                "no_advisor": {"type": "boolean", "default": False},
                "max_review_rounds": {"type": "integer", "minimum": 0},
                "policy_retries": {"type": "integer", "minimum": 0},
            },
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    },
    {
        "name": "poll_sbx",
        "title": "Poll Stringbean sbx",
        "description": (
            "Read the next bounded output chunk from a Stringbean run. Continue until status is "
            "completed or failed and the final sentinel has been returned."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id", "cursor"],
            "properties": {
                "run_id": {"type": "string", "minLength": 1},
                "cursor": {"type": "integer", "minimum": 0},
                "wait_seconds": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": MAX_WAIT_SECONDS,
                    "default": 5,
                },
            },
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "cancel_sbx",
        "title": "Cancel Stringbean sbx",
        "description": (
            "Interrupt one active Stringbean process group only after the user explicitly confirms "
            "that its watchdog run should stop. Poll afterward to drain remaining output."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["run_id", "confirmed_by_user"],
            "properties": {
                "run_id": {"type": "string", "minLength": 1},
                "confirmed_by_user": {"const": True},
            },
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def _handle_request(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(method, str):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32600, "message": "Invalid JSON-RPC request"},
        }
    if request_id is None:
        return None
    if method == "initialize":
        params = message.get("params", {})
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": "initialize params must be an object"},
            }
        requested = params.get("protocolVersion")
        protocol_version = requested if isinstance(requested, str) else PROTOCOL_VERSION
        result = {
            "protocolVersion": protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "experimental": {SANDBOX_META_CAPABILITY: {}},
            },
            "serverInfo": {"name": "stringbean", "version": "0.2.0"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            result = _tool_result({"error": "tools/call params must be an object"}, is_error=True)
        else:
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                result = _tool_result({"error": "tool arguments must be an object"}, is_error=True)
            else:
                try:
                    if name == "start_sbx":
                        payload = _start_sbx(arguments, params.get("_meta"))
                    elif name == "poll_sbx":
                        payload = _poll_sbx(arguments, params.get("_meta"))
                    elif name == "cancel_sbx":
                        payload = _cancel_sbx(arguments, params.get("_meta"))
                    else:
                        raise ValueError(f"unknown Stringbean tool: {name}")
                    result = _tool_result(payload)
                except (OSError, RuntimeError, ValueError) as exc:
                    result = _tool_result({"error": str(exc)}, is_error=True)
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _cleanup() -> None:
    global _CLEANING_UP
    if _CLEANING_UP:
        return
    _CLEANING_UP = True
    for run in RUNS.values():
        if run.process.poll() is None:
            run.cancel_requested = True
            _stop_run(run)
        if run.capture_thread is not None:
            run.capture_thread.join(timeout=2.0)
    shutil.rmtree(SERVER_TEMP, ignore_errors=True)


def _fast_shutdown() -> None:
    global _CLEANING_UP
    if _CLEANING_UP:
        return
    _CLEANING_UP = True
    active = [run for run in RUNS.values() if run.process.poll() is None]
    for run in active:
        run.cancel_requested = True
        _signal_run(run, signal.SIGINT)
    for sig, grace in ((signal.SIGTERM, 0.5), (signal.SIGKILL, 0.25)):
        deadline = time.monotonic() + grace
        while any(run.process.poll() is None for run in active) and time.monotonic() < deadline:
            time.sleep(0.05)
        for run in active:
            if run.process.poll() is None:
                _signal_run(run, sig)
    for run in RUNS.values():
        if run.capture_thread is not None:
            run.capture_thread.join(timeout=0.25)
    shutil.rmtree(SERVER_TEMP, ignore_errors=True)


def _handle_shutdown(signum: int, _frame: Any) -> None:
    # Codex normally closes local MCP servers with SIGTERM. Python does not run
    # atexit handlers for that signal, so clean up detached run groups here.
    _fast_shutdown()
    raise SystemExit(128 + signum)


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        else:
            if not isinstance(message, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid JSON-RPC request"},
                }
            else:
                try:
                    response = _handle_request(message)
                except Exception as exc:
                    response = {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {"code": -32603, "message": f"Internal error: {exc}"},
                    }
        if response is not None:
            print(json.dumps(response, separators=(",", ":"), ensure_ascii=False), flush=True)
    return 0


atexit.register(_cleanup)
signal.signal(signal.SIGTERM, _handle_shutdown)
signal.signal(signal.SIGINT, _handle_shutdown)


if __name__ == "__main__":
    raise SystemExit(main())
