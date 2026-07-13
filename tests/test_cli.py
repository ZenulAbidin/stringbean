from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from agent_relay import cli
from agent_relay.state import create_new_run


runner = CliRunner()


def test_cli_help_available():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "stringbean" in result.stdout
    assert "agent" + "-relay" not in result.stdout


def test_cli_version_available():
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert "stringbean 0.1.0" in result.stdout


def test_run_help_lists_agent_stream_switch():
    result = runner.invoke(cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--no-agent-stream" in result.stdout
    assert "--codex-final" in result.stdout
    assert "--codex-progress" in result.stdout
    assert "heartbeat lines" in result.stdout
    assert "--policy-retries" in result.stdout
    assert "--rw" in result.stdout
    assert "--ro" in result.stdout
    assert "[default: rw]" in result.stdout


def test_sbx_accepts_unquoted_prompt_words():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "scripts" / "sbx"), "enumerate", "bugs", "--dry-run", "--quiet"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "enumerate-bugs" in result.stdout
    assert "'dry_run': True" in result.stdout
    assert "'execution_profile': 'rw'" in result.stdout


def test_installed_sbx_entrypoint_accepts_unquoted_prompt_words(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda: captured.append(sys.argv.copy()))
    monkeypatch.setattr(sys, "argv", ["sbx", "enumerate", "bugs", "--dry-run", "--quiet"])

    cli.sbx_main()

    assert captured == [["stringbean", "run", "enumerate bugs", "--dry-run", "--quiet"]]


def test_installed_sbx_entrypoint_help_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["sbx", "--help"])

    with pytest.raises(SystemExit) as exc:
        cli.sbx_main()

    assert exc.value.code == 0
    assert "Usage:" in capsys.readouterr().out


def test_sbx_script_targets_invocation_directory_not_stringbean_source(tmp_path: Path):
    source_repo = Path(__file__).resolve().parents[1]
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    run_id = f"target-cwd-probe-{tmp_path.name}"
    subprocess.run(["git", "init"], cwd=target_repo, check=True, capture_output=True)
    (target_repo / "target-marker.txt").write_text("target repo marker\n", encoding="utf-8")

    init = subprocess.run(
        [str(source_repo / "scripts" / "stringbean"), "init", "--force", "--preset", "C"],
        cwd=target_repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert init.returncode == 0, init.stderr

    result = subprocess.run(
        [
            str(source_repo / "scripts" / "sbx"),
            "verify",
            "target",
            "cwd",
            "--dry-run",
            "--quiet",
            "--run-id",
            run_id,
        ],
        cwd=target_repo,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "target-marker.txt" in result.stdout
    assert (tmp_path / ".stringbean" / "runs" / run_id / "state.json").exists()
    assert not (source_repo / ".stringbean" / "runs" / run_id).exists()


def test_sbx_codex_final_emits_sentinel_block():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "scripts" / "sbx"), "enumerate", "bugs", "--dry-run", "--codex-final"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "UnsupportedConfigWarning" not in result.stderr
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout
    assert "'dry_run': True" not in result.stdout


def test_sbx_plugin_final_alias_emits_sentinel_block():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "scripts" / "sbx"), "enumerate", "bugs", "--dry-run", "--plugin-final"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout


def test_codex_plugin_sbx_wrapper_emits_sentinel_block():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "plugins" / "stringbean" / "scripts" / "sbx-codex"), "enumerate", "bugs", "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout


