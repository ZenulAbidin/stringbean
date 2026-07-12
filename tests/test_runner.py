from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from agent_relay.runner import RunnerConfig, run_subprocess


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
