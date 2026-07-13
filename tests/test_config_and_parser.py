from __future__ import annotations

import asyncio
import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

import agent_relay.policy as policy
from agent_relay.config import (
    AgentConfig,
    Config,
    OutputConfig,
    RepositoryConfig,
    UnsupportedConfigWarning,
    WorkflowConfig,
    load_config,
    save_config,
)
from agent_relay.policy import (
    POLICY_PRELOAD_NAME,
    apply_codex_execution_profile,
    install_command_policy_wrappers,
    internal_subprocess_env,
    normalize_execution_profile,
    path_without_policy_bins,
)
from agent_relay.parser import parse_structured_output
from agent_relay.models import AdvisorResponse, ImplementerResponse, OrchestratorPlan, ReviewerResponse
from agent_relay.runner import RunnerConfig, run_subprocess
def test_config_roundtrip_and_validation(tmp_path: Path):
    cfg = Config(
        agents={
            "local": AgentConfig(
                name="local",
                adapter="generic",
                model=None,
                role="orchestrator",
                permissions="read_write",
                command=["cat"],
            )
        },
        workflow=WorkflowConfig(orchestrator="local"),
        repository=RepositoryConfig(),
        output=OutputConfig(),
    )
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.agents["local"].name == "local"
    assert loaded.workflow.orchestrator == "local"


def test_workflow_config_allows_zero_review_rounds():
    cfg = WorkflowConfig(orchestrator="local", max_review_rounds=0)

    assert cfg.max_review_rounds == 0


def test_reserved_workflow_contracts_warn_when_non_default(recwarn):
    cfg = WorkflowConfig(
        orchestrator="local",
        testers=["qa"],
        researcher=["research"],
        parallel_read_only_agents=True,
        parallel_write_agents=True,
    )

    assert cfg.testers == ["qa"]
    messages = [
        str(warning.message)
        for warning in recwarn
        if issubclass(warning.category, UnsupportedConfigWarning)
    ]
    assert any("workflow.testers is reserved" in message for message in messages)
    assert any("workflow.researcher is reserved" in message for message in messages)
    assert any("workflow.parallel_read_only_agents is reserved" in message for message in messages)
    assert any("workflow.parallel_write_agents is reserved" in message for message in messages)


def test_reserved_repository_contract_warns_when_enabled(recwarn):
    cfg = RepositoryConfig(create_checkpoint_commits=True)

    assert cfg.create_checkpoint_commits is True
    messages = [
        str(warning.message)
        for warning in recwarn
        if issubclass(warning.category, UnsupportedConfigWarning)
    ]
    assert any("repository.create_checkpoint_commits is reserved" in message for message in messages)


def test_generic_command_construction_from_config(tmp_path: Path):
    from agent_relay.adapters import GenericCLIAdapter

    cfg = AgentConfig(
        name="local",
        adapter="generic",
        role="generic",
        permissions="read_write",
        command=["echo", "hello"],
    )
    adapter = GenericCLIAdapter(cfg)
    cmd = adapter.build_command("prompt", tmp_path)
    assert cmd == ["echo", "hello"]


def test_parser_designated_block_and_fallback(tmp_path: Path):
    text = """
noise
```json
{"summary":"a","assumptions":[],"tasks":[],"risks":[],"advisor_questions":[]}
```
"""
    parsed, raw, err = parse_structured_output(text, OrchestratorPlan)
    assert err is None
    assert parsed is not None
    assert parsed.summary == "a"
    assert isinstance(raw, dict)

    text2 = '{"summary":"fallback","assumptions":[],"tasks":[],"risks":[],"advisor_questions":[]}'
    parsed2, raw2, err2 = parse_structured_output(text2, OrchestratorPlan)
    assert err2 is None
    assert parsed2 is not None
    assert raw2 == json.loads(text2)


def test_parser_malformed_response():
    text = "not-json"
    parsed, raw, err = parse_structured_output(text, OrchestratorPlan)
    assert parsed is None
    assert raw is None
    assert err == "no-json-found"


def test_implementer_response_coerces_structured_command_results():
    payload = {
        "status": "completed",
        "summary": "done",
        "files_changed": [],
        "commands_run": [
            {"command": "python3 -m pytest -q", "exit_code": 0},
        ],
        "tests": [],
        "remaining_issues": [],
        "handoff_notes": [],
    }
    parsed = ImplementerResponse.model_validate(payload)
    assert parsed.commands_run == ["python3 -m pytest -q (exit_code=0)"]


