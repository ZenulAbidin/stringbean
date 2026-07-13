from __future__ import annotations

import json
from pathlib import Path
import asyncio
import os
import subprocess

import pytest

from agent_relay.config import AgentConfig, Config, OutputConfig, RepositoryConfig, WorkflowConfig
from agent_relay.state import RunState, create_new_run
from agent_relay.workflow import WorkflowEngine
from agent_relay.models import ImplementerResponse, RunStatus
from agent_relay.policy import git_command, install_command_policy_wrappers, internal_subprocess_env
from tests.helpers import write_fake_agent


def _init_git_repo(repo: Path) -> None:
    subprocess.run([git_command(), "init"], cwd=repo, check=True, capture_output=True, env=internal_subprocess_env())
    subprocess.run([git_command(), "config", "user.email", "test@example.com"], cwd=repo, check=True, env=internal_subprocess_env())
    subprocess.run([git_command(), "config", "user.name", "Stringbean Test"], cwd=repo, check=True, env=internal_subprocess_env())


def _commit_all(repo: Path, message: str = "baseline") -> None:
    subprocess.run([git_command(), "add", "."], cwd=repo, check=True, capture_output=True, env=internal_subprocess_env())
    subprocess.run([git_command(), "commit", "-m", message], cwd=repo, check=True, capture_output=True, env=internal_subprocess_env())


def _status_engine_for_repo(repo: Path, run_root: Path) -> WorkflowEngine:
    run_dir = create_new_run(run_root, f"run-status-{repo.name}", "Status parsing", 10, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_build_config(run_root / "unused-agent"), run_dir, state, quiet=True)
    engine.repo_root = repo
    return engine


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


