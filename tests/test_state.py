from __future__ import annotations

import json
from datetime import datetime, timezone

from agent_relay.models import RunStatus
from agent_relay.state import RunState, create_new_run


def test_state_is_written_atomically(tmp_path):
    run_dir = create_new_run(
        tmp_path,
        "20260101-120000-test",
        task="state test",
        run_limit=10,
        selected_agents={},
    )
    state = RunState.load(run_dir.state_path)
    for status in [RunStatus.PLANNING, RunStatus.IMPLEMENTING, RunStatus.COMPLETED]:
        state.state.mark(status, datetime.now(timezone.utc))
        state.state.task = "changed"
        state.write()
        raw = run_dir.state_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert payload["status"] == status.value
        assert payload["stage"] == status.value
