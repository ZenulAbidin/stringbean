from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time

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
