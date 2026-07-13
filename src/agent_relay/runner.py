from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Thread
from typing import Callable, Dict, List, Optional

from .policy import command_policy_denial


@dataclass
class RunnerConfig:
    command: List[str]
    working_directory: Path
    env: Optional[Dict[str, str]] = None
    timeout_seconds: float = 1200
    prompt: Optional[str] = None
    on_stdout_line: Optional[Callable[[str], None]] = None
    on_stderr_line: Optional[Callable[[str], None]] = None
    on_progress: Optional[Callable[[float], None]] = None
    progress_interval_seconds: float = 30.0


@dataclass
class RunnerOutput:
    command: List[str]
    exit_code: Optional[int]
    duration_seconds: float
    start_time: str
    end_time: str
    raw_stdout: str
    raw_stderr: str


def _safe_invoke(callback: Callable[[str], None], line: str) -> None:
    try:
        callback(line)
    except Exception:
        # Output callbacks are best-effort telemetry.
        pass


def _safe_invoke_progress(callback: Callable[[float], None], elapsed_seconds: float) -> None:
    try:
        callback(elapsed_seconds)
    except Exception:
        # Progress callbacks are best-effort telemetry.
        pass


def _to_str_lines(buffer: bytes) -> str:
    return buffer.decode("utf-8", errors="replace")


def _pump_stream(stream: Optional[object], cb: Optional[Callable[[str], None]], out: list[str]) -> None:
    if stream is None:
        return
    read_available = getattr(stream, "read1", None)
    while True:
        # BufferedReader.read(size) can wait for the entire requested size. A
        # provider may emit a short event and then work silently for minutes,
        # so use read1() to forward currently available pipe bytes immediately.
        try:
            if callable(read_available):
                chunk = read_available(4096)
            else:
                chunk = stream.read(4096)
        except (OSError, ValueError):
            # Process finalization can close the pipe after the bounded thread
            # join but before this reader observes EOF.
            break
        if not chunk:
            break
        text = _to_str_lines(chunk)
        out.append(text)
        if cb:
            _safe_invoke(cb, text)


async def run_subprocess(cfg: RunnerConfig) -> RunnerOutput:
    start = time.time()
    start_iso = _timestamp_iso(start)
    timeout_seconds = float(cfg.timeout_seconds)
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    env = os.environ.copy()
    if cfg.env:
        env.update(cfg.env)

    denial = command_policy_denial(cfg.command, env)
    if denial is not None:
        end = time.time()
        return RunnerOutput(
            command=cfg.command,
            exit_code=126,
            duration_seconds=end - start,
            start_time=start_iso,
            end_time=_timestamp_iso(end),
            raw_stdout="",
            raw_stderr=f"{denial}\n",
        )

    proc = subprocess.Popen(
        cfg.command,
        cwd=str(cfg.working_directory),
        env=env,
        stdin=subprocess.PIPE if cfg.prompt is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    if cfg.prompt is not None and proc.stdin is not None:
        proc.stdin.write(cfg.prompt.encode("utf-8"))
        proc.stdin.close()

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    threads = [
        Thread(target=_pump_stream, args=(proc.stdout, cfg.on_stdout_line, stdout_chunks), daemon=True),
        Thread(target=_pump_stream, args=(proc.stderr, cfg.on_stderr_line, stderr_chunks), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        end_by = start + timeout_seconds
        next_progress_at = start + float(cfg.progress_interval_seconds)
        while True:
            code = proc.poll()
            if code is not None:
                break
            now = time.time()
            if now >= end_by:
                raise TimeoutError(f"process timed out after {timeout_seconds} seconds")
            if cfg.on_progress and cfg.progress_interval_seconds > 0 and now >= next_progress_at:
                _safe_invoke_progress(cfg.on_progress, now - start)
                next_progress_at = now + float(cfg.progress_interval_seconds)
            await asyncio.sleep(0.05)
    except TimeoutError:
        _terminate_process_group(proc)
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        raise
    except asyncio.CancelledError:
        _terminate_process_group(proc)
        try:
            code = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1)
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()

        if proc.poll() is None:
            try:
                code = proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                code = None

    if code is None:
        # Best effort fallback if finalization raced.
        code = proc.returncode

    end = time.time()
    end_iso = _timestamp_iso(end)

    return RunnerOutput(
        command=cfg.command,
        exit_code=code,
        duration_seconds=end - start,
        start_time=start_iso,
        end_time=end_iso,
        raw_stdout="".join(stdout_chunks),
        raw_stderr="".join(stderr_chunks),
    )


def _terminate_process_group(proc: subprocess.Popen) -> None:
    if proc.pid is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, NotImplementedError):
        pass
    except OSError:
        pass
    try:
        proc.terminate()
    except ProcessLookupError:
        return


def _timestamp_iso(seconds: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
