from __future__ import annotations

from pathlib import Path
import json
import subprocess

from typer.testing import CliRunner

from agent_relay import cli
from agent_relay.state import create_new_run


runner = CliRunner()


def test_cli_help_available():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "stringbean" in result.stdout
    assert "agent" + "-relay" not in result.stdout


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
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout
    assert "'dry_run': True" not in result.stdout


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
    assert "STRINGBEAN_RESULT_START" in result.stdout
    assert "Status: DRY_RUN" in result.stdout
    assert "STRINGBEAN_RESULT_END" in result.stdout


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
