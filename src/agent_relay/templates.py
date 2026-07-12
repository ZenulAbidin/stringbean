from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable

from .config import PROJECT_DIR_NAME


TEMPLATES = {
    "orchestrator-planning": "orchestrator-planning.md",
    "advisor-review": "advisor-review.md",
    "orchestrator-revision": "orchestrator-revision.md",
    "implementer-task": "implementer-task.md",
    "reviewer-review": "reviewer-review.md",
    "implementer-fix-request": "implementer-fix-request.md",
    "final-summary": "final-summary.md",
}


def available_template_names() -> Iterable[str]:
    return TEMPLATES.keys()


@dataclass
class TemplateNotFound(Exception):
    name: str


def _pkg_template_path(name: str) -> Path:
    return Path(__file__).resolve().parent / "templates" / name


def load_template(name: str, project_root: Path) -> str:
    if name not in TEMPLATES:
        raise TemplateNotFound(name)
    file_name = TEMPLATES[name]
    local = project_root / PROJECT_DIR_NAME / "templates" / file_name
    if local.exists():
        return local.read_text(encoding="utf-8")
    return _pkg_template_path(file_name).read_text(encoding="utf-8")


def render_template(name: str, project_root: Path, values: Dict[str, str]) -> str:
    content = load_template(name, project_root)
    rendered = content
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{ {key} }}}}", value)
    return rendered