def test_reviewer_response_coerces_structured_issues():
    payload = {
        "verdict": "reject",
        "summary": "needs work",
        "blocking_issues": [
            {"issue": "Repository was modified during a read-only task", "files": ["README.md"]},
        ],
        "non_blocking_issues": [],
        "required_fixes": [{"summary": "Remove unrelated edits"}],
        "tests_recommended": [],
    }
    parsed = ReviewerResponse.model_validate(payload)
    assert parsed.blocking_issues == ["Repository was modified during a read-only task"]
    assert parsed.required_fixes == ["Remove unrelated edits"]


@pytest.mark.parametrize(
    ("model", "payload", "field", "bad_value"),
    [
        (
            AdvisorResponse,
            {
                "verdict": "looks_good",
                "summary": "unexpected advisor verdict",
            },
            "verdict",
            "looks_good",
        ),
        (
            ReviewerResponse,
            {
                "verdict": "revise",
                "summary": "unexpected reviewer verdict",
            },
            "verdict",
            "revise",
        ),
        (
            ImplementerResponse,
            {
                "status": "done",
                "summary": "unexpected implementer status",
            },
            "status",
            "done",
        ),
    ],
)
def test_agent_response_models_reject_unknown_verdicts_and_statuses(model, payload, field, bad_value):
    with pytest.raises(ValueError) as exc_info:
        model.model_validate(payload)

    message = str(exc_info.value)
    assert field in message
    assert bad_value in message
    assert "Input should be" in message


@pytest.mark.parametrize(
    ("model", "payload", "field", "bad_value"),
    [
        (
            AdvisorResponse,
            {
                "verdict": "continue",
                "summary": "unexpected advisor verdict",
            },
            "verdict",
            "continue",
        ),
        (
            ReviewerResponse,
            {
                "verdict": "needs_work",
                "summary": "unexpected reviewer verdict",
            },
            "verdict",
            "needs_work",
        ),
        (
            ImplementerResponse,
            {
                "status": "success",
                "summary": "unexpected implementer status",
            },
            "status",
            "success",
        ),
    ],
)
def test_parser_returns_clear_error_for_unknown_verdicts_and_statuses(model, payload, field, bad_value):
    parsed, raw, err = parse_structured_output(json.dumps(payload), model)

    assert parsed is None
    assert raw == {"raw": json.dumps(payload)}
    assert err is not None
    assert err.startswith("fallback-json-parse-failed:")
    assert field in err
    assert bad_value in err
    assert "Input should be" in err


def test_invalid_mode_rejected():
    try:
        AgentConfig(
            name="bad",
            adapter="generic",
            role="orchestrator",
            permissions="read_write",
            command=["cat"],
            mode="extreme",
        )
    except ValueError as exc:
        assert "mode must be" in str(exc)
    else:
        raise AssertionError("Invalid mode was accepted")


def test_codex_execution_profile_flags_are_forced():
    base = ["codex", "exec", "--ask-for-approval", "on-request", "--sandbox", "read-only", "-m", "gpt-5.5"]

    ro = apply_codex_execution_profile(base, "ro")
    assert ro[:6] == ["codex", "--ask-for-approval", "never", "--sandbox", "workspace-write", "exec"]
    assert "read-only" not in ro

    rw = apply_codex_execution_profile(base, "rw")
    assert rw[:6] == ["codex", "--ask-for-approval", "never", "--sandbox", "danger-full-access", "exec"]


def test_execution_profile_default_is_rw():
    assert normalize_execution_profile(None) == "rw"


def test_policy_wrapper_blocks_denied_command(tmp_path: Path):
    policy_bin = install_command_policy_wrappers(tmp_path, denied_commands=("blocked-tool",))
    env = internal_subprocess_env()
    env["PATH"] = f"{policy_bin}{os.pathsep}{env.get('PATH', '')}"

    proc = subprocess.run(["blocked-tool", "--version"], env=env, capture_output=True, text=True, check=False)

    assert proc.returncode == 126
    assert "stringbean policy" in proc.stderr


