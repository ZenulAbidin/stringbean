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
    assert state.state.execution_profile == "rw"
    for status in [RunStatus.PLANNING, RunStatus.IMPLEMENTING, RunStatus.COMPLETED]:
        state.state.mark(status, datetime.now(timezone.utc))
        state.state.task = "changed"
        state.write()
        raw = run_dir.state_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        assert payload["status"] == status.value
        assert payload["stage"] == status.value


def test_legacy_state_without_execution_profile_loads_as_ro(tmp_path):
    run_dir = create_new_run(
        tmp_path,
        "20260101-120000-legacy",
        task="legacy state test",
        run_limit=10,
        selected_agents={},
    )
    payload = json.loads(run_dir.state_path.read_text(encoding="utf-8"))
    payload.pop("execution_profile")
    run_dir.state_path.write_text(json.dumps(payload), encoding="utf-8")

    state = RunState.load(run_dir.state_path)

    assert state.state.execution_profile == "ro"


def test_create_new_run_persists_rw_execution_profile(tmp_path):
    run_dir = create_new_run(
        tmp_path,
        "20260101-120000-new",
        task="new state test",
        run_limit=10,
        selected_agents={},
    )

    payload = json.loads(run_dir.state_path.read_text(encoding="utf-8"))
    manifest = json.loads(run_dir.manifest.read_text(encoding="utf-8"))

    assert payload["execution_profile"] == "rw"
    assert manifest["execution_profile"] == "rw"


def test_create_new_run_avoids_existing_run_artifacts(tmp_path):
    run_dir = create_new_run(
        tmp_path,
        "20260101-120000-repeat",
        task="original state test",
        run_limit=10,
        selected_agents={},
    )
    call_dir = run_dir.calls_dir / "001-implementer"
    call_dir.mkdir(parents=True)

    original_manifest = '{"sentinel": "manifest"}'
    original_state = '{"sentinel": "state"}'
    original_events = '{"sentinel": "event"}\n'
    original_stdout = "sentinel call output"
    run_dir.manifest.write_text(original_manifest, encoding="utf-8")
    run_dir.state_path.write_text(original_state, encoding="utf-8")
    run_dir.events_path.write_text(original_events, encoding="utf-8")
    (call_dir / "stdout.txt").write_text(original_stdout, encoding="utf-8")

    next_run_dir = create_new_run(
        tmp_path,
        "20260101-120000-repeat",
        task="replacement state test",
        run_limit=10,
        selected_agents={},
    )

    assert next_run_dir.run_id == "20260101-120000-repeat-2"
    assert next_run_dir.path != run_dir.path
    assert run_dir.manifest.read_text(encoding="utf-8") == original_manifest
    assert run_dir.state_path.read_text(encoding="utf-8") == original_state
    assert run_dir.events_path.read_text(encoding="utf-8") == original_events
    assert (call_dir / "stdout.txt").read_text(encoding="utf-8") == original_stdout

    next_state = json.loads(next_run_dir.state_path.read_text(encoding="utf-8"))
    next_manifest = json.loads(next_run_dir.manifest.read_text(encoding="utf-8"))
    assert next_state["run_id"] == "20260101-120000-repeat-2"
    assert next_manifest["run_id"] == "20260101-120000-repeat-2"
