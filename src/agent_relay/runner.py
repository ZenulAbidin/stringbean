from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock, Thread
from typing import Callable, Dict, List, Literal, Optional

from .policy import command_policy_denial


WatchdogDecision = Literal["continue", "terminate"]
WatchdogKind = Literal["wall_clock", "idle", "repeated_output"]


@dataclass(frozen=True)
class WatchdogEvent:
    kind: WatchdogKind
    message: str
    elapsed_seconds: float
    threshold: float | int
    repeated_line: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "message": self.message,
            "elapsed_seconds": self.elapsed_seconds,
            "threshold": self.threshold,
            "repeated_line": self.repeated_line,
        }


class WatchdogTermination(TimeoutError):
    """Raised only after a watchdog callback explicitly approves termination."""

    def __init__(self, event: WatchdogEvent) -> None:
        self.event = event
        super().__init__(event.message)


@dataclass
class RunnerConfig:
    command: List[str]
    working_directory: Path
    env: Optional[Dict[str, str]] = None
    timeout_seconds: float = 0
    idle_timeout_seconds: float = 7200
    max_repeated_output_lines: int = 200
    prompt: Optional[str] = None
    on_stdout_line: Optional[Callable[[str], None]] = None
    on_stderr_line: Optional[Callable[[str], None]] = None
    on_progress: Optional[Callable[[float], None]] = None
    on_watchdog: Optional[Callable[[WatchdogEvent], WatchdogDecision]] = None
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
    watchdog_events: List[Dict[str, object]] = field(default_factory=list)


@dataclass
class _OutputWatchdog:
    max_repeated_output_lines: int
    last_activity: float = field(default_factory=time.monotonic)
    acknowledged_idle_activity: float | None = None
    last_line: str = ""
    repeated_lines: int = 0
    repeated_line: str = ""
    acknowledged_repeated_line: str = ""
    lock: Lock = field(default_factory=Lock)

    def observe(self, text: str) -> None:
        with self.lock:
            self.last_activity = time.monotonic()
            self.acknowledged_idle_activity = None
            for raw_line in text.splitlines():
                line = " ".join(raw_line.split())
                if not line:
                    continue
                if line == self.last_line:
                    self.repeated_lines += 1
                else:
                    self.last_line = line
                    self.repeated_lines = 1
                    self.repeated_line = ""
                    self.acknowledged_repeated_line = ""
                if (
                    self.max_repeated_output_lines > 0
                    and self.repeated_lines >= self.max_repeated_output_lines
                    and line != self.acknowledged_repeated_line
                ):
                    self.repeated_line = line[:160]

    def snapshot(self) -> tuple[float, bool, int, str]:
        with self.lock:
            return (
                self.last_activity,
                self.acknowledged_idle_activity == self.last_activity,
                self.repeated_lines,
                self.repeated_line,
            )

    def acknowledge(self) -> None:
        with self.lock:
            self.acknowledged_idle_activity = self.last_activity
            if self.repeated_line:
                self.acknowledged_repeated_line = self.last_line
                self.repeated_line = ""


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


def _safe_watchdog_decision(
    callback: Optional[Callable[[WatchdogEvent], WatchdogDecision]],
    event: WatchdogEvent,
) -> WatchdogDecision:
    if callback is None:
        return "continue"
    try:
        decision = callback(event)
    except Exception:
        # Failure to obtain approval must never become implicit approval.
        return "continue"
    if decision == "terminate":
        return "terminate"
    return "continue"


def _to_str_lines(buffer: bytes) -> str:
    return buffer.decode("utf-8", errors="replace")


def _pump_stream(
    stream: Optional[object],
    cb: Optional[Callable[[str], None]],
    out: list[str],
    watchdog: _OutputWatchdog | None = None,
) -> None:
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
        if watchdog is not None:
            watchdog.observe(text)
        if cb:
            _safe_invoke(cb, text)


async def run_subprocess(cfg: RunnerConfig) -> RunnerOutput:
    start = time.time()
    monotonic_start = time.monotonic()
    start_iso = _timestamp_iso(start)
    timeout_seconds = float(cfg.timeout_seconds)
    idle_timeout_seconds = float(cfg.idle_timeout_seconds)
    max_repeated_output_lines = int(cfg.max_repeated_output_lines)
    if timeout_seconds < 0:
        raise ValueError("timeout_seconds must be 0 (disabled) or greater")
    if idle_timeout_seconds < 0:
        raise ValueError("idle_timeout_seconds must be 0 (disabled) or greater")
    if max_repeated_output_lines < 0:
        raise ValueError("max_repeated_output_lines must be 0 (disabled) or greater")

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
            watchdog_events=[],
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
    watchdog = _OutputWatchdog(max_repeated_output_lines=max_repeated_output_lines)
    watchdog_events: list[Dict[str, object]] = []

    threads = [
        Thread(target=_pump_stream, args=(proc.stdout, cfg.on_stdout_line, stdout_chunks, watchdog), daemon=True),
        Thread(target=_pump_stream, args=(proc.stderr, cfg.on_stderr_line, stderr_chunks, watchdog), daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        end_by = monotonic_start + timeout_seconds if timeout_seconds > 0 else None
        wall_clock_acknowledged = False
        next_progress_at = monotonic_start + float(cfg.progress_interval_seconds)
        while True:
            code = proc.poll()
            if code is not None:
                break
            now = time.monotonic()
            watchdog_event: WatchdogEvent | None = None
            if end_by is not None and not wall_clock_acknowledged and now >= end_by:
                watchdog_event = WatchdogEvent(
                    kind="wall_clock",
                    message=(
                        f"configured wall-clock threshold of {timeout_seconds:g} seconds was reached; "
                        "completion time is unknown"
                    ),
                    elapsed_seconds=now - monotonic_start,
                    threshold=timeout_seconds,
                )
            last_activity, idle_acknowledged, repeated_lines, repeated_line = watchdog.snapshot()
            if (
                watchdog_event is None
                and idle_timeout_seconds > 0
                and not idle_acknowledged
                and now - last_activity >= idle_timeout_seconds
            ):
                watchdog_event = WatchdogEvent(
                    kind="idle",
                    message=(
                        f"process produced no output for {idle_timeout_seconds:g} seconds; "
                        "it may be waiting or stuck"
                    ),
                    elapsed_seconds=now - monotonic_start,
                    threshold=idle_timeout_seconds,
                )
            if watchdog_event is None and max_repeated_output_lines > 0 and repeated_line:
                watchdog_event = WatchdogEvent(
                    kind="repeated_output",
                    message=(
                        f"process repeated one line {repeated_lines} times and may be looping: "
                        f"{repeated_line}"
                    ),
                    elapsed_seconds=now - monotonic_start,
                    threshold=max_repeated_output_lines,
                    repeated_line=repeated_line,
                )
            if watchdog_event is not None:
                decision = _safe_watchdog_decision(cfg.on_watchdog, watchdog_event)
                watchdog_events.append({**watchdog_event.as_dict(), "decision": decision})
                if decision == "terminate":
                    if proc.poll() is not None:
                        code = proc.returncode
                        break
                    raise WatchdogTermination(watchdog_event)
                watchdog.acknowledge()
                if watchdog_event.kind == "wall_clock":
                    wall_clock_acknowledged = True
                continue
            if cfg.on_progress and cfg.progress_interval_seconds > 0 and now >= next_progress_at:
                _safe_invoke_progress(cfg.on_progress, now - monotonic_start)
                next_progress_at = now + float(cfg.progress_interval_seconds)
            await asyncio.sleep(0.05)
    except WatchdogTermination:
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
        watchdog_events=watchdog_events,
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
