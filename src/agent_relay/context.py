from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Dict, List

from .exclusions import RepositoryExclusions
from .policy import git_command, internal_subprocess_env
from .utils import git_status_short


_MAX_GUIDANCE_FILE_BYTES = 128 * 1024


def top_level_files(path: Path, exclusions: RepositoryExclusions | None = None) -> List[str]:
    exclusions = exclusions or RepositoryExclusions.discover(path)
    files: list[str] = []
    try:
        entries = path.iterdir()
    except OSError:
        return files
    for entry in entries:
        if entry.name.startswith(".") or entry.is_symlink() or not entry.is_file():
            continue
        if exclusions.is_excluded(entry):
            continue
        files.append(entry.name)
    return sorted(files)[:100]


def read_text_if_present(path: Path, exclusions: RepositoryExclusions | None = None) -> str:
    if exclusions is not None and exclusions.is_excluded(path):
        return ""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_GUIDANCE_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _git_status_summary(root: Path) -> str:
    status = git_status_short(root)
    changed_count = sum(1 for line in status.splitlines() if line.strip())
    if not changed_count:
        return "clean"
    noun = "entry" if changed_count == 1 else "entries"
    return f"{changed_count} changed {noun}; paths omitted from provider context"


def collect_repo_context(
    root: Path,
    exclusions: RepositoryExclusions | None = None,
) -> Dict[str, object]:
    root = Path(root).resolve()
    exclusions = exclusions or RepositoryExclusions.discover(root)
    git_root: Path | None = None
    branch = "N/A"
    git_executable_available = True
    git_repository = False

    try:
        proc = subprocess.run(
            [git_command(), "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env=internal_subprocess_env(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            git_root = Path(proc.stdout.strip()).resolve()
            git_repository = True
            proc2 = subprocess.run(
                [git_command(), "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                env=internal_subprocess_env(),
            )
            if proc2.returncode == 0:
                branch = proc2.stdout.strip()
    except FileNotFoundError:
        git_executable_available = False

    context = {
        "cwd": str(root),
        "workspace_root": str(root),
        "workspace_type": "git-worktree" if git_repository else "directory",
        "git_root": str(git_root) if git_root is not None else None,
        "git_available": git_executable_available,
        "git_repository": git_repository,
        "current_branch": branch,
        "git_status": _git_status_summary(root) if git_repository else "not a git worktree",
        "top_level_files": top_level_files(root, exclusions),
        "AGENTS.md": read_text_if_present(root / "AGENTS.md", exclusions),
        "CLAUDE.md": read_text_if_present(root / "CLAUDE.md", exclusions),
        "README.md": read_text_if_present(root / "README.md", exclusions),
        "excluded_path_patterns": list(exclusions.prompt_patterns()),
        "excluded_nested_repositories": list(exclusions.nested_repository_roots),
        "scope_note": (
            "Use workspace_root as the default scope. Explicit user-named paths are also in scope, "
            "but never read excluded paths; skip an excluded path without retrying it."
        ),
    }
    return context