def test_runner_denies_absolute_denied_command_before_execution(tmp_path: Path):
    marker = tmp_path / "ran.txt"
    blocked = tmp_path / "blocked-tool"
    blocked.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    blocked.chmod(0o755)
    alias = tmp_path / "safe-tool"
    alias.symlink_to(blocked)

    result = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[str(alias)],
                working_directory=tmp_path,
                env={"STRINGBEAN_DENIED_COMMANDS": "blocked-tool"},
            )
        )
    )

    assert result.exit_code == 126
    assert "blocked-tool" in result.raw_stderr
    assert not marker.exists()

    result = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=["safe-tool"],
                working_directory=tmp_path,
                env={
                    "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
                    "STRINGBEAN_DENIED_COMMANDS": "blocked-tool",
                },
            )
        )
    )

    assert result.exit_code == 126
    assert "blocked-tool" in result.raw_stderr
    assert not marker.exists()


def test_runner_denies_absolute_git_denied_subcommand_before_execution(tmp_path: Path):
    marker = tmp_path / "git-ran.txt"
    fake_git = tmp_path / "git"
    fake_git.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    alias = tmp_path / "mygit"
    alias.symlink_to(fake_git)

    result = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=[str(alias), "zap"],
                working_directory=tmp_path,
                env={"STRINGBEAN_DENIED_GIT_SUBCOMMANDS": "zap"},
            )
        )
    )

    assert result.exit_code == 126
    assert "git zap" in result.raw_stderr
    assert not marker.exists()

    result = asyncio.run(
        run_subprocess(
            RunnerConfig(
                command=["mygit", "zap"],
                working_directory=tmp_path,
                env={
                    "PATH": f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
                    "STRINGBEAN_DENIED_GIT_SUBCOMMANDS": "zap",
                },
            )
        )
    )

    assert result.exit_code == 126
    assert "git zap" in result.raw_stderr
    assert not marker.exists()


def test_policy_preload_denies_absolute_child_command_before_execution(tmp_path: Path):
    policy_bin = install_command_policy_wrappers(tmp_path / "policy", denied_commands=("blocked-tool",))
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    marker = tmp_path / "child-ran.txt"
    blocked = tmp_path / "blocked-tool"
    blocked.write_text(
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n",
        encoding="utf-8",
    )
    blocked.chmod(0o755)
    alias = tmp_path / "safe-tool"
    alias.symlink_to(blocked)

    env = internal_subprocess_env()
    env["LD_PRELOAD"] = str(preload)
    env["STRINGBEAN_DENIED_COMMANDS"] = "blocked-tool"
    env["STRINGBEAN_DENIED_GIT_SUBCOMMANDS"] = "zap"
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import subprocess, sys\n"
                "try:\n"
                "    proc = subprocess.run(['safe-tool'], capture_output=True, text=True)\n"
                "except PermissionError as exc:\n"
                "    sys.stderr.write(str(exc))\n"
                "    sys.exit(0)\n"
                "sys.stderr.write(proc.stderr)\n"
                "sys.exit(0 if proc.returncode else 1)\n"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "blocked-tool" in proc.stderr or "Permission denied" in proc.stderr
    assert not marker.exists()


