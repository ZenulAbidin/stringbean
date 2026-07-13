from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

import pytest

from agent_relay.runner import RunnerConfig, _pump_stream, run_subprocess


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


def test_idle_watchdog_stops_a_silent_stuck_process(tmp_path: Path):
    with pytest.raises(TimeoutError, match="produced no output"):
        asyncio.run(
            run_subprocess(
                RunnerConfig(
                    command=[sys.executable, "-c", "import time; time.sleep(5)"],
                    working_directory=tmp_path,
                    timeout_seconds=0,
                    idle_timeout_seconds=0.1,
                    max_repeated_output_lines=0,
                )
            )
        )


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


def test_repeated_output_watchdog_stops_an_obvious_loop(tmp_path: Path):
    with pytest.raises(TimeoutError, match="output loop"):
        asyncio.run(
            run_subprocess(
                RunnerConfig(
                    command=[
                        sys.executable,
                        "-u",
                        "-c",
                        "import time\nfor _ in range(20): print('same', flush=True); time.sleep(0.01)\ntime.sleep(5)",
                    ],
                    working_directory=tmp_path,
                    timeout_seconds=0,
                    idle_timeout_seconds=0,
                    max_repeated_output_lines=5,
                )
            )
        )
