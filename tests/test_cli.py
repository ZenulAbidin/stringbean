from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from agent_relay import cli


runner = CliRunner()


def test_cli_help_available():
    result = runner.invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    assert "stringbean" in result.stdout or "agent-relay" in result.stdout


def test_run_help_lists_agent_stream_switch():
    result = runner.invoke(cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "--no-agent-stream" in result.stdout


def test_init_and_status_cycle(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli.app, ["init", "--force", "--preset", "C"])
    assert result.exit_code == 0
    assert (tmp_path / ".stringbean" / "config.yaml").exists() or (tmp_path / ".agent-relay" / "config.yaml").exists()

    state = runner.invoke(cli.app, ["agents"])
    assert state.exit_code == 0