def _single_read_only_agent_config(script: Path) -> Config:
    return Config(
        agents={
            "reader": AgentConfig(
                name="reader",
                adapter="generic",
                role="implementer",
                permissions="read_only",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="reader", implementers=["reader"], reviewers=["reader"]),
        output=OutputConfig(),
    )


def test_subagent_policy_env_drops_stale_policy_bin_from_parent_path(tmp_path: Path, monkeypatch):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    stale_policy_bin = install_command_policy_wrappers(tmp_path / "stale")
    monkeypatch.setenv("PATH", f"{stale_policy_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    run_dir = create_new_run(tmp_path, "run-policy-path", "Policy PATH", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_build_config(fake), run_dir, state)

    path_parts = engine._apply_subagent_policy_env({})["PATH"].split(os.pathsep)

    assert path_parts[0] == str(engine.policy_bin_dir)
    assert str(stale_policy_bin) not in path_parts[1:]


def test_subagent_policy_env_keeps_unrelated_policy_bin_in_parent_path(tmp_path: Path, monkeypatch):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    unrelated_policy_bin = tmp_path / "tools" / "policy-bin"
    unrelated_policy_bin.mkdir(parents=True)
    monkeypatch.setenv("PATH", f"{unrelated_policy_bin}{os.pathsep}{os.environ.get('PATH', '')}")

    run_dir = create_new_run(tmp_path, "run-unrelated-policy-path", "Policy PATH", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_build_config(fake), run_dir, state)

    path_parts = engine._apply_subagent_policy_env({})["PATH"].split(os.pathsep)

    assert path_parts[0] == str(engine.policy_bin_dir)
    assert str(unrelated_policy_bin) in path_parts[1:]


def test_repo_delta_detects_modified_path_with_spaces(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tracked = repo / "tracked file.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _commit_all(repo)
    engine = _status_engine_for_repo(repo, tmp_path / "runs")

    before = engine._repo_status_snapshot()
    tracked.write_text("changed\n", encoding="utf-8")
    after = engine._repo_status_snapshot()
    changed, allowed, denied = engine._classify_repo_delta(before, after, allow_creates=True)

    assert before == {}
    assert after["tracked file.txt"] == " M"
    assert engine._display_status_paths(changed) == ["tracked file.txt"]
    assert allowed == []
    assert engine._display_status_paths(denied) == ["tracked file.txt"]


def test_repo_delta_detects_renamed_paths_with_spaces(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    old_path = repo / "old tracked file.txt"
    old_path.write_text("baseline\n", encoding="utf-8")
    _commit_all(repo)
    engine = _status_engine_for_repo(repo, tmp_path / "runs")

    before = engine._repo_status_snapshot()
    subprocess.run(
        [git_command(), "mv", "old tracked file.txt", "new tracked file.txt"],
        cwd=repo,
        check=True,
        capture_output=True,
        env=internal_subprocess_env(),
    )
    after = engine._repo_status_snapshot()
    changed, allowed, denied = engine._classify_repo_delta(before, after, allow_creates=True)

    assert before == {}
    assert len(after) == 1
    changed_key = changed[0]
    assert after[changed_key] == "R "
    assert engine._split_status_path(changed_key) == ("old tracked file.txt", "new tracked file.txt")
    assert engine._display_status_paths(changed) == ["old tracked file.txt -> new tracked file.txt"]
    assert allowed == []
    assert engine._display_status_paths(denied) == ["old tracked file.txt -> new tracked file.txt"]


def test_repo_delta_keeps_arrow_like_filename_as_single_path(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    tracked = repo / "notes -> draft.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    _commit_all(repo)
    engine = _status_engine_for_repo(repo, tmp_path / "runs")

    before = engine._repo_status_snapshot()
    tracked.write_text("changed\n", encoding="utf-8")
    after = engine._repo_status_snapshot()
    changed, allowed, denied = engine._classify_repo_delta(before, after, allow_creates=True)

    assert before == {}
    assert after["notes -> draft.txt"] == " M"
    assert engine._split_status_path(changed[0]) == ("notes -> draft.txt", None)
    assert engine._display_status_paths(changed) == ["notes -> draft.txt"]
    assert allowed == []
    assert engine._display_status_paths(denied) == ["notes -> draft.txt"]


def test_repo_status_entries_parses_porcelain_z_static_bytes():
    entries = WorkflowEngine._repo_status_entries(
        b" M plain.txt\0"
        b"R  renamed-new.txt\0renamed-old.txt\0"
        b" R unstaged-new.txt\0unstaged-old.txt\0"
        b"C  copied-new.txt\0copied-old.txt\0"
        b" D deleted.txt\0"
        b" T typechanged.txt\0"
        b"?? arrow -> literal.txt\0"
        b" M tab\tname.txt\0"
        b" M quote\"name.txt\0"
    )

    assert entries["plain.txt"] == " M"
    assert entries["renamed-old.txt\0renamed-new.txt"] == "R "
    assert entries["unstaged-old.txt\0unstaged-new.txt"] == " R"
    assert entries["copied-old.txt\0copied-new.txt"] == "C "
    assert entries["deleted.txt"] == " D"
    assert entries["typechanged.txt"] == " T"
    assert entries["arrow -> literal.txt"] == "??"
    assert entries["tab\tname.txt"] == " M"
    assert entries["quote\"name.txt"] == " M"


def test_repo_status_entries_parses_porcelain_non_z_static_bytes():
    entries = WorkflowEngine._repo_status_entries(
        b" M plain.txt\n"
        b"R  renamed old.txt -> renamed new.txt\n"
        b" R unstaged old.txt -> unstaged new.txt\n"
        b"C  copied old.txt -> copied new.txt\n"
        b"R  \"old -> arrow.txt\" -> \"new -> arrow.txt\"\n"
        b" D deleted.txt\n"
        b" T typechanged.txt\n"
        b"?? arrow -> literal.txt\n"
        b" M \"tab\\tname.txt\"\n"
        b" M \"quote\\\"name.txt\"\n"
        b" M \"utf8-\\303\\251.txt\"\n"
    )

    assert entries["plain.txt"] == " M"
    assert entries["renamed old.txt\0renamed new.txt"] == "R "
    assert entries["unstaged old.txt\0unstaged new.txt"] == " R"
    assert entries["copied old.txt\0copied new.txt"] == "C "
    assert entries["old -> arrow.txt\0new -> arrow.txt"] == "R "
    assert entries["deleted.txt"] == " D"
    assert entries["typechanged.txt"] == " T"
    assert entries["arrow -> literal.txt"] == "??"
    assert entries["tab\tname.txt"] == " M"
    assert entries["quote\"name.txt"] == " M"
    assert entries["utf8-\xe9.txt"] == " M"


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


def test_environment_redaction_preserves_runtime_secret_and_redacts_call_artifacts(tmp_path: Path):
    script = tmp_path / "secret_agent.py"
    script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path


secret = os.environ["API_KEY"]
Path(os.environ["RECEIVED_FILE"]).write_text(secret, encoding="utf-8")

print(f"runtime api key: {secret}")
print("```json")
print(json.dumps({
    "status": "completed",
    "summary": f"received {secret}",
    "files_changed": [],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": [],
}))
print("```")
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    secret = "original-api-secret-123"
    received_file = tmp_path / "received-api-key.txt"
    cfg = Config(
        agents={
            "implementer": AgentConfig(
                name="implementer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
                environment_overrides={"API_KEY": secret, "RECEIVED_FILE": str(received_file)},
            )
        },
        workflow=WorkflowConfig(orchestrator="implementer", implementers=["implementer"], reviewers=["implementer"]),
        repository=RepositoryConfig(require_git=False),
        output=OutputConfig(stream_agent_output=False, redact_environment_values=True),
    )
    run_dir = create_new_run(tmp_path, "run-env-redaction", "Check secret env", 20, {})
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, quiet=True)

    call_result, parse_error = asyncio.run(
        engine._run_agent(
            "implementer",
            "implementer",
            RunStatus.IMPLEMENTING,
            "Check secret env",
            ImplementerResponse,
            track_repo_diff=False,
        )
    )

    assert parse_error is None
    assert received_file.read_text(encoding="utf-8") == secret
    assert call_result.parsed_output is not None
    assert call_result.parsed_output["summary"] == "received REDACTED"

    call_dir = run_dir.calls_dir / "001-implementer"
    artifact_text = "\n".join(
        (call_dir / name).read_text(encoding="utf-8")
        for name in ("stdout.txt", "stderr.txt", "result.json", "metadata.json")
    )
    assert secret not in artifact_text
    assert "runtime api key: REDACTED" in (call_dir / "stdout.txt").read_text(encoding="utf-8")
    assert "received REDACTED" in (call_dir / "result.json").read_text(encoding="utf-8")


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
    run_dir = create_new_run(tmp_path, "run-ro-policy", "RO policy", 10, {}, execution_profile="ro")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="ro")

    result, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert state.state.execution_profile == "ro"
    assert parse_error is None
    assert result.metadata["selected_agent"] == "writer"
    assert result.metadata["effective_agent"] == "writer"
    assert result.metadata["requested_profile"] == "ro"
    assert result.metadata["effective_profile"] == "ro"
    assert result.metadata["execution_profile"] == "ro"
    assert result.metadata["effective_permission"] == "read_only"
    assert result.metadata["policy_bin"] == str(engine.policy_bin_dir)
    assert result.metadata["policy_wrappers_active"] is True
    assert isinstance(result.metadata["policy_preload_active"], bool)
    assert (tmp_path / "notes" / "implemented.txt").read_text(encoding="utf-8") == "changed\n"


def test_read_only_agent_rejects_and_rolls_back_ignored_file_changes(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("ignored/\ncache/\n", encoding="utf-8")
    ignored_dir = tmp_path / "ignored"
    ignored_dir.mkdir()
    existing = ignored_dir / "existing.cache"
    existing.write_text("baseline\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("ignored/existing.cache").write_text("modified\\n", encoding="utf-8")
Path("cache").mkdir()
Path("cache/new.cache").write_text("new\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "modified ignored files",
    "files_changed": ["ignored/existing.cache", "cache/new.cache"],
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
            "reader": AgentConfig(
                name="reader",
                adapter="generic",
                role="advisor",
                permissions="read_only",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="reader", implementers=["reader"], reviewers=["reader"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-ignored-readonly", "Ignored policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "advisor", RunStatus.ADVISOR_REVIEW, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "read-only role policy violation" in parse_error
    assert "ignored/existing.cache" in parse_error
    assert "cache/new.cache" in parse_error
    assert existing.read_text(encoding="utf-8") == "baseline\n"
    assert not (tmp_path / "cache").exists()


def test_read_only_rollback_removes_empty_parents_for_new_untracked_nested_paths(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _commit_all(tmp_path)
    preexisting_parent = tmp_path / "existing" / "empty"
    preexisting_parent.mkdir(parents=True)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("scratch/nested").mkdir(parents=True)
Path("scratch/nested/new.txt").write_text("new\\n", encoding="utf-8")
Path("existing/empty/new.txt").write_text("new\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "created nested untracked files",
    "files_changed": ["scratch/nested/new.txt", "existing/empty/new.txt"],
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
            "reader": AgentConfig(
                name="reader",
                adapter="generic",
                role="advisor",
                permissions="read_only",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="reader", implementers=["reader"], reviewers=["reader"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-untracked-parent-rollback", "Untracked policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "advisor", RunStatus.ADVISOR_REVIEW, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "read-only role policy violation" in parse_error
    assert "scratch/nested/new.txt" in parse_error
    assert "existing/empty/new.txt" in parse_error
    assert not (tmp_path / "scratch").exists()
    assert preexisting_parent.is_dir()
    assert list(preexisting_parent.iterdir()) == []


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
    run_dir = create_new_run(tmp_path, "run-ro-modify-policy", "RO policy", 10, {}, execution_profile="ro")
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
    run_dir = create_new_run(tmp_path, "run-ro-rename-policy", "RO policy", 10, {}, execution_profile="ro")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="ro")

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"
    assert not (tmp_path / "renamed.txt").exists()


def test_read_only_rollback_restores_modified_symlink(tmp_path: Path):
    _init_git_repo(tmp_path)
    (tmp_path / "target.txt").write_text("target\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("other\n", encoding="utf-8")
    os.symlink("target.txt", tmp_path / "link.txt")
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os

os.unlink("link.txt")
os.symlink("other.txt", "link.txt")
print(json.dumps({
    "status": "completed",
    "summary": "modified symlink",
    "files_changed": ["link.txt"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    run_dir = create_new_run(tmp_path, "run-readonly-symlink-rollback", "Symlink rollback", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_single_read_only_agent_config(script), run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "read-only role policy violation" in parse_error
    assert (tmp_path / "link.txt").is_symlink()
    assert os.readlink(tmp_path / "link.txt") == "target.txt"


def test_read_only_rollback_preserves_executable_mode(tmp_path: Path):
    _init_git_repo(tmp_path)
    tool = tmp_path / "tool.sh"
    tool.write_text("#!/bin/sh\necho baseline\n", encoding="utf-8")
    tool.chmod(0o755)
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path

Path("tool.sh").write_text("#!/bin/sh\\necho modified\\n", encoding="utf-8")
os.chmod("tool.sh", 0o644)
print(json.dumps({
    "status": "completed",
    "summary": "modified mode",
    "files_changed": ["tool.sh"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    run_dir = create_new_run(tmp_path, "run-readonly-mode-rollback", "Mode rollback", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_single_read_only_agent_config(script), run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert tool.read_text(encoding="utf-8") == "#!/bin/sh\necho baseline\n"
    assert os.stat(tool).st_mode & 0o777 == 0o755


def test_read_only_rollback_restores_file_to_directory_change(tmp_path: Path):
    _init_git_repo(tmp_path)
    artifact = tmp_path / "artifact"
    artifact.write_text("baseline file\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path

os.unlink("artifact")
Path("artifact").mkdir()
Path("artifact/data.txt").write_text("directory replacement\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "changed file to directory",
    "files_changed": ["artifact"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    run_dir = create_new_run(tmp_path, "run-readonly-file-dir-rollback", "Type rollback", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_single_read_only_agent_config(script), run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert artifact.is_file()
    assert artifact.read_text(encoding="utf-8") == "baseline file\n"


def test_read_only_rollback_restores_directory_to_file_change(tmp_path: Path):
    _init_git_repo(tmp_path)
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    nested = artifact / "data.txt"
    nested.write_text("baseline directory\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
import shutil
from pathlib import Path

shutil.rmtree("artifact")
Path("artifact").write_text("file replacement\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "summary": "changed directory to file",
    "files_changed": ["artifact"],
    "commands_run": [],
    "tests": [],
    "remaining_issues": [],
    "handoff_notes": []
}))
""",
        encoding="utf-8",
    )
    script.chmod(0o755)
    run_dir = create_new_run(tmp_path, "run-readonly-dir-file-rollback", "Type rollback", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(_single_read_only_agent_config(script), run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert artifact.is_dir()
    assert nested.read_text(encoding="utf-8") == "baseline directory\n"


@pytest.mark.parametrize(
    ("execution_profile", "agent_permission", "expected_message"),
    [
        ("rw", "read_only", "read-only role policy violation"),
        ("ro", "read_write", "read-only profile policy violation"),
    ],
)
def test_non_git_read_only_tracking_rejects_existing_file_modification(
    tmp_path: Path,
    execution_profile: str,
    agent_permission: str,
    expected_message: str,
):
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")

    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").write_text("modified outside git\\n", encoding="utf-8")
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
                permissions=agent_permission,
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="writer", implementers=["writer"], reviewers=["writer"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(
        tmp_path,
        f"run-non-git-{execution_profile}-{agent_permission}",
        "Policy",
        10,
        {},
        execution_profile=execution_profile,
    )
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile=execution_profile)

    _, parse_error = asyncio.run(
        engine._run_agent("writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert expected_message in parse_error
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"


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


def test_rw_profile_rejects_read_only_role_edits(tmp_path: Path):
    _init_git_repo(tmp_path)
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")
    _commit_all(tmp_path)

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").write_text("read-only changed\\n", encoding="utf-8")
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
            "reader": AgentConfig(
                name="reader",
                adapter="generic",
                role="implementer",
                permissions="read_only",
                command=[str(script)],
            )
        },
        workflow=WorkflowConfig(orchestrator="reader", implementers=["reader"], reviewers=["reader"]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-rw-read-only-policy", "RW policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    _, parse_error = asyncio.run(
        engine._run_agent("reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert parse_error is not None
    assert "read-only role policy violation" in parse_error
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"


def test_rw_profile_rejects_read_only_fallback_edits(tmp_path: Path):
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")

    script = tmp_path / "reader.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").write_text("read-only fallback changed\\n", encoding="utf-8")
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
            "unavailable-writer": AgentConfig(
                name="unavailable-writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(tmp_path / "missing-writer")],
                fallback_agent="reader",
            ),
            "reader": AgentConfig(
                name="reader",
                adapter="generic",
                role="implementer",
                permissions="read_only",
                command=[str(script)],
            ),
        },
        workflow=WorkflowConfig(orchestrator="unavailable-writer", implementers=["unavailable-writer"], reviewers=[]),
        output=OutputConfig(),
    )
    cfg.workflow.max_policy_violation_retries = 0
    run_dir = create_new_run(tmp_path, "run-rw-read-only-fallback-policy", "RW policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    result, parse_error = asyncio.run(
        engine._run_agent("unavailable-writer", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert result.agent_name == "reader"
    assert result.metadata["effective_permission"] == "read_only"
    assert parse_error is not None
    assert "read-only role policy violation" in parse_error
    assert "tracked.txt" in parse_error
    assert baseline.read_text(encoding="utf-8") == "baseline\n"


def test_rw_profile_allows_write_capable_fallback_edits(tmp_path: Path):
    baseline = tmp_path / "tracked.txt"
    baseline.write_text("baseline\n", encoding="utf-8")

    script = tmp_path / "writer.py"
    script.write_text(
        """#!/usr/bin/env python3
import json
from pathlib import Path

Path("tracked.txt").write_text("write-capable fallback changed\\n", encoding="utf-8")
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
            "unavailable-reader": AgentConfig(
                name="unavailable-reader",
                adapter="generic",
                role="implementer",
                permissions="read_only",
                command=[str(tmp_path / "missing-reader")],
                fallback_agent="writer",
            ),
            "writer": AgentConfig(
                name="writer",
                adapter="generic",
                role="implementer",
                permissions="read_write",
                command=[str(script)],
            ),
        },
        workflow=WorkflowConfig(orchestrator="unavailable-reader", implementers=["unavailable-reader"], reviewers=[]),
        output=OutputConfig(),
    )
    run_dir = create_new_run(tmp_path, "run-rw-write-fallback-policy", "RW policy", 10, {}, execution_profile="rw")
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state, execution_profile="rw")

    result, parse_error = asyncio.run(
        engine._run_agent("unavailable-reader", "implementer", RunStatus.IMPLEMENTING, "prompt", ImplementerResponse)
    )

    assert result.agent_name == "writer"
    assert result.metadata["effective_permission"] == "read_write"
    assert parse_error is None
    assert result.diff_delta_files is None
    assert baseline.read_text(encoding="utf-8") == "write-capable fallback changed\n"


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
    before_state = run_dir.state_path.read_bytes()
    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Dry", dry_run=True))

    assert result["dry_run"] is True
    assert RunStatus.PLANNING.value in result["stages"]
    assert result["state_dir"] == str(run_dir.path)
    assert run_dir.state_path.read_bytes() == before_state
    assert not run_dir.task_path.exists()


def test_dry_run_does_not_rewrite_legacy_state_or_task_file(tmp_path: Path):
    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake)
    run_dir = create_new_run(tmp_path, "run-8-legacy", "Original", 20, {})
    run_dir.task_path.write_text("Original task file\n", encoding="utf-8")
    payload = json.loads(run_dir.state_path.read_text(encoding="utf-8"))
    payload.pop("execution_profile", None)
    run_dir.state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    before_state = run_dir.state_path.read_bytes()
    before_task = run_dir.task_path.read_bytes()

    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    result = asyncio.run(engine.run("Dry replacement", dry_run=True))

    assert result["dry_run"] is True
    assert run_dir.state_path.read_bytes() == before_state
    assert run_dir.task_path.read_bytes() == before_task


def test_dry_run_reports_clean_start_blocker_without_rewriting_state_or_task_file(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    fake = tmp_path / "agent.sh"
    write_fake_agent(tmp_path, "agent.sh")
    cfg = _build_config(fake, require_clean=True)
    run_dir = create_new_run(tmp_path, "run-8-dirty-clean-start", "Original", 20, {})
    run_dir.task_path.write_text("Original task file\n", encoding="utf-8")
    before_state = run_dir.state_path.read_bytes()
    before_task = run_dir.task_path.read_bytes()

    state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(cfg, run_dir, state)
    engine.repo_root = repo
    result = asyncio.run(engine.run("Dry replacement", dry_run=True))

    assert result["dry_run"] is True
    assert result["repository_dirty"] is True
    assert result["require_clean_start"] is True
    assert result["would_fail"] is True
    assert result["failure_reason"] == "repository has uncommitted changes"
    assert "dirty.txt" in result["repo_status"]
    assert run_dir.state_path.read_bytes() == before_state
    assert run_dir.task_path.read_bytes() == before_task


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


def test_read_only_violations_are_rejected_and_rolled_back(tmp_path: Path, monkeypatch):
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
    monkeypatch.setenv("PATH", f"{engine.policy_bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

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
