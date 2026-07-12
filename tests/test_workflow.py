from __future__ import annotations

import json
from pathlib import Path
import asyncio
import subprocess

import pytest

from agent_relay.config import AgentConfig, Config, OutputConfig, RepositoryConfig, WorkflowConfig
from agent_relay.state import RunState, create_new_run
from agent_relay.workflow import WorkflowEngine
from agent_relay.models import ImplementerResponse, RunStatus
from tests.helpers import write_fake_agent


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Stringbean Test"], cwd=repo, check=True)


def _commit_all(repo: Path, message: str = "baseline") -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


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
    assert result["result"] == "done #1"
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


def test_codex_progress_prints_sanitized_stage_updates(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    for agent in cfg.agents.values():
        agent.environment_overrides["EMIT_STDERR_STATUS"] = "1"

    run_dir = create_new_run(tmp_path, "run-codex-progress", "Audit bugs", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(
        cfg,
        run_dir,
        state,
        quiet=True,
        codex_progress=True,
        progress_interval_seconds=999,
    )

    result = asyncio.run(engine.run("Audit bugs"))

    captured = capsys.readouterr()
    assert result["status"] == "COMPLETED"
    assert "Progress: Selected agents" in captured.out
    assert "Agent: orchestrator planner started" in captured.out
    assert "Progress: Planning started" in captured.out
    assert "Progress: Plan summary" in captured.out
    assert "Progress: Advisor verdict" in captured.out
    assert "Progress: Implementation result" in captured.out
    assert "Progress: Review verdict" in captured.out
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


def test_agent_stream_decodes_visible_escapes(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-escapes", "Escaped output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk("alpha\\nbeta\\t1")
    engine._log("[stringbean] next")

    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines[0] == "alpha"
    assert lines[1].replace("    ", "\t") == "beta\t1"
    assert lines[2] == "[stringbean] next"


def test_agent_stream_formats_json_events(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-json", "JSON output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk('{"type":"agent_message","message":"hello\\nworld"}\n')

    captured = capsys.readouterr()
    assert captured.out == "assistant: hello\nassistant: world\n"


def test_agent_stream_suppresses_prompt_echo(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-prompt", "Prompt echo", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk("Reading prompt from stdin...\nuser\nSECRET PROMPT\ncodex\nFinal answer\n")

    captured = capsys.readouterr()
    assert "SECRET PROMPT" not in captured.out
    assert captured.out == "Final answer\n"


def test_agent_stream_collapses_pretty_structured_json(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-pretty-json", "Pretty JSON", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk(
        '{\n'
        '  "summary": "Use README.md as the answer.",\n'
        '  "tasks": [\n'
        '    {"title": "Confirm README"},\n'
        '    {"title": "Report result"}\n'
        '  ],\n'
        '  "risks": []\n'
        '}\n'
    )

    captured = capsys.readouterr()
    assert captured.out == "Plan: Use README.md as the answer.\n  - Confirm README\n  - Report result\n"


def test_agent_stream_hides_tool_output_body_and_tokens(tmp_path: Path, capsys):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-stream-tool-output", "Tool output", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    engine._stream_agent_chunk(
        '/usr/bin/zsh -lc "sed -n 1,80p README.md" in /repo\n'
        "succeeded in 12ms:\n"
        "first useful line\n"
        "second useful line\n"
        "third useful line\n"
        "FOURTH LINE SHOULD NOT PRINT\n"
        "codex\n"
        "tokens used\n"
        "1,234\n"
        '{"status":"completed","summary":"README.md exists."}\n'
        '{"status":"completed","summary":"README.md exists."}\n'
    )

    captured = capsys.readouterr()
    assert captured.out == (
        'Tool Call: /usr/bin/zsh -lc "sed -n 1,80p README.md"\n'
        "Executed: succeeded in 12ms\n"
        "  first useful line\n"
        "  second useful line\n"
        "  third useful line\n"
        "Result: completed — README.md exists.\n"
    )
    assert "FOURTH LINE" not in captured.out
    assert "1,234" not in captured.out


def test_agent_stream_labels_have_terminal_styles():
    line = WorkflowEngine._styled_stream_line("Tool Call: ls -1")

    assert line.plain == "Tool Call: ls -1"
    assert line.spans
    assert str(line.spans[0].style) == "bold white"


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


def test_ro_profile_allows_write_capable_agent_to_create_new_files_and_dirs(tmp_path: Path):
    _init_git_repo(tmp_path)
    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("notes").mkdir()
Path("notes/implemented.txt").write_text("changed\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "wrote",
    "files_changed": ["notes/implemented.txt"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg = Config(
        agents={
            "writer": AgentConfig(
                name="writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="writer", implementers=["writer"], reviewers=["writer"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-ro-policy", "RO policy", 10, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="ro")

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is None
    assert (tmp_path / "notes" / "implemented.txt").read_text(encoding="utf-8") == "changed\n"


def test_ro_profile_blocks_and_rolls_back_existing_file_modification(tmp_path: Path):
    _init_git_repo(tmp_path)
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").write_text("modified\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "modified",
    "files_changed": ["tracked.txt"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg = Config(
        agents={
            "writer": AgentConfig(
                name="writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="writer", implementers=["writer"], reviewers=["writer"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-ro-modify-policy", "RO policy", 10, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="ro")

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "read-only profile policy violation" in parse_error
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"


def test_ro_profile_blocks_rename_and_removes_new_target(tmp_path: Path):
    _init_git_repo(tmp_path)
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").rename("renamed.txt")
print(json.dumps({
    "status": "completed",
    "summary": "renamed",
    "files_changed": ["tracked.txt", "renamed.txt"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg = Config(
        agents={
            "writer": AgentConfig(
                name="writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="writer", implementers=["writer"], reviewers=["writer"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-ro-rename-policy", "RO policy", 10, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="ro")

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"
    assert not (tmp_path / "renamed.txt").exists()


def test_rw_profile_allows_write_capable_agent(tmp_path: Path):
    _init_git_repo(tmp_path)
    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("implemented.txt").write_text("changed\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "wrote",
    "files_changed": ["implemented.txt"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg = Config(
        agents={
            "writer": AgentConfig(
                name="writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="writer", implementers=["writer"], reviewers=["writer"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-rw-policy", "RW policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is None
    assert (tmp_path / "implemented.txt").read_text(encoding="utf-8") == "changed\n"


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
    import asyncio

    _init_git_repo(repo)
    baseline = repo / "tracked.txt"
    baseline.write_text("baseline\\n", encoding="utf-8")
    _commit_all(repo)

    script = repo / "agent.sh"
    script.write_text(
        """#!/usr/bin/env python3
import os
import json
import pathlib

role = os.environ.get(\"AGENT_ROLE\", \"orchestrator\")

if os.environ.get(\"WRITE_READ_ONLY_VIOLATION\", \"0\") == \"1\" and role == \"advisor\":
    pathlib.Path(\"tracked.txt\").write_text(\"should be blocked\\n\", encoding=\"utf-8\")

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

    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "baseline\\n"


def _write_policy_retry_agent(repo: Path) -> Path:
    script = repo / "policy_retry_agent.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path


def count(name):
    path = Path(os.environ["COUNT_FILE"])
    state = {}
    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    state[name] = int(state.get(name, 0)) + 1
    path.write_text(json.dumps(state), encoding="utf-8")
    return state[name]


def emit(payload):
    print(json.dumps(payload))


role = os.environ.get("AGENT_ROLE", "planner")
if role == "planner":
    emit({
        "summary": "plan",
        "assumptions": [],
        "tasks": [{
            "id": "task-1",
            "title": "Audit",
            "description": "Audit only",
            "dependencies": [],
            "recommended_role": "implementer",
            "permissions": "read_write",
            "verification": []
        }],
        "risks": [],
        "advisor_questions": []
    })
elif role == "advisor":
    attempt = count("advisor")
    if os.environ.get("ALWAYS_VIOLATE", "0") == "1" or attempt == 1:
        Path("tracked.txt").write_text(f"advisor changed tracked file on attempt {attempt}\\n", encoding="utf-8")
    emit({
        "verdict": "approve",
        "severity": "none",
        "summary": f"advisor attempt {attempt}",
        "blockers": [],
        "concerns": [],
        "recommendations": []
    })
elif role == "implementer":
    emit({
        "status": "completed",
        "summary": "implementation skipped for audit",
        "files_changed": [],
        "commands_run": [],
        "tests": [],
        "remaining_issues": [],
        "handoff_notes": []
    })
elif role == "reviewer":
    emit({
        "verdict": "approve",
        "summary": "review approved",
        "blocking_issues": [],
        "non_blocking_issues": [],
        "required_fixes": [],
        "tests_recommended": []
    })
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_policy_violation_retries_with_reframed_prompt(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    script = _write_policy_retry_agent(tmp_path)
    _commit_all(tmp_path)
    count_file = tmp_path.parent / f"{tmp_path.name}-policy-count.json"

    cfg = _build_config(
        script,
        planner_role="planner",
        advisor_role="advisor",
        implementer_role="implementer",
        reviewer_role="reviewer",
    )
    for agent in cfg.agents.values():
        agent.environment_overrides["COUNT_FILE"] = str(count_file)

    run_dir = create_new_run(tmp_path, "run-policy-retry", "Retry policy", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Retry policy"))

    assert result["status"] == "COMPLETED"
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"
    advisor_calls = sorted(path for path in run_dir.calls_dir.iterdir() if "advisor" in path.name)
    assert len(advisor_calls) == 2
    assert "Policy retry instruction" in advisor_calls[1].joinpath("prompt.md").read_text(encoding="utf-8")
    assert json.loads(count_file.read_text(encoding="utf-8"))["advisor"] == 2


def test_policy_violation_retry_detects_already_dirty_file_changes(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("committed baseline\n", encoding="utf-8")
    script = _write_policy_retry_agent(tmp_path)
    _commit_all(tmp_path)
    (tmp_path / "tracked.txt").write_text("dirty baseline\n", encoding="utf-8")
    count_file = tmp_path.parent / f"{tmp_path.name}-policy-dirty-count.json"

    cfg = _build_config(
        script,
        planner_role="planner",
        advisor_role="advisor",
        implementer_role="implementer",
        reviewer_role="reviewer",
    )
    for agent in cfg.agents.values():
        agent.environment_overrides["COUNT_FILE"] = str(count_file)

    run_dir = create_new_run(tmp_path, "run-policy-retry-dirty", "Retry policy", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Retry policy"))

    assert result["status"] == "COMPLETED"
    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "dirty baseline\n"
    advisor_calls = sorted(path for path in run_dir.calls_dir.iterdir() if "advisor" in path.name)
    assert len(advisor_calls) == 2
    assert "Policy retry instruction" in advisor_calls[1].joinpath("prompt.md").read_text(encoding="utf-8")
    assert json.loads(count_file.read_text(encoding="utf-8"))["advisor"] == 2


def test_policy_violation_retry_limit_is_enforced(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    script = _write_policy_retry_agent(tmp_path)
    _commit_all(tmp_path)
    count_file = tmp_path.parent / f"{tmp_path.name}-policy-count.json"

    cfg = _build_config(
        script,
        planner_role="planner",
        advisor_role="advisor",
        implementer_role="implementer",
        reviewer_role="reviewer",
    )
    cfg.workflow.max_policy_violation_retries = 1
    cfg.agents["advisor"].environment_overrides["ALWAYS_VIOLATE"] = "1"
    for agent in cfg.agents.values():
        agent.environment_overrides["COUNT_FILE"] = str(count_file)

    run_dir = create_new_run(tmp_path, "run-policy-retry-limit", "Retry policy", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)

    with pytest.raises(RuntimeError, match="read-only"):
        asyncio.run(engine.run("Retry policy"))

    assert (tmp_path / "tracked.txt").read_text(encoding="utf-8") == "baseline\n"
    advisor_calls = sorted(path for path in run_dir.calls_dir.iterdir() if "advisor" in path.name)
    assert len(advisor_calls) == 2
    assert json.loads(count_file.read_text(encoding="utf-8"))["advisor"] == 2


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
