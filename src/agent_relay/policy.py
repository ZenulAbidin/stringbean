from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Iterable, Sequence


EXECUTION_PROFILES = {"ro", "rw"}

DENIED_COMMANDS: tuple[str, ...] = (
    "rm",
    "rmdir",
    "sudo",
    "su",
    "dd",
    "mkfs",
    "mount",
    "umount",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "systemctl",
    "service",
    "kill",
    "killall",
    "pkill",
    "chown",
    "chgrp",
    "setfacl",
    "shred",
    "wipefs",
)

DENIED_GIT_SUBCOMMANDS: tuple[str, ...] = (
    "reset",
    "clean",
    "checkout",
    "restore",
    "switch",
    "rebase",
    "merge",
    "commit",
    "push",
    "pull",
)


def normalize_execution_profile(value: str | None) -> str:
    normalized = (value or "rw").strip().lower()
    if normalized not in EXECUTION_PROFILES:
        raise ValueError("execution profile must be ro or rw")
    return normalized


def codex_sandbox_for_profile(profile: str) -> str:
    profile = normalize_execution_profile(profile)
    if profile == "rw":
        return "danger-full-access"
    return "workspace-write"


def apply_codex_execution_profile(command: Sequence[str], profile: str) -> list[str]:
    """Force Codex subprocesses into Stringbean's explicit execution profile."""
    out: list[str] = []
    skip_next = False
    for part in command:
        if skip_next:
            skip_next = False
            continue
        if part in {"-a", "--ask-for-approval", "-s", "--sandbox"}:
            skip_next = True
            continue
        if part.startswith("--ask-for-approval=") or part.startswith("--sandbox="):
            continue
        if part == "--dangerously-bypass-approvals-and-sandbox":
            continue
        out.append(str(part))

    if len(out) >= 2 and Path(out[0]).name == "codex" and "exec" in out[1:]:
        exec_index = out.index("exec", 1)
        return [
            out[0],
            "--ask-for-approval",
            "never",
            "--sandbox",
            codex_sandbox_for_profile(profile),
            *out[1:exec_index],
            out[exec_index],
            *out[exec_index + 1 :],
        ]
    return out


def policy_prompt(profile: str, effective_permission: str) -> str:
    profile = normalize_execution_profile(profile)
    denied = ", ".join(DENIED_COMMANDS)
    denied_git = ", ".join(f"git {name}" for name in DENIED_GIT_SUBCOMMANDS)
    if profile == "rw":
        write_policy = (
            "Execution profile: rw. Agents with read_write permission may modify files in service "
            "of the task. Agents with read_only permission must not modify files; Stringbean will "
            "treat modifications as a policy violation."
        )
    else:
        write_policy = (
            "Execution profile: ro. Treat this run as create-only. You may create new files or "
            "new directories, but you must not modify, delete, rename, move, or type-change "
            "pre-existing repository paths. Stringbean will treat forbidden changes as policy "
            "violations, even for agents whose configured role is read_write."
        )
    return (
        "Stringbean execution policy:\n"
        f"- {write_policy}\n"
        f"- Effective permission for this call: {effective_permission}.\n"
        f"- Do not run these denied commands: {denied}.\n"
        f"- Do not run these denied git operations: {denied_git}.\n"
        "- If a denied operation appears necessary, stop and report it instead of running it."
    )


def install_command_policy_wrappers(
    directory: Path,
    denied_commands: Iterable[str] = DENIED_COMMANDS,
    denied_git_subcommands: Iterable[str] = DENIED_GIT_SUBCOMMANDS,
) -> Path:
    """Create PATH shims that block common destructive commands for subagents."""
    bin_dir = directory / "policy-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    denied_text = ", ".join(sorted(set(denied_commands)))
    for command in sorted(set(denied_commands)):
        wrapper = bin_dir / command
        wrapper.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"stringbean policy: command '{command}' is denied for subagents.\" >&2\n"
            f"echo \"denied commands: {denied_text}\" >&2\n"
            "exit 126\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)

    real_git = shutil.which("git", path=os.environ.get("PATH", ""))
    if real_git:
        denied_git = "|".join(sorted(set(denied_git_subcommands)))
        git_wrapper = bin_dir / "git"
        git_wrapper.write_text(
            "#!/usr/bin/env bash\n"
            f"case \"${{1:-}}\" in\n"
            f"  {denied_git})\n"
            "    echo \"stringbean policy: this git operation is denied for subagents: git ${1:-}\" >&2\n"
            "    exit 126\n"
            "    ;;\n"
            "esac\n"
            f"exec {real_git!r} \"$@\"\n",
            encoding="utf-8",
        )
        git_wrapper.chmod(0o755)

    return bin_dir
