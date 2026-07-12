from __future__ import annotations

import json
from pathlib import Path
import asyncio

import pytest

from agent_relay.config import AgentConfig, Config, OutputConfig, RepositoryConfig, WorkflowConfig
from agent_relay.state import RunState, create_new_run
from agent_relay.workflow import WorkflowEngine
from agent_relay.models import RunStatus
from tests.helpers import write_fake_agent


def _build_config(fake_agent: Path, *, reviewer_sequence: str = "approve", reviewer_role="reviewer", planner_role="planner", advisor_role="advisor", implementer_role="implementer", plan_revision: bool = False, require_clean: bool = False) -> Config:
    return Config(
        agents={
            "planner": AgentConfig(
                name="planner",
                adapter="generic",
                role="orchestrator",
                permissions="read_write",
                command=[str(fake_agent)],
                model="fake",
                environment_overrides={"AGENT_ROLE": planner_role, "PLAN_REVISION_ENABLED": "1" if plan_revision else "0"},
            ),
            "advisor": AgentConfig(
                name="advisor",
                adapter="generic",
                role="advisor",
                permissions="read_only",
                command=[str(fake_agent)],
                model="fake",
                environment_overrides={"AGENT_ROLE": advisor_role},
            ),
            "implementer": AgentConfig(
                name="implementer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(fake_agent)],
                model="fake",
                environment_overrides={"AGENT_ROLE": implementer_role},
                timeout_seconds=5,
            ),
            "reviewer": AgentConfig(
                name="reviewer",
                adapter="generic",
                role="reviewer",
                permissions="read_only",
                command=[str(fake_agent)],
                model="fake",
                environment_overrides={"AGENT_ROLE": reviewer_role, "REVIEW_SEQUENCE": reviewer_sequence},
            ),
        },
        workflow=WorkflowConfig(
            orchestrator="planner",
            advisors=["advisor"],
            implementers=["implementer"],
            reviewers=["reviewer"],
            max_total_agent_calls=20,
        ),
        repository=RepositoryConfig(require_clean_start=require_clean),
        output=OutputConfig(),
    )


