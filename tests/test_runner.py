from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

import pytest

from agent_relay.runner import (
    RunnerConfig,
    WatchdogEvent,
    WatchdogTermination,
    _pump_stream,
    run_subprocess,
)


def test_run_subprocess_progress_callback_during_silent_process(tmp_path: Path):
    heartbeats: list[float] = []

    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[sys.executable, "-c", "import time; time.sleep(0.16); print('done')"],
                working_directory=tmp_path,
                on_progress=heartbeats.append,
                progress_interval_seconds=0.05,
            )
        )
    )

    assert heartbeats
    assert output.raw_stdout.strip() == "done"


def test_run_subprocess_forwards_small_output_before_process_exit(tmp_path: Path):
    callback_times: list[float] = []
    start = time.monotonic()

    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[
                    sys.executable,
                    "-u",
                    "-c",
                    "import time; print('first', flush=True); time.sleep(0.6); print('second')",
                ],
                working_directory=tmp_path,
                on_stdout_line=lambda _: callback_times.append(time.monotonic() - start),
            )
        )
    )

    assert callback_times
    assert callback_times[0] < 0.4
    assert output.raw_stdout == "first\nsecond\n"


def test_stream_pump_treats_closed_pipe_as_eof():
    class ClosedPipe:
        def read1(self, _size: int) -> bytes:
            raise ValueError("read of closed file")

    captured: list[str] = []
    _pump_stream(ClosedPipe(), captured.append, captured)

    assert captured == []


def test_idle_watchdog_without_approval_keeps_silent_process_alive(tmp_path: Path):
    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[sys.executable, "-c", "import time; time.sleep(0.22); print('done')"],
                working_directory=tmp_path,
                timeout_seconds=0,
                idle_timeout_seconds=0.08,
                max_repeated_output_lines=0,
            )
        )
    )

    assert output.exit_code == 0
    assert output.raw_stdout.strip() == "done"
    assert len(output.watchdog_events) == 1
    assert {event["decision"] for event in output.watchdog_events} == {"continue"}


def test_idle_watchdog_stops_only_after_explicit_approval(tmp_path: Path):
    events: list[WatchdogEvent] = []

    def approve(event: WatchdogEvent):
        events.append(event)
        return "terminate"

    with pytest.raises(WatchdogTermination, match="produced no output"):
        asyncio.run(
            run_subprocess(
                RunnerConfig(
                    command=[sys.executable, "-c", "import time; time.sleep(5)"],
                    working_directory=tmp_path,
                    timeout_seconds=0,
                    idle_timeout_seconds=0.08,
                    max_repeated_output_lines=0,
                    on_watchdog=approve,
                )
            )
        )

    assert [event.kind for event in events] == ["idle"]


def test_watchdog_callback_failure_is_not_termination_approval(tmp_path: Path):
    def broken_decider(_event: WatchdogEvent):
        raise RuntimeError("approval channel failed")

    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[sys.executable, "-c", "import time; time.sleep(0.16); print('done')"],
                working_directory=tmp_path,
                timeout_seconds=0,
                idle_timeout_seconds=0.08,
                max_repeated_output_lines=0,
                on_watchdog=broken_decider,
            )
        )
    )

    assert output.exit_code == 0
    assert output.raw_stdout.strip() == "done"
    assert output.watchdog_events[0]["decision"] == "continue"


def test_explicit_wall_clock_threshold_requests_approval_instead_of_killing(tmp_path: Path):
    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[sys.executable, "-c", "import time; time.sleep(0.18); print('done')"],
                working_directory=tmp_path,
                timeout_seconds=0.08,
                idle_timeout_seconds=0,
                max_repeated_output_lines=0,
            )
        )
    )

    assert output.exit_code == 0
    assert output.raw_stdout.strip() == "done"
    assert len(output.watchdog_events) == 1
    assert {event["kind"] for event in output.watchdog_events} == {"wall_clock"}
    assert {event["decision"] for event in output.watchdog_events} == {"continue"}


def test_regular_output_keeps_long_task_alive_past_idle_window(tmp_path: Path):
    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[
                    sys.executable,
                    "-u",
                    "-c",
                    "import time\nfor i in range(6): print(i, flush=True); time.sleep(0.05)",
                ],
                working_directory=tmp_path,
                timeout_seconds=0,
                idle_timeout_seconds=0.12,
                max_repeated_output_lines=20,
            )
        )
    )

    assert output.exit_code == 0
    assert output.raw_stdout.splitlines() == ["0", "1", "2", "3", "4", "5"]


def test_repeated_output_watchdog_warns_once_and_keeps_process_alive(tmp_path: Path):
    events: list[WatchdogEvent] = []

    def keep_running(event: WatchdogEvent):
        events.append(event)
        return "continue"

    output = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[
                    sys.executable,
                    "-u",
                    "-c",
                    "import time\nfor _ in range(20): print('same', flush=True); time.sleep(0.01)\nprint('done')",
                ],
                working_directory=tmp_path,
                timeout_seconds=0,
                idle_timeout_seconds=0,
                max_repeated_output_lines=5,
                on_watchdog=keep_running,
            )
        )
    )

    assert output.exit_code == 0
    assert output.raw_stdout.splitlines()[-1] == "done"
    assert [event.kind for event in events] == ["repeated_output"]


def test_repeated_output_watchdog_stops_only_after_explicit_approval(tmp_path: Path):
    with pytest.raises(WatchdogTermination, match="may be looping"):
        asyncio.run(
            run_subprocess(
                RunnerConfig(
                    command=[
                        sys.executable,
                        "-u",
                        "-c",
                        "import time\nwhile True: print('same', flush=True); time.sleep(0.01)",
                    ],
                    working_directory=tmp_path,
                    timeout_seconds=0,
                    idle_timeout_seconds=0,
                    max_repeated_output_lines=5,
                    on_watchdog=lambda _event: "terminate",
                )
            )
        )
