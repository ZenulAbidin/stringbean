from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .utils import git_status_short


def top_level_files(path: Path) -> List[str]:
    return sorted([p.name for p in path.iterdir() if p.is_file() and not p.name.startswith(".")][:100])


def read_text_if_present(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def collect_repo_context(root: Path) -> Dict[str, object]:
    import subprocess

    git_root = root
    branch = "N/A"
    git_available = True
    status = ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            git_root = Path(proc.stdout.strip())
        proc2 = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc2.returncode == 0:
            branch = proc2.stdout.strip()
    except FileNotFoundError:
        git_available = False
    status = git_status_short(git_root) if git_available else "git unavailable"

    context = {
        "cwd": str(root),
        "git_root": str(git_root),
        "git_available": git_available,
        "current_branch": branch,
        "git_status": status,
        "top_level_files": top_level_files(root),
        "AGENTS.md": read_text_if_present(root / "AGENTS.md"),
        "CLAUDE.md": read_text_if_present(root / "CLAUDE.md"),
        "README.md": read_text_if_present(root / "README.md"),
        ".codex": str((root / ".codex").resolve()),
        ".claude": str((root / ".claude").resolve()),
    }
    return context