def test_grok_plugin_sbx_wrapper_emits_sentinel_block():
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [str(repo / "plugins" / "grok-stringbean" / "scripts" / "sbx-grok"), "enumerate", "bugs", "--dry-run"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout


def test_run_rejects_local_fallback_cat_config_before_launch(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init = runner.invoke(cli.app, ["init", "--force", "--preset", "C"])
    assert init.exit_code == 0

    result = runner.invoke(cli.app, ["run", "real task", "--quiet"])

    assert result.exit_code == 1
    assert "Configuration error:" in result.stdout
    assert "Configured placeholder agent(s) cannot perform a real run" in result.stdout
    assert "local-fallback" in result.stdout
    assert not (tmp_path / ".stringbean" / "runs").exists()


def test_codex_final_rejects_local_fallback_cat_config_with_sentinel_block(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init = runner.invoke(cli.app, ["init", "--force", "--preset", "C"])
    assert init.exit_code == 0

    result = runner.invoke(cli.app, ["run", "real task", "--codex-final"])

    assert result.exit_code == 1
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: FAILED" in result.stdout
    assert "Configured placeholder agent(s) cannot perform a real run" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout


def test_run_summary_prints_singular_error_field(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".stringbean").mkdir()
    cfg = cli._preset_config("D")
    cfg.repository.require_git = True
    monkeypatch.setattr(cli, "load_config", lambda _path: cfg)

    result = runner.invoke(cli.app, ["run", "inspect tmp", "--quiet"])

    assert result.exit_code == 0
    assert "Status: FAILED" in result.stdout
    assert "Error: repository is not a git worktree" in result.stdout


def test_codex_final_summary_prints_singular_error_field(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".stringbean").mkdir()
    cfg = cli._preset_config("D")
    cfg.repository.require_git = True
    monkeypatch.setattr(cli, "load_config", lambda _path: cfg)

    result = runner.invoke(cli.app, ["run", "inspect tmp", "--codex-final"])

    assert result.exit_code == 0
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "Status: FAILED" in result.stdout
    assert "Error: repository is not a git worktree" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout


def test_run_catches_unexpected_engine_exception_without_traceback(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".stringbean").mkdir()
    cfg = cli._preset_config("D")
    cfg.repository.require_git = False
    monkeypatch.setattr(cli, "load_config", lambda _path: cfg)

    def fail_engine(**_kwargs):
        raise MemoryError("snapshot too large")

    monkeypatch.setattr(cli, "_run_engine", fail_engine)

    result = runner.invoke(cli.app, ["run", "inspect tmp", "--quiet"])

    assert result.exit_code == 1
    assert "Run failed: snapshot too large" in result.stdout
    assert "Traceback" not in result.stdout


def test_codex_final_catches_engine_exception_with_sentinel_block(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".stringbean").mkdir()
    cfg = cli._preset_config("D")
    cfg.repository.require_git = False
    monkeypatch.setattr(cli, "load_config", lambda _path: cfg)

    def fail_engine(**_kwargs):
        raise RuntimeError("agent exited with status 1")

    monkeypatch.setattr(cli, "_run_engine", fail_engine)

    result = runner.invoke(cli.app, ["run", "inspect tmp", "--codex-final"])

    assert result.exit_code == 1
    assert "STRINGBEAN_FINAL_START" in result.stdout
    assert "Status: FAILED" in result.stdout
    assert "Error: agent exited with status 1" in result.stdout
    assert "Artifacts:" in result.stdout
    assert "STRINGBEAN_FINAL_END" in result.stdout
    assert "Run failed:" not in result.stdout
    assert "Traceback" not in result.stdout


def test_init_and_status_cycle(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["init", "--force", "--preset", "C"])
    assert result.exit_code == 0
    assert (tmp_path / ".stringbean" / "config.yaml").exists()

    state = runner.invoke(cli.app, ["agents"])
    assert state.exit_code == 0


def test_cli_repeated_run_id_returns_usable_suffixed_run_dir(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _path: cli._default_config())

    first = runner.invoke(cli.app, ["run", "repeat task", "--run-id", "repeat-run", "--dry-run", "--quiet"])
    second = runner.invoke(cli.app, ["run", "repeat task", "--run-id", "repeat-run", "--dry-run", "--quiet"])

    first_run_dir = tmp_path / ".stringbean" / "runs" / "repeat-run"
    second_run_dir = tmp_path / ".stringbean" / "runs" / "repeat-run-2"
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "Run ID: repeat-run" in first.stdout
    assert "Run ID: repeat-run-2" in second.stdout
    assert first_run_dir.joinpath("manifest.json").exists()
    assert first_run_dir.joinpath("state.json").exists()
    assert first_run_dir.joinpath("calls").is_dir()
    assert second_run_dir.joinpath("manifest.json").exists()
    assert second_run_dir.joinpath("state.json").exists()
    assert second_run_dir.joinpath("calls").is_dir()

    second_state = json.loads(second_run_dir.joinpath("state.json").read_text(encoding="utf-8"))
    assert second_state["run_id"] == "repeat-run-2"
    assert second_state["run_dir"] == str(second_run_dir)


def test_resume_legacy_state_defaults_to_ro_and_allows_rw_override(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda _path: cli._default_config())
    used_profiles: list[str] = []

    class FakeWorkflowEngine:
        def __init__(self, _cfg, _run_dir, _state, **kwargs):
            used_profiles.append(kwargs["execution_profile"])

        async def run(self, **_kwargs):
            return {"status": "COMPLETED"}

    monkeypatch.setattr(cli, "WorkflowEngine", FakeWorkflowEngine)

    run_dir = create_new_run(tmp_path, "legacy-resume", "Legacy resume", 20, {})
    payload = json.loads(run_dir.state_path.read_text(encoding="utf-8"))
    payload.pop("execution_profile")
    run_dir.state_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(cli.app, ["resume", "legacy-resume"])

    assert result.exit_code == 0
    assert "Execution profile: ro" in result.stdout
    assert used_profiles == ["ro"]

    payload = json.loads(run_dir.state_path.read_text(encoding="utf-8"))
    payload.pop("execution_profile", None)
    run_dir.state_path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(cli.app, ["resume", "legacy-resume", "--profile", "rw"])

    assert result.exit_code == 0
    assert "Execution profile: rw" in result.stdout
    assert used_profiles == ["ro", "rw"]