def test_fake_run_plans_and_reviews(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    # ...
    run_dir = create_new_run(tmp_path, "run-1", "Build feature", 20, {})
    state = RunState.load(run_dir.state_path)

    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Build feature"))

    assert result["status"] == "COMPLETED"
    assert (run_dir.path / "plan.json").exists()
    assert result["review_round"] == 1
    assert (run_dir.path / "state.json").exists()
    assert any(p.name.startswith("001-") for p in (run_dir.calls_dir).iterdir())


def test_agent_output_streams_stdout_and_stderr_by_default(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    for agent in cfg.agents.values():
        agent.environment_overrides["EMIT_STDERR_STATUS"] = "1"

    run_dir = create_new_run(tmp_path, "run-stream-default", "Stream output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    result = asyncio.run(engine.run("Stream output"))

    captured = capsys.readouterr()
    assert result["status"] == "COMPLETED"
    assert "[stringbean] starting orchestrator agent: planner" in captured.out
    assert "stream output start" in captured.out
    assert "stderr status from planner" in captured.out


def test_agent_output_stream_can_be_disabled(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    cfg.output.stream_agent_output = False
    for agent in cfg.agents.values():
        agent.environment_overrides["EMIT_STDERR_STATUS"] = "1"

    run_dir = create_new_run(tmp_path, "run-stream-disabled", "Hide output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    result = asyncio.run(engine.run("Hide output"))

    captured = capsys.readouterr()
    assert result["status"] == "COMPLETED"
    assert "stream output start" not in captured.out
    assert "stderr status from planner" not in captured.out


def test_agent_stream_preserves_partial_chunks(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-chunks", "Chunk output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk("partial")
    engine._stream_agent_chunk("line\n")
    engine._log("[stringbean] next")

    captured = capsys.readouterr()
    assert captured.out == "partialline\n[stringbean] next\n"


def test_advisor_revision_leads_to_revised_plan(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(
        fake,
        reviewer_sequence="approve",
        planner_role="planner",
        advisor_role="advisor",
    )
    cfg.agents["advisor"].environment_overrides["ADVISOR_ALWAYS_REVISE"] = "1"
    run_dir = create_new_run(tmp_path, "run-2", "Revise plan", 20, {})
    state = RunState.load(run_dir.state_path)

    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Revise plan"))

    assert result["status"] == "COMPLETED"
    plan = json.loads((run_dir.path / "plan.json").read_text(encoding="utf-8"))
    assert len(plan["tasks"]) == 2


def test_reviewer_requests_changes_and_gets_fix_round(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake, reviewer_sequence="changes_requested,approve")
    run_dir = create_new_run(tmp_path, "run-3", "Fixes needed", 20, {})
    state = RunState.load(run_dir.state_path)

    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Fixes needed"))

    assert result["status"] == "COMPLETED"
    assert result["review_round"] >= 2
    assert state.state.review_round >= 2


def test_reviewer_max_round_enforcement(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake, reviewer_sequence="changes_requested,changes_requested", reviewer_role="reviewer")
    cfg.workflow.max_review_rounds = 1
    run_dir = create_new_run(tmp_path, "run-4", "No final", 20, {})
    state = RunState.load(run_dir.state_path)

    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("No final"))

    assert result["status"] == "FAILED"
    assert state.state.last_error in {"max review rounds exceeded", "reviewer rejected", "reviewer did not approve"}
    assert state.state.review_round == 1


def test_read_only_agent_cannot_modify(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake, reviewer_sequence="approve")
    cfg.agents["advisor"].permissions = "read_only"
    cfg.agents["advisor"].environment_overrides["AGENT_ROLE"] = "implementer"

    run_dir = create_new_run(tmp_path, "run-5", "Dirty read-only", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    with pytest.raises(RuntimeError):
        asyncio.run(engine.run("Dirty read-only"))


def test_resume_after_failed_stage(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    cfg.agents["implementer"].environment_overrides["IMPLEMENT_FAIL_FIRST"] = "1"

    run_id = "run-6"
    run_dir = create_new_run(tmp_path, run_id, "Resume", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    with pytest.raises(RuntimeError):
        asyncio.run(engine.run("Resume"))

    assert "implementer-complete" not in state.state.review_history
    cfg.agents["implementer"].environment_overrides["IMPLEMENT_FAIL_FIRST"] = "0"
    state = RunState.load(run_dir.state_path)
    resumed = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(resumed.run("Resume"))

    assert result["status"] == "COMPLETED"
    assert (tmp_path / "implemented.txt").exists()


def test_timeout_is_handled(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(
        fake,
        advisor_role="advisor",
        reviewer_role="reviewer",
        reviewer_sequence="approve",
    )
    cfg.agents["implementer"].environment_overrides["AGENT_ROLE"] = "timeout"
    cfg.agents["implementer"].environment_overrides["TIMEOUT_SECONDS"] = "1"
    cfg.agents["implementer"].timeout_seconds = 0.1

    run_dir = create_new_run(tmp_path, "run-7", "Timeout", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    with pytest.raises(RuntimeError):
        asyncio.run(engine.run("Timeout"))


def test_dry_run_shows_planned_stages(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-8", "Dry", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Dry", dry_run=True))

    assert result["dry_run"] is True
    assert RunStatus.PLANNING.value in result["stages"]
    assert result["state_dir"] == str(run_dir.path)


def test_prevent_concurrent_write_agents_by_sequential_calls(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    cfg.agents["advisor"].permissions = "read_only"

    # second implementer is intentionally not used in this orchestrator; this should keep execution sequential.
    cfg.agents["implementer-2"] = AgentConfig(
        name="implementer-2",
        adapter="generic",
        role="implementer",
        permissions="read_write",
        command=[str(fake)],
        model="fake",
        environment_overrides={"AGENT_ROLE": "implementer"},
    )

    cfg.workflow.implementers = ["implementer", "implementer-2"]
    run_dir = create_new_run(tmp_path, "run-9", "Parallel", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Parallel"))

    assert result["status"] == "COMPLETED"
    calls = sorted((run_dir.calls_dir).iterdir())
    # sequential execution ensures one implementer call is made for each task once in workflow order.
    task_calls = [c for c in calls if "implementer" in c.name]
    assert len(task_calls) == 1


def test_read_only_violations_are_rejected_and_rolled_back(tmp_path: Path):
    repo = tmp_path
    import subprocess
    import asyncio

    subprocess.run(["git", "init"], cwd=repo, check=False, capture_output=True)
    baseline = repo / "tracked.txt"
    baseline.write_text("baseline\\n", encoding="utf-8")

    script = repo / "agent.sh"
    script.write_text(
        """#!/usr/bin/env python3
import os
import json
import pathlib

role = os.environ.get(\"AGENT_ROLE\", \"orchestrator\")

if os.environ.get(\"WRITE_READ_ONLY_VIOLATION\", \"0\") == \"1\" and role == \"advisor\":
    pathlib.Path(\"read-only-breach.txt\").write_text(\"should be blocked\\n\", encoding=\"utf-8\")

if role == \"planner\":
    print(\"```json\")
    print(json.dumps({
      \"summary\": \"plan\",
      \"assumptions\": [],
      \"tasks\": [
        {
          \"id\": \"task-1\",
          \"title\": \"No-op\",
          \"description\": \"nothing\",
          \"dependencies\": [],
          \"recommended_role\": \"implementer\",
          \"permissions\": \"read_write\",
          \"verification\": []
        }
      ],
      \"risks\": [],
      \"advisor_questions\": []
    }))
    print(\"```\")
elif role == \"advisor\":
    print(\"```json\")
    print(json.dumps({
      \"verdict\": \"approve\",
      \"severity\": \"none\",
      \"summary\": \"looks good\",
      \"blockers\": [],
      \"concerns\": [],
      \"recommendations\": []
    }))
    print(\"```\")
elif role == \"implementer\":
    print(\"```json\")
    print(json.dumps({
      \"status\": \"completed\",
      \"summary\": \"implemented\",
      \"files_changed\": [],
      \"commands_run\": [],
      \"tests\": [],
      \"remaining_issues\": [],
      \"handoff_notes\": []
    }))
    print(\"```\")
elif role == \"reviewer\":
    print(\"```json\")
    print(json.dumps({
      \"verdict\": \"approve\",
      \"summary\": \"approved\",
      \"blocking_issues\": [],
      \"non_blocking_issues\": [],
      \"required_fixes\": [],
      \"tests_recommended\": []
    }))
    print(\"```\")
""",
        encoding="utf-8",
    )
    script.chmod(0o755)

    cfg = _build_config(script, advisor_role="advisor", reviewer_role="reviewer", planner_role="planner")
    cfg.workflow.max_total_agent_calls = 10
    cfg.agents["advisor"].environment_overrides["WRITE_READ_ONLY_VIOLATION"] = "1"
    cfg.agents["planner"].environment_overrides["AGENT_ROLE"] = "planner"

    run_dir = create_new_run(tmp_path, "run-readonly", "Read-only check", 10, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    with pytest.raises(RuntimeError):
        asyncio.run(engine.run("Read-only check"))

    assert not (tmp_path / "read-only-breach.txt").exists()


def test_role_mode_override_selects_matching_agent(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    cfg.agents["advisor-high"] = AgentConfig(
        name="advisor-high",
        adapter="generic",
        role="advisor",
        permissions="read_only",
        command=[str(fake)],
        model="fake",
        environment_overrides={"AGENT_ROLE": "advisor"},
        mode="high",
    )
    cfg.agents["advisor-low"] = AgentConfig(
        name="advisor-low",
        adapter="generic",
        role="advisor",
        permissions="read_only",
        command=[str(fake)],
        model="fake",
        environment_overrides={"AGENT_ROLE": "advisor"},
        mode="low",
    )
    cfg.workflow.advisors = ["advisor", "advisor-low", "advisor-high"]

    run_dir = create_new_run(tmp_path, "run-mode-1", "Small task", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    result = asyncio.run(engine.run("small task", dry_run=True, role_modes={"advisor": "high"}))

    assert result["selected_agents"]["advisor"] == "advisor-high"


def test_auto_mode_infers_mode_from_task_complexity(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    cfg.agents["advisor-high"] = AgentConfig(
        name="advisor-high",
        adapter="generic",
        role="advisor",
        permissions="read_only",
        command=[str(fake)],
        model="fake",
        environment_overrides={"AGENT_ROLE": "advisor"},
        mode="high",
    )
    cfg.agents["advisor-low"] = AgentConfig(
        name="advisor-low",
        adapter="generic",
        role="advisor",
        permissions="read_only",
        command=[str(fake)],
        model="fake",
        environment_overrides={"AGENT_ROLE": "advisor"},
        mode="low",
    )
    cfg.workflow.advisors = ["advisor-low", "advisor-high"]

    run_dir = create_new_run(tmp_path, "run-mode-2", "Fix typo", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    simple_result = asyncio.run(engine.run("Fix typo", dry_run=True))
    assert simple_result["selected_agents"]["advisor"] == "advisor-low"

    run_dir2 = create_new_run(tmp_path, "run-mode-3", "Refactor distributed architecture and rewrite migration flow", 20, {})
    state2 = RunState.load(run_dir2.state_path)
    engine2 = WorkflowEngine(cfg, run_dir2, state2)
    complex_result = asyncio.run(engine2.run("Refactor distributed architecture and rewrite migration flow", dry_run=True))
    assert complex_result["selected_agents"]["advisor"] == "advisor-high"