@pytest.mark.parametrize("launcher", ["execve", "execvpe", "posix_spawn"])
@pytest.mark.parametrize("target_kind", ["command", "git"])
def test_policy_preload_enforces_when_child_env_omits_policy_markers(
    tmp_path: Path, launcher: str, target_kind: str
):
    if launcher == "posix_spawn" and not hasattr(os, "posix_spawn"):
        pytest.skip("os.posix_spawn is not available on this platform")

    policy_bin = install_command_policy_wrappers(
        tmp_path / "policy",
        denied_commands=("blocked-tool",),
        denied_git_subcommands=("zap",),
    )
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    marker = tmp_path / f"{launcher}-{target_kind}-ran.txt"
    blocked = tmp_path / ("blocked-tool" if target_kind == "command" else "git")
    blocked.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        "sys.exit(33)\n",
        encoding="utf-8",
    )
    blocked.chmod(0o755)
    command_path = blocked
    if target_kind == "command":
        command_path = tmp_path / "safe-tool"
        command_path.symlink_to(blocked)

    executable = command_path.name if launcher == "execvpe" else str(command_path)
    argv = [executable, "zap"] if target_kind == "git" else [executable]
    env = internal_subprocess_env()
    env["LD_PRELOAD"] = str(preload)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["STRINGBEAN_DENIED_COMMANDS"] = "blocked-tool"
    env["STRINGBEAN_DENIED_GIT_SUBCOMMANDS"] = "zap"
    assert not any(name.startswith("STRINGBEAN_POLICY_") for name in env)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import errno, os, sys\n"
                f"launcher = {launcher!r}\n"
                f"executable = {executable!r}\n"
                f"argv = {argv!r}\n"
                "child_env = {'PATH': os.environ['PATH']}\n"
                "try:\n"
                "    if launcher == 'execve':\n"
                "        os.execve(executable, argv, child_env)\n"
                "    elif launcher == 'execvpe':\n"
                "        os.execvpe(executable, argv, child_env)\n"
                "    else:\n"
                "        pid = os.posix_spawn(executable, argv, child_env)\n"
                "        os.waitpid(pid, 0)\n"
                "        sys.exit(99)\n"
                "except PermissionError as exc:\n"
                "    sys.stderr.write(str(exc))\n"
                "    sys.exit(0)\n"
                "except OSError as exc:\n"
                "    sys.stderr.write(str(exc))\n"
                "    sys.exit(0 if exc.errno == errno.EACCES else 1)\n"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert "Permission denied" in proc.stderr or "denied for subagents" in proc.stderr
    assert not marker.exists()


@pytest.mark.parametrize("launcher", ["execve", "execvpe", "posix_spawn", "fork_execve"])
def test_policy_preload_allows_process_launch_for_allowed_targets(tmp_path: Path, launcher: str):
    if launcher == "posix_spawn" and not hasattr(os, "posix_spawn"):
        pytest.skip("os.posix_spawn is not available on this platform")
    if launcher == "fork_execve" and not hasattr(os, "fork"):
        pytest.skip("os.fork is not available on this platform")

    policy_bin = install_command_policy_wrappers(tmp_path / "policy", denied_commands=("blocked-tool",))
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    marker = tmp_path / f"{launcher}-allowed-ran.txt"
    allowed = tmp_path / "allowed-tool"
    allowed.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('ran', encoding='utf-8')\n"
        "sys.exit(33)\n",
        encoding="utf-8",
    )
    allowed.chmod(0o755)

    executable = allowed.name if launcher == "execvpe" else str(allowed)
    argv = [executable]
    env = internal_subprocess_env()
    env["LD_PRELOAD"] = str(preload)
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["STRINGBEAN_DENIED_COMMANDS"] = "blocked-tool"
    env["STRINGBEAN_DENIED_GIT_SUBCOMMANDS"] = "zap"

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os, sys\n"
                f"launcher = {launcher!r}\n"
                f"executable = {executable!r}\n"
                f"argv = {argv!r}\n"
                "child_env = dict(os.environ)\n"
                "if launcher == 'execve':\n"
                "    os.execve(executable, argv, child_env)\n"
                "elif launcher == 'execvpe':\n"
                "    os.execvpe(executable, argv, child_env)\n"
                "elif launcher == 'posix_spawn':\n"
                "    pid = os.posix_spawn(executable, argv, child_env)\n"
                "    _, status = os.waitpid(pid, 0)\n"
                "    sys.exit(os.waitstatus_to_exitcode(status))\n"
                "else:\n"
                "    pid = os.fork()\n"
                "    if pid == 0:\n"
                "        os.execve(executable, argv, child_env)\n"
                "    _, status = os.waitpid(pid, 0)\n"
                "    sys.exit(os.waitstatus_to_exitcode(status))\n"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 33
    assert marker.read_text(encoding="utf-8") == "ran"


