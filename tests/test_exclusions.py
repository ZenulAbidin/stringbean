from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_relay.config import RepositoryConfig
from agent_relay.context import collect_repo_context, read_text_if_present
from agent_relay.exclusions import RepositoryExclusions
from agent_relay.policy import (
    POLICY_PRELOAD_NAME,
    install_command_policy_wrappers,
    internal_subprocess_env,
    policy_prompt,
)


def test_repository_defaults_to_directory_mode_with_sensitive_boundaries():
    config = RepositoryConfig()

    assert config.require_git is False
    assert config.exclude_nested_repositories is True
    assert config.excluded_paths == []


def test_policy_prompt_continues_without_sensitive_path_or_provider_consent_pause(tmp_path: Path):
    text = policy_prompt(
        "ro",
        "read_only",
        workspace_root=tmp_path,
        excluded_paths=(".env*", "credentials/**"),
    )

    normalized = " ".join(text.split())
    assert "Ordinary remote processing" in normalized
    assert "do not pause for separate provider-use approval" in normalized
    assert "skip it and continue with the rest of the task" in normalized
    assert (
        "Do not retry access, ask the user for permission to inspect it, or ask another agent to "
        "inspect it"
        in normalized
    )


def test_exclusions_protect_secrets_and_nested_repositories_without_hiding_templates(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=private\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("TOKEN=example\n", encoding="utf-8")
    private = tmp_path / "private-production"
    private.mkdir()
    (private / "auth.json").write_text("private\n", encoding="utf-8")
    (tmp_path / ".stringbeanignore").write_text("private-production/**\n", encoding="utf-8")
    nested = tmp_path / "nested-control"
    nested.mkdir()
    (nested / ".git").mkdir()
    (nested / "credentials.txt").write_text("private\n", encoding="utf-8")

    exclusions = RepositoryExclusions.discover(tmp_path)

    assert exclusions.is_excluded(".env")
    assert not exclusions.is_excluded(".env.example")
    assert exclusions.is_excluded("private-production/auth.json")
    assert exclusions.is_excluded("nested-control/credentials.txt")
    assert exclusions.nested_repository_roots == ("nested-control",)
    assert {path.relative_to(tmp_path).as_posix() for path in exclusions.protected_paths} == {
        ".env",
        "nested-control",
        "private-production",
    }


def test_non_git_context_is_first_class_and_does_not_read_excluded_guidance(tmp_path: Path):
    (tmp_path / "README.md").write_text("do not export this\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible\n", encoding="utf-8")
    (tmp_path / ".stringbeanignore").write_text("README.md\n", encoding="utf-8")
    exclusions = RepositoryExclusions.discover(tmp_path)

    context = collect_repo_context(tmp_path, exclusions)

    assert context["workspace_type"] == "directory"
    assert context["git_repository"] is False
    assert context["git_root"] is None
    assert context["git_status"] == "not a git worktree"
    assert context["README.md"] == ""
    assert context["top_level_files"] == ["visible.txt"]
    assert "do not export this" not in str(context)
    assert read_text_if_present(tmp_path / "README.md", exclusions) == ""


def test_configured_excluded_directory_is_filtered_from_context(tmp_path: Path):
    (tmp_path / "visible.txt").write_text("visible\n", encoding="utf-8")
    private_dir = tmp_path / "private-notes"
    private_dir.mkdir()
    (private_dir / "plan.txt").write_text("do not collect this guidance\n", encoding="utf-8")
    exclusions = RepositoryExclusions.discover(tmp_path, configured_patterns=("private-notes/**",))

    context = collect_repo_context(tmp_path, exclusions)

    assert exclusions.is_excluded("private-notes/plan.txt")
    assert context["top_level_files"] == ["visible.txt"]
    assert "do not collect this guidance" not in str(context)
    assert read_text_if_present(private_dir / "plan.txt", exclusions) == ""


def test_git_context_reports_only_a_dirty_count_not_dirty_paths(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    source = tmp_path / "src"
    source.mkdir()
    (source / "sensitive-name.py").write_text("changed = True\n", encoding="utf-8")

    context = collect_repo_context(tmp_path, RepositoryExclusions.discover(tmp_path))

    assert context["workspace_type"] == "git-worktree"
    assert "changed" in str(context["git_status"])
    assert "sensitive-name.py" not in str(context["git_status"])


def test_policy_preload_denies_file_reads_inside_protected_paths(tmp_path: Path):
    policy_bin = install_command_policy_wrappers(tmp_path / "policy")
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    protected = tmp_path / "production-auth"
    protected.mkdir()
    secret = protected / "token.txt"
    secret.write_text("must-not-reach-provider\n", encoding="utf-8")
    safe = tmp_path / "safe.txt"
    safe.write_text("safe\n", encoding="utf-8")

    env = internal_subprocess_env()
    env["LD_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_EXCLUDED_PATHS"] = str(protected.resolve())
    env["STRINGBEAN_POLICY_ALLOWED_PATHS"] = str(policy_bin.resolve())
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys\n"
                f"safe = pathlib.Path({str(safe)!r}).read_text()\n"
                "try:\n"
                f"    pathlib.Path({str(secret)!r}).read_text()\n"
                "except PermissionError:\n"
                "    print(safe.strip())\n"
                "    sys.exit(0)\n"
                "sys.exit(9)\n"
            ),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0
    assert proc.stdout.strip() == "safe"
    assert "must-not-reach-provider" not in proc.stdout
    assert "excluded path access denied" in proc.stderr


def test_policy_bin_remains_usable_inside_an_excluded_run_tree(tmp_path: Path):
    policy_bin = install_command_policy_wrappers(tmp_path / "runs" / "current" / "policy-bin-parent")
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    env = internal_subprocess_env()
    env["PATH"] = f"{policy_bin}{os.pathsep}{env.get('PATH', '')}"
    env["LD_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_EXCLUDED_PATHS"] = str((tmp_path / "runs").resolve())
    env["STRINGBEAN_POLICY_ALLOWED_PATHS"] = str(policy_bin.resolve())
    proc = subprocess.run(["git", "--version"], env=env, capture_output=True, text=True, check=False)

    assert proc.returncode == 0, proc.stderr
    assert "git version" in proc.stdout


@pytest.mark.parametrize("launcher", ["execve", "posix_spawn"])
def test_excluded_read_policy_survives_scrubbed_child_environments(tmp_path: Path, launcher: str):
    if launcher == "posix_spawn" and not hasattr(os, "posix_spawn"):
        pytest.skip("os.posix_spawn is not available on this platform")
    policy_bin = install_command_policy_wrappers(tmp_path / "policy")
    preload = policy_bin / POLICY_PRELOAD_NAME
    if not preload.is_file():
        pytest.skip("policy preload library was not built on this platform")

    protected = tmp_path / "private"
    protected.mkdir()
    secret = protected / "token.txt"
    secret.write_text("must-not-reach-scrubbed-child\n", encoding="utf-8")
    child_code = (
        "import pathlib, sys\n"
        "try:\n"
        f"    pathlib.Path({str(secret)!r}).read_text()\n"
        "except PermissionError:\n"
        "    sys.exit(0)\n"
        "sys.exit(9)\n"
    )
    outer_code = (
        "import os, sys\n"
        f"argv = [sys.executable, '-c', {child_code!r}]\n"
        "child_env = {'PATH': os.environ['PATH']}\n"
        f"launcher = {launcher!r}\n"
        "if launcher == 'execve':\n"
        "    os.execve(sys.executable, argv, child_env)\n"
        "pid = os.posix_spawn(sys.executable, argv, child_env)\n"
        "_, status = os.waitpid(pid, 0)\n"
        "sys.exit(os.waitstatus_to_exitcode(status))\n"
    )
    env = internal_subprocess_env()
    env["LD_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_PRELOAD"] = str(preload)
    env["STRINGBEAN_POLICY_EXCLUDED_PATHS"] = str(protected.resolve())
    env["STRINGBEAN_POLICY_ALLOWED_PATHS"] = str(policy_bin.resolve())
    proc = subprocess.run(
        [sys.executable, "-c", outer_code],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert "must-not-reach-scrubbed-child" not in proc.stdout
    assert "excluded path access denied" in proc.stderr
