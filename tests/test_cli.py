from __future__ import annotations

from pathlib import Path
import subprocess

from typer.testing import CliRunner

from agent_relay import cli


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