def test_policy_wrapper_uses_real_git_when_path_is_already_wrapped(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(policy, "_REAL_GIT", None)
    first_policy_bin = install_command_policy_wrappers(tmp_path / "first")
    monkeypatch.setenv("PATH", f"{first_policy_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(policy, "_REAL_GIT", None)

    second_policy_bin = install_command_policy_wrappers(tmp_path / "second")
    env = internal_subprocess_env()
    env["PATH"] = f"{second_policy_bin}{os.pathsep}{env.get('PATH', '')}"

    assert str(first_policy_bin) not in path_without_policy_bins(os.environ["PATH"]).split(os.pathsep)
    assert str(first_policy_bin / "git") not in (second_policy_bin / "git").read_text(encoding="utf-8")

    allowed = subprocess.run(["git", "--version"], env=env, capture_output=True, text=True, check=False)

    assert allowed.returncode == 0
    assert "git version" in allowed.stdout


def test_internal_subprocess_env_strips_policy_preload_and_policy_markers(tmp_path: Path):
    policy_bin = install_command_policy_wrappers(tmp_path / "policy")
    preload = policy_bin / POLICY_PRELOAD_NAME
    unrelated_preload = tmp_path / "libcustom.so"
    env = {
        "PATH": f"{policy_bin}{os.pathsep}/usr/bin",
        "LD_PRELOAD": f"{preload}{os.pathsep}{unrelated_preload}",
        "STRINGBEAN_POLICY_BIN": str(policy_bin),
        "STRINGBEAN_POLICY_PRELOAD": str(preload),
        "STRINGBEAN_POLICY_PRELOAD_ACTIVE": "1",
        "STRINGBEAN_POLICY_WRAPPERS_ACTIVE": "1",
        "STRINGBEAN_DENIED_COMMANDS": "blocked-tool",
    }

    cleaned = internal_subprocess_env(env)

    assert cleaned["PATH"] == "/usr/bin"
    assert cleaned["LD_PRELOAD"] == str(unrelated_preload)
    assert "STRINGBEAN_DENIED_COMMANDS" in cleaned
    assert not any(name.startswith("STRINGBEAN_POLICY_") for name in cleaned)


def test_path_without_policy_bins_removes_legacy_wrapper_without_sentinel(tmp_path: Path):
    legacy_policy_bin = tmp_path / "legacy" / "policy-bin"
    legacy_policy_bin.mkdir(parents=True)
    (legacy_policy_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        "echo \"stringbean policy: this git operation is denied for subagents\" >&2\n",
        encoding="utf-8",
    )
    keep_bin = tmp_path / "keep-bin"
    keep_bin.mkdir()

    cleaned = path_without_policy_bins(f"{legacy_policy_bin}{os.pathsep}{keep_bin}")

    assert cleaned.split(os.pathsep) == [str(keep_bin)]


def _install_fake_git_policy_wrapper(tmp_path: Path, monkeypatch) -> tuple[dict[str, str], Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$FAKE_GIT_ARGS\"\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    monkeypatch.setattr(policy, "_REAL_GIT", None)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    policy_bin = install_command_policy_wrappers(tmp_path / "policy")

    args_file = tmp_path / "fake-git-args.txt"
    env = internal_subprocess_env()
    env["FAKE_GIT_ARGS"] = str(args_file)
    env["PATH"] = f"{policy_bin}{os.pathsep}{env.get('PATH', '')}"
    return env, args_file


@pytest.mark.parametrize(
    "args, denied_subcommand",
    [
        (["reset", "--hard"], "reset"),
        (["-C", "repo", "reset", "--hard"], "reset"),
        (["-c", "core.pager=cat", "push", "origin", "main"], "push"),
        (["--no-pager", "commit", "-m", "message"], "commit"),
        (["--git-dir", ".git", "checkout", "main"], "checkout"),
        (["--git-dir=.git", "clean", "-fd"], "clean"),
    ],
)
def test_git_policy_wrapper_blocks_denied_subcommands_after_global_options(
    tmp_path: Path,
    monkeypatch,
    args: list[str],
    denied_subcommand: str,
):
    env, args_file = _install_fake_git_policy_wrapper(tmp_path, monkeypatch)

    proc = subprocess.run(["git", *args], env=env, capture_output=True, text=True, check=False)

    assert proc.returncode == 126
    assert f"git {denied_subcommand}" in proc.stderr
    assert not args_file.exists()


def test_git_policy_wrapper_allows_status_through_fake_git(tmp_path: Path, monkeypatch):
    env, args_file = _install_fake_git_policy_wrapper(tmp_path, monkeypatch)

    proc = subprocess.run(["git", "status", "--short"], env=env, capture_output=True, text=True, check=False)

    assert proc.returncode == 0
    assert proc.stderr == ""
    assert args_file.read_text(encoding="utf-8").splitlines() == ["status", "--short"]
