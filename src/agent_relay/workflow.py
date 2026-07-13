from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import subprocess
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

from rich.console import Console
from rich.text import Text

from .connectors import Adapter, AdapterCapabilities, ClaudeConnector, CodexConnector, GenericConnector, GrokConnector
from .config import AgentConfig, Config
from .context import collect_repo_context
from .models import (
    AdvisorResponse,
    AgentCallResult,
    ImplementerResponse,
    OrchestratorPlan,
    ReviewerResponse,
    RunEvent,
    RunStatus,
)
from .parser import parse_structured_output
from .policy import (
    DENIED_COMMANDS,
    DENIED_GIT_SUBCOMMANDS,
    POLICY_PRELOAD_NAME,
    apply_codex_execution_profile,
    git_command,
    install_command_policy_wrappers,
    internal_subprocess_env,
    normalize_execution_profile,
    path_without_policy_bins,
    policy_prompt,
)
from .runner import RunnerConfig, RunnerOutput, run_subprocess
from .state import CallStore, RunDirectory, RunEventStore, RunState, now_iso
from .streaming import LiveStreamFormatter
from .templates import render_template
from .utils import (
    environment_redaction_values,
    git_status_short,
    merged_environment,
    redact_environment_payload,
    redact_environment_text,
)
from pydantic import BaseModel


ADAPTERS = {
    "codex": CodexConnector,
    "claude": ClaudeConnector,
    "grok": GrokConnector,
    "generic": GenericConnector,
}


MODE_CHOICES = {"auto", "low", "medium", "high"}
_STATUS_RENAME_SEPARATOR = "\0"
_NON_GIT_STATUS_PREFIX = "NG:"
PathContentState = Tuple[str, bytes, Optional[int]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def infer_mode_for_task(task: str) -> str:
    """Heuristic mode inference from task text."""
    normalized = task.lower()
    high_signals = (
        "refactor",
        "architecture",
        "design",
        "migrate",
        "migration",
        "security",
        "scalability",
        "integration",
        "multi-step",
        "multi step",
        "investigate",
        "investigation",
        "rewrite",
        "overhaul",
        "concurrency",
        "distributed",
        "algorithm",
        "complex",
    )
    medium_signals = (
        "add",
        "update",
        "remove",
        "rename",
        "fix",
        "improve",
        "cleanup",
        "tests",
        "test",
        "docs",
        "documentation",
        "bug",
        "feature",
        "implement",
        "build",
        "ci",
        "integration tests",
    )

    if any(token in normalized for token in high_signals):
        return "high"
    if any(token in normalized for token in medium_signals):
        return "medium"
    if len(task.split()) > 80:
        return "high"
    if len(task.split()) > 25:
        return "medium"
    return "low"


def _normalize_mode(value: Optional[str]) -> str:
    if value is None:
        return "auto"
    value = value.strip().lower()
    if value not in MODE_CHOICES:
        raise ValueError(f"invalid mode: {value}")
    return value


class _StageTransitionError(RuntimeError):
    pass


def build_adapters(config: Config) -> Dict[str, Adapter]:
    out: Dict[str, Adapter] = {}
    for name, agent in config.agents.items():
        adapter_name = agent.adapter.lower()
        cls = ADAPTERS.get(adapter_name, GenericConnector)
        out[name] = cls(agent)
    return out


class WorkflowEngine:
    def __init__(
        self,
        config: Config,
        run_dir: RunDirectory,
        run_state: RunState,
        console: Optional[Console] = None,
        quiet: bool = False,
        execution_profile: str = "rw",
        codex_progress: bool = False,
        progress_interval_seconds: float = 30.0,
        repo_root: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.state = run_state
        self.console = console or Console()
        self.quiet = quiet
        self.codex_progress = codex_progress
        self.progress_interval_seconds = progress_interval_seconds
        self.execution_profile = normalize_execution_profile(execution_profile)
        self.adapters = build_adapters(config)
        self.events = RunEventStore(self.run_dir.events_path)
        self.call_store = CallStore(self.run_dir.calls_dir)
        resolved_repo_root = Path(repo_root).resolve() if repo_root is not None else self.run_dir.path
        if repo_root is None:
            for _ in range(3):
                resolved_repo_root = resolved_repo_root.parent
        self.repo_root = resolved_repo_root
        self.call_counter = 0
        if self.run_dir.calls_dir.exists():
            existing = [p for p in self.run_dir.calls_dir.iterdir() if p.is_dir()]
            self.call_counter = len(existing)
        self.config_snapshot_written = False
        self._agent_stream_open_line = False
        self._agent_stream_formatter: Optional[LiveStreamFormatter] = None
        self._agent_output_redaction_values: list[str] = []
        self._latest_response_summary: Optional[str] = None
        self._latest_implementation_summary: Optional[str] = None
        self.policy_bin_dir = install_command_policy_wrappers(self.run_dir.path)

        if self.run_dir.task_path.exists():
            self.state.state.task = self.run_dir.task_path.read_text(encoding="utf-8").strip()

    def _log(self, message: str) -> None:
        if self.quiet:
            return
        self._flush_agent_stream()
        if self._agent_stream_open_line:
            print("", flush=True)
            self._agent_stream_open_line = False
        self._print_stream_line(message)

    def _write_agent_stream_line(self, line: str) -> None:
        if self._agent_output_redaction_values:
            line = redact_environment_text(line, self._agent_output_redaction_values)
        self._print_stream_line(line)
        self._agent_stream_open_line = False

    def _print_stream_line(self, line: str) -> None:
        self.console.print(self._styled_stream_line(line))

    @staticmethod
    def _styled_stream_line(line: str) -> Text:
        label_styles = {
            "Tool Call": "bold white",
            "Executed": "bold white",
            "Plan": "bold white",
            "Result": "bold white",
            "Review": "bold white",
            "Response": "bold white",
            "Progress": "bold white",
            "Agent": "bold white",
            "[stringbean]": "bold white",
        }
        for label, style in label_styles.items():
            prefix = f"{label}: "
            if line.startswith(prefix):
                return Text.assemble((label, style), (": ", style), (line[len(prefix) :], "white"))
            if line.startswith(label) and label.startswith("["):
                return Text(line, style)
        if line.startswith("  "):
            return Text(line, style="white")
        return Text(line, style="white")

    def _ensure_agent_stream_formatter(self) -> LiveStreamFormatter:
        if self._agent_stream_formatter is None:
            write_line = (
                self._write_codex_agent_stream_line
                if self.quiet and self.codex_progress
                else self._write_agent_stream_line
            )
            self._agent_stream_formatter = LiveStreamFormatter(write_line)
        return self._agent_stream_formatter

    def _flush_agent_stream(self) -> None:
        if self._agent_stream_formatter is not None:
            self._agent_stream_formatter.flush()

    def _stream_agent_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        if self.quiet and not self.codex_progress:
            return
        self._ensure_agent_stream_formatter().feed(chunk)

    def _write_codex_agent_stream_line(self, line: str) -> None:
        if self._agent_output_redaction_values:
            line = redact_environment_text(line, self._agent_output_redaction_values)
        line = self._shorten_text(line, limit=420)
        if not line or line == "stream output start" or self._is_reasoning_stream_line(line):
            return
        if self._agent_stream_open_line:
            print("", flush=True)
            self._agent_stream_open_line = False
        print(f"STRINGBEAN_INTERMEDIATE: Agent output: {line}", flush=True)
        self._agent_stream_open_line = False

    @staticmethod
    def _is_reasoning_stream_line(line: str) -> bool:
        normalized = line.lstrip().lower().replace("-", "_").replace(".", "_")
        return normalized.startswith(
            (
                "reasoning:",
                "reasoning_summary:",
                "reasoning summary:",
                "chain_of_thought:",
                "scratchpad:",
                "scratch_thoughts:",
                "scratch thoughts:",
            )
        )

    def _progress(self, message: str) -> None:
        if not self.codex_progress:
            return
        self._flush_agent_stream()
        if self._agent_stream_open_line:
            print("", flush=True)
            self._agent_stream_open_line = False
        print(f"STRINGBEAN_INTERMEDIATE: {message}", flush=True)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        seconds = max(0, int(seconds))
        minutes, remainder = divmod(seconds, 60)
        if minutes:
            return f"{minutes}m {remainder:02d}s"
        return f"{remainder}s"

    @staticmethod
    def _shorten_text(value: object, limit: int = 220) -> str:
        text = str(value).strip()
        text = " ".join(part.strip() for part in text.splitlines() if part.strip())
        if len(text) <= limit:
            return text
        return f"{text[: limit - 1]}…"

    @classmethod
    def _preview_items(cls, values: object, *, key: str | None = None, limit: int = 4) -> str:
        if not isinstance(values, list) or not values:
            return ""
        rendered: list[str] = []
        for item in values[:limit]:
            if isinstance(item, dict):
                raw = item.get(key or "") if key else None
                raw = raw or item.get("title") or item.get("id") or item.get("summary") or item.get("issue") or item
            else:
                raw = item
            rendered.append(cls._shorten_text(raw, limit=90))
        if len(values) > limit:
            rendered.append(f"+{len(values) - limit} more")
        return "; ".join(rendered)

    def _progress_event(self, status: RunStatus, event: str, payload: Dict[str, object]) -> None:
        message: str | None = None
        if event == "dirty-repo":
            message = "Progress: Repository has uncommitted changes; continuing because clean-start enforcement is off."
        elif event == "start-planning":
            message = "Progress: Planning started — the orchestrator is turning the request into concrete tasks."
        elif event == "plan-complete":
            tasks = self._preview_items(payload.get("tasks"))
            suffix = f": {tasks}" if tasks else "."
            message = f"Progress: Planning complete — selected task IDs{suffix}"
        elif event == "start-advisor":
            message = "Progress: Advisor review started — checking the plan before implementation."
        elif event == "advisor-revision-requested":
            message = "Progress: Advisor requested a plan revision; orchestrator is revising the plan."
        elif event == "advisor-blocked":
            message = "Progress: Advisor blocked the run; finalizing with failure details."
        elif event == "advisor-complete":
            message = "Progress: Advisor review complete."
        elif event == "start-implementation":
            message = "Progress: Implementation started — running planned task work."
        elif event == "implementer-incomplete":
            message = f"Progress: Implementer reported remaining issues on {payload.get('task')}."
        elif event == "implementing-complete":
            message = f"Progress: Implementation complete — {payload.get('count', 0)} task(s) marked implemented."
        elif event == "review-skipped":
            message = "Progress: Review skipped because max review rounds is 0."
        elif event == "reviewing":
            message = f"Progress: Review round {payload.get('round')} started — reviewer is checking the result."
        elif event == "review-approved":
            message = f"Progress: Review round {payload.get('round')} approved the result."
        elif event == "start-fix":
            message = f"Progress: Fix pass started for review round {payload.get('round')}."
        elif event == "fixes-complete":
            message = f"Progress: Fix pass complete for review round {payload.get('round')}; returning to review."
        elif event == "review-rejected":
            message = "Progress: Reviewer rejected the result; finalizing with failure details."
        elif event == "review-round-limit":
            message = f"Progress: Review round limit reached ({payload.get('max_rounds')}); finalizing with failure details."
        elif event == "finalized":
            message = f"Progress: Finalized run with status {status.value}."
        if message:
            self._progress(message)

    def _progress_agent_start(self, role: str, agent_name: str, agent: AgentConfig) -> None:
        mode = self._agent_mode(agent_name) or "default"
        permission = self._effective_permission(agent)
        self._progress(
            f"Agent: {role} {agent_name} started "
            f"(mode={mode}, profile={self.execution_profile}, permission={permission})."
        )

    def _progress_agent_wait(self, role: str, agent_name: str, elapsed_seconds: float) -> None:
        elapsed = self._format_elapsed(elapsed_seconds)
        targets = {
            "orchestrator": "plan",
            "advisor": "advisor verdict",
            "implementer": "implementation result",
            "reviewer": "review verdict",
        }
        target = targets.get(role, "structured result")
        self._progress(f"Agent: {role} {agent_name} still running ({elapsed}) — awaiting {target}.")

    def _progress_agent_finish(self, role: str, agent_name: str, exit_code: Optional[int], duration_seconds: float) -> None:
        elapsed = self._format_elapsed(duration_seconds)
        self._progress(f"Agent: {role} {agent_name} finished in {elapsed} (exit={exit_code}).")

    def _progress_payload(self, role: str, payload: Dict[str, Any]) -> None:
        summary = self._shorten_text(payload.get("summary") or "")
        if "tasks" in payload:
            task_preview = self._preview_items(payload.get("tasks"), key="title")
            if summary:
                self._progress(f"Progress: Plan summary — {summary}")
            if task_preview:
                self._progress(f"Progress: Planned tasks — {task_preview}")
            return

        if "verdict" in payload:
            verdict = self._shorten_text(payload.get("verdict") or "unknown", limit=60)
            detail = f" — {summary}" if summary else ""
            label = "Advisor verdict" if role == "advisor" else "Review verdict"
            self._progress(f"Progress: {label} — {verdict}{detail}")
            fixes = self._preview_items(payload.get("required_fixes") or payload.get("blockers") or payload.get("blocking_issues"))
            if fixes:
                self._progress(f"Progress: {label} details — {fixes}")
            return

        if "status" in payload:
            status = self._shorten_text(payload.get("status") or "unknown", limit=60)
            detail = f" — {summary}" if summary else ""
            self._progress(f"Progress: Implementation result — {status}{detail}")
            files = self._preview_items(payload.get("files_changed"))
            tests = self._preview_items(payload.get("tests"))
            remaining = self._preview_items(payload.get("remaining_issues"))
            if files:
                self._progress(f"Progress: Files touched — {files}")
            if tests:
                self._progress(f"Progress: Tests reported — {tests}")
            if remaining:
                self._progress(f"Progress: Remaining issues — {remaining}")
            return

        if summary:
            self._progress(f"Progress: {role} summary — {summary}")

    def _remember_agent_response(self, role: str, payload: Optional[Dict[str, object]]) -> None:
        if not payload:
            return
        summary = payload.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return
        summary = summary.strip()
        self._latest_response_summary = summary
        if role in {"implementer", "fixer"}:
            self._latest_implementation_summary = summary

    def _mark(self, status: RunStatus, event: str, payload: Optional[Dict[str, object]] = None) -> None:
        self.state.state.mark(status, datetime.now(timezone.utc))
        self.state.write()
        payload = payload or {}
        self.events.append(RunEvent(timestamp=_now_iso(), stage=status, event=event, payload=payload))
        self._progress_event(status, event, payload)

    @staticmethod
    def _candidate_mode_from_command(command: Optional[List[str]]) -> Optional[str]:
        if not command:
            return None
        lowered = [str(item).lower() for item in command]
        for idx, part in enumerate(lowered):
            if part in {"--reasoning-effort", "--reasoning", "--reasoning-level"} and idx + 1 < len(lowered):
                candidate = lowered[idx + 1].strip()
                if candidate in {"high", "medium", "low"}:
                    return candidate
            if part.startswith("--reasoning-effort="):
                candidate = part.split("=", 1)[-1]
                if candidate in {"high", "medium", "low"}:
                    return candidate
            if part.startswith("--reasoning="):
                candidate = part.split("=", 1)[-1]
                if candidate in {"high", "medium", "low"}:
                    return candidate
            if part.startswith("--reasoning-level="):
                candidate = part.split("=", 1)[-1]
                if candidate in {"high", "medium", "low"}:
                    return candidate
        return None

    def _agent_mode(self, agent_name: str) -> Optional[str]:
        cfg = self.config.agents[agent_name]
        if cfg.mode:
            return cfg.mode
        return self._candidate_mode_from_command(cfg.command)

    def _effective_permission(self, agent: AgentConfig) -> str:
        if self.execution_profile == "ro":
            return "read_only"
        return agent.permissions

    def _should_track_repo_diff(self, agent: AgentConfig) -> bool:
        return self.execution_profile == "ro" or agent.permissions == "read_only"

    def _policy_violation_message(self, agent_name: str, changed_files: List[str]) -> str:
        prefix = "read-only profile policy violation" if self.execution_profile == "ro" else "read-only role policy violation"
        return f"{prefix} in {agent_name}: forbidden changes {', '.join(changed_files)}"

    def _policy_retry_prompt(
        self,
        prompt: str,
        *,
        agent_name: str,
        role: str,
        denied_paths: List[str],
        retry_attempt: int,
        retry_limit: int,
    ) -> str:
        paths = "\n".join(f"- {path}" for path in denied_paths)
        return (
            "Policy retry instruction:\n"
            f"The previous {role} attempt by {agent_name} violated Stringbean filesystem policy.\n"
            f"Retry {retry_attempt} of {retry_limit} must complete the same role without modifying these forbidden paths:\n"
            f"{paths}\n"
            "Reframe the task as analysis-only for this role. Do not edit, delete, rename, move, or type-change "
            "pre-existing files. If edits are needed, report the needed changes in your structured response instead "
            "of applying them. Return the required structured output schema.\n\n"
            f"{prompt}"
        )

    @staticmethod
    def _repo_status_entries(status_output: bytes | str) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        if isinstance(status_output, bytes):
            status_text = status_output.decode("utf-8", errors="surrogateescape")
        else:
            status_text = status_output
        if "\0" not in status_text:
            for line in status_text.splitlines():
                if len(line) < 4 or not line.strip():
                    continue
                status = line[:2]
                path_text = line[3:]
                if "R" in status[:2] or "C" in status[:2]:
                    rename_paths = WorkflowEngine._split_porcelain_rename_path(path_text)
                    if rename_paths:
                        old_path, new_path = rename_paths
                        entries[f"{old_path}{_STATUS_RENAME_SEPARATOR}{new_path}"] = status
                        continue
                path = WorkflowEngine._decode_porcelain_path(path_text)
                if path:
                    entries[path] = status
            return entries

        records = status_text.split("\0")
        idx = 0
        while idx < len(records):
            record = records[idx]
            idx += 1
            if len(record) < 4 or not record.strip():
                continue
            status = record[:2]
            path = record[3:]
            if path:
                if "R" in status[:2] or "C" in status[:2]:
                    if idx >= len(records):
                        continue
                    old_path = records[idx]
                    idx += 1
                    entries[f"{old_path}{_STATUS_RENAME_SEPARATOR}{path}"] = status
                else:
                    entries[path] = status
        return entries

    @staticmethod
    def _decode_porcelain_path(path: str) -> str:
        if not path.startswith('"'):
            return path

        decoded = bytearray()
        idx = 1
        while idx < len(path):
            char = path[idx]
            idx += 1
            if char == '"':
                break
            if char != "\\":
                decoded.extend(char.encode("utf-8", errors="surrogateescape"))
                continue
            if idx >= len(path):
                decoded.extend(b"\\")
                break

            escaped = path[idx]
            idx += 1
            escape_bytes = {
                "a": b"\a",
                "b": b"\b",
                "f": b"\f",
                "n": b"\n",
                "r": b"\r",
                "t": b"\t",
                "v": b"\v",
                "\\": b"\\",
                '"': b'"',
            }
            if escaped in escape_bytes:
                decoded.extend(escape_bytes[escaped])
                continue
            if "0" <= escaped <= "7":
                octal = escaped
                while idx < len(path) and len(octal) < 3 and "0" <= path[idx] <= "7":
                    octal += path[idx]
                    idx += 1
                decoded.append(int(octal, 8))
                continue
            decoded.extend(escaped.encode("utf-8", errors="surrogateescape"))

        return decoded.decode("utf-8", errors="surrogateescape")

    @staticmethod
    def _split_porcelain_rename_path(path: str) -> Optional[Tuple[str, str]]:
        in_quote = False
        escaped = False
        idx = 0
        while idx < len(path):
            char = path[idx]
            if escaped:
                escaped = False
            elif char == "\\" and in_quote:
                escaped = True
            elif char == '"':
                in_quote = not in_quote
            elif not in_quote and path.startswith(" -> ", idx):
                old_path = WorkflowEngine._decode_porcelain_path(path[:idx])
                new_path = WorkflowEngine._decode_porcelain_path(path[idx + 4 :])
                return old_path, new_path
            idx += 1
        return None

    def _repo_status_snapshot(self) -> Dict[str, str]:
        try:
            proc = subprocess.run(
                [git_command(), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=False,
                env=internal_subprocess_env(),
            )
        except FileNotFoundError:
            return self._non_git_status_snapshot()
        if proc.returncode == 0:
            entries = self._repo_status_entries(proc.stdout)
            for path, status in self._git_ignored_status_snapshot().items():
                entries.setdefault(path, status)
            return entries
        return self._non_git_status_snapshot()

    def _git_ignored_status_snapshot(self) -> Dict[str, str]:
        try:
            proc = subprocess.run(
                [git_command(), "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=False,
                env=internal_subprocess_env(),
            )
        except FileNotFoundError:
            return {}
        if proc.returncode != 0:
            return {}
        entries: Dict[str, str] = {}
        for raw in proc.stdout.split(b"\0"):
            if not raw:
                continue
            path = raw.decode("utf-8", errors="surrogateescape")
            if self._is_internal_run_path(self.repo_root / path):
                continue
            path_parts = Path(path).parts
            for idx in range(1, len(path_parts)):
                parent = Path(*path_parts[:idx]).as_posix()
                if parent not in entries:
                    kind, content, mode = self._path_content_state(parent)
                    digest = hashlib.sha256(content).hexdigest()
                    mode_text = "" if mode is None else f":{mode:o}"
                    entries[parent] = f"{_NON_GIT_STATUS_PREFIX}{kind}:{digest}{mode_text}"
            kind, content, mode = self._path_content_state(path)
            digest = hashlib.sha256(content).hexdigest()
            mode_text = "" if mode is None else f":{mode:o}"
            entries[path] = f"{_NON_GIT_STATUS_PREFIX}{kind}:{digest}{mode_text}"
        return entries

    def _is_internal_run_path(self, path: Path) -> bool:
        try:
            path.relative_to(self.run_dir.path)
            return True
        except ValueError:
            return False

    def _non_git_status_snapshot(self) -> Dict[str, str]:
        entries: Dict[str, str] = {}
        for current_root, dir_names, file_names in os.walk(self.repo_root, topdown=True, followlinks=False):
            root_path = Path(current_root)
            kept_dirs = []
            for dir_name in dir_names:
                dir_path = root_path / dir_name
                if dir_name == ".git" or self._is_internal_run_path(dir_path):
                    continue
                kept_dirs.append(dir_name)
                rel_dir = dir_path.relative_to(self.repo_root).as_posix()
                entries[rel_dir] = f"{_NON_GIT_STATUS_PREFIX}dir"
            dir_names[:] = kept_dirs

            for file_name in file_names:
                file_path = root_path / file_name
                if self._is_internal_run_path(file_path):
                    continue
                rel_file = file_path.relative_to(self.repo_root).as_posix()
                try:
                    if file_path.is_symlink():
                        target = os.readlink(file_path).encode("utf-8", errors="surrogateescape")
                        digest = hashlib.sha256(target).hexdigest()
                        entries[rel_file] = f"{_NON_GIT_STATUS_PREFIX}symlink:{digest}"
                        continue
                    if not file_path.is_file():
                        entries[rel_file] = f"{_NON_GIT_STATUS_PREFIX}other"
                        continue
                    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
                    entries[rel_file] = f"{_NON_GIT_STATUS_PREFIX}file:{digest}"
                except OSError:
                    entries[rel_file] = f"{_NON_GIT_STATUS_PREFIX}unreadable"
        return entries

    def _path_content_state(self, path: str) -> PathContentState:
        full_path = self.repo_root / path
        if not os.path.lexists(full_path):
            return ("missing", b"", None)
        try:
            mode = os.stat(full_path, follow_symlinks=False).st_mode & 0o7777
        except OSError:
            mode = None
        if full_path.is_symlink():
            try:
                return ("symlink", os.readlink(full_path).encode("utf-8", errors="surrogateescape"), mode)
            except OSError:
                return ("unreadable", b"", mode)
        if full_path.is_dir():
            return ("dir", b"", mode)
        try:
            return ("file", full_path.read_bytes(), mode)
        except OSError:
            return ("unreadable", b"", mode)

    def _git_tracked_paths(self) -> List[str]:
        try:
            proc = subprocess.run(
                [git_command(), "ls-files", "-z"],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=False,
                env=internal_subprocess_env(),
            )
        except FileNotFoundError:
            return []
        if proc.returncode != 0:
            return []
        return [
            raw.decode("utf-8", errors="surrogateescape")
            for raw in proc.stdout.split(b"\0")
            if raw
        ]

    def _is_git_worktree(self) -> bool:
        try:
            proc = subprocess.run(
                [git_command(), "rev-parse", "--is-inside-work-tree"],
                cwd=self.repo_root,
                check=False,
                capture_output=True,
                text=True,
                env=internal_subprocess_env(),
            )
        except FileNotFoundError:
            return False
        return proc.returncode == 0 and proc.stdout.strip() == "true"

    def _repo_baseline_content_snapshot(self, status_entries: Dict[str, str]) -> Dict[str, PathContentState]:
        snapshot: Dict[str, PathContentState] = {}
        for current_root, dir_names, _ in os.walk(self.repo_root, topdown=True, followlinks=False):
            root_path = Path(current_root)
            kept_dirs = []
            for dir_name in dir_names:
                dir_path = root_path / dir_name
                if dir_name == ".git" or self._is_internal_run_path(dir_path):
                    continue
                kept_dirs.append(dir_name)
                rel_dir = dir_path.relative_to(self.repo_root).as_posix()
                snapshot[rel_dir] = self._path_content_state(rel_dir)
            dir_names[:] = kept_dirs
        for tracked_path in self._git_tracked_paths():
            snapshot[tracked_path] = self._path_content_state(tracked_path)
        for status_path in status_entries:
            old_path, new_path = self._split_status_path(status_path)
            snapshot[old_path] = self._path_content_state(old_path)
            if new_path:
                snapshot[new_path] = self._path_content_state(new_path)
        return snapshot

    def _repo_content_snapshot_for_paths(self, paths: Iterable[str]) -> Dict[str, PathContentState]:
        return {path: self._path_content_state(path) for path in paths}

    @staticmethod
    def _is_create_only_status(status: Optional[str]) -> bool:
        if status is None:
            return False
        if status == "??":
            return True
        if status.startswith("A") and not any(marker in status for marker in ("D", "R", "C", "T", "U")):
            return True
        return False

    @staticmethod
    def _is_non_git_status(status: Optional[str]) -> bool:
        return bool(status and status.startswith(_NON_GIT_STATUS_PREFIX))

    def _is_created_path_status(self, before_status: Optional[str], after_status: Optional[str]) -> bool:
        if after_status is None:
            return False
        if self._is_create_only_status(after_status):
            return before_status is None or self._is_create_only_status(before_status)
        if self._is_non_git_status(after_status):
            return before_status is None
        return False

    def _classify_repo_delta(
        self,
        before: Dict[str, str],
        after: Dict[str, str],
        *,
        allow_creates: bool,
        before_contents: Optional[Dict[str, PathContentState]] = None,
        after_contents: Optional[Dict[str, PathContentState]] = None,
    ) -> Tuple[List[str], List[str], List[str]]:
        before_contents = before_contents or {}
        after_contents = after_contents or {}
        status_changed = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
        content_changed = {
            path
            for path, before_content in before_contents.items()
            if before_content != after_contents.get(path, ("missing", b"", None))
        }
        changed = sorted(status_changed | content_changed)
        allowed: List[str] = []
        denied: List[str] = []
        for path in changed:
            if path in content_changed and path not in status_changed:
                denied.append(path)
                continue
            before_status = before.get(path)
            after_status = after.get(path)
            if allow_creates and self._is_created_path_status(before_status, after_status):
                allowed.append(path)
                continue
            denied.append(path)
        return changed, allowed, denied

    @staticmethod
    def _display_status_path(path: str) -> str:
        old_path, new_path = WorkflowEngine._split_status_path(path)
        if new_path:
            return f"{old_path} -> {new_path}"
        return old_path

    @staticmethod
    def _display_status_paths(paths: Iterable[str]) -> List[str]:
        return [WorkflowEngine._display_status_path(path) for path in paths]

    def _prepare_agent_command(self, agent: AgentConfig, command: List[str]) -> List[str]:
        if agent.adapter.lower() == "codex" or (command and Path(command[0]).name == "codex"):
            return apply_codex_execution_profile(command, self.execution_profile)
        return command

    def _apply_subagent_policy_env(self, env_overrides: Dict[str, str]) -> Dict[str, str]:
        out = dict(env_overrides)
        existing_path = out.get("PATH") or os.environ.get("PATH", "")
        existing_path = path_without_policy_bins(existing_path)
        out["PATH"] = f"{self.policy_bin_dir}{os.pathsep}{existing_path}" if existing_path else str(self.policy_bin_dir)
        out["STRINGBEAN_EXECUTION_PROFILE"] = self.execution_profile
        out["STRINGBEAN_DENIED_COMMANDS"] = ",".join(DENIED_COMMANDS)
        out["STRINGBEAN_DENIED_GIT_SUBCOMMANDS"] = ",".join(DENIED_GIT_SUBCOMMANDS)
        preload_path = self.policy_bin_dir / POLICY_PRELOAD_NAME
        out["STRINGBEAN_POLICY_BIN"] = str(self.policy_bin_dir)
        out["STRINGBEAN_POLICY_WRAPPERS_ACTIVE"] = "1"
        out["STRINGBEAN_POLICY_PRELOAD_ACTIVE"] = "1" if preload_path.is_file() else "0"
        if preload_path.is_file():
            existing_preload = out.get("LD_PRELOAD") or os.environ.get("LD_PRELOAD", "")
            out["LD_PRELOAD"] = f"{preload_path} {existing_preload}".strip()
            out["STRINGBEAN_POLICY_PRELOAD"] = str(preload_path)
        return out

    def _resolve_modes(self, task: str, global_mode: str, role_modes: Optional[Dict[str, str]]) -> Dict[str, str]:
        if not task:
            task = self.state.state.task

        resolved = {
            "orchestrator": _normalize_mode(global_mode),
            "advisor": _normalize_mode(global_mode),
            "implementer": _normalize_mode(global_mode),
            "reviewer": _normalize_mode(global_mode),
        }

        if role_modes:
            for role, value in role_modes.items():
                role = role.lower()
                if role not in resolved:
                    continue
                normalized = _normalize_mode(value)
                if normalized != "auto":
                    resolved[role] = normalized

        inferred = None
        for role in resolved:
            if resolved[role] == "auto":
                if inferred is None:
                    inferred = infer_mode_for_task(task)
                resolved[role] = inferred
        for role, value in resolved.items():
            if value not in {"low", "medium", "high"}:
                raise RuntimeError(f"Resolved mode {value!r} for {role} is not supported")
        return resolved

    def _agent_candidates_for_role(self, role: str) -> List[str]:
        if role == "orchestrator":
            return [self.config.workflow.orchestrator]
        if role == "advisor":
            return list(self.config.workflow.advisors)
        if role == "implementer":
            return list(self.config.workflow.implementers)
        if role == "reviewer":
            return list(self.config.workflow.reviewers)
        return []

    def _agent_for_role(self, role: str, mode: Optional[str] = None, override: Optional[str] = None) -> str:
        if role in self.state.state.selected_agents and self.state.state.selected_agents.get(role):
            return self.state.state.selected_agents[role]
        if override:
            return override
        candidates = self._agent_candidates_for_role(role)
        if not candidates:
            if role == "advisor":
                raise _StageTransitionError("No advisor configured")
            if role == "implementer":
                raise _StageTransitionError("No implementer configured")
            if role == "reviewer":
                raise _StageTransitionError("No reviewer configured")
            raise _StageTransitionError(f"Unsupported role {role}")

        if mode:
            for agent_name in candidates:
                if self._agent_mode(agent_name) == mode:
                    return agent_name

        return candidates[0]

    async def detect_capabilities(self) -> Dict[str, AdapterCapabilities]:
        out: Dict[str, AdapterCapabilities] = {}
        for name, adapter in self.adapters.items():
            out[name] = await adapter.detect(self.repo_root)
        return out

    def _run_dir_index(self) -> int:
        self.call_counter += 1
        return self.call_counter

    async def _run_agent(
        self,
        agent_name: str,
        role: str,
        stage: RunStatus,
        prompt: str,
        expected: Type[BaseModel],
        track_repo_diff: Optional[bool] = None,
        extra_env: Optional[Dict[str, str]] = None,
        policy_retry_attempt: int = 0,
    ) -> Tuple[AgentCallResult, Optional[str]]:
        if self.state.state.call_count >= self.state.state.total_calls_limit:
            raise RuntimeError("agent call limit reached")

        agent = self.config.agents[agent_name]
        adapter = self.adapters[agent_name]
        original_agent_name = agent_name

        command = list(adapter.build_command(prompt, self.repo_root))
        if not command:
            raise RuntimeError(f"agent {agent_name} missing command")
        command = self._prepare_agent_command(agent, command)

        attempted = {original_agent_name}
        while not shutil.which(command[0]):
            fallback_name = self.config.agents[agent_name].fallback_agent
            if not fallback_name:
                raise RuntimeError(f"agent {agent_name} executable unavailable: {command[0]}")
            if fallback_name not in self.adapters:
                raise RuntimeError(f"agent {agent_name} fallback agent missing: {fallback_name}")
            if fallback_name in attempted:
                raise RuntimeError(f"agent {agent_name} fallback chain loops at {fallback_name}")
            attempted.add(fallback_name)

            agent_name = fallback_name
            agent = self.config.agents[agent_name]
            adapter = self.adapters[agent_name]
            command = list(adapter.build_command(prompt, self.repo_root))
            if not command:
                raise RuntimeError(f"agent {agent_name} missing command")
            command = self._prepare_agent_command(agent, command)
            if not adapter.supports_prompt_transport(agent.prompt_transport):
                raise RuntimeError(f"agent {agent_name} does not support prompt transport {agent.prompt_transport}")

        if agent.prompt_transport not in {"stdin", "argv", "file"}:
            raise RuntimeError(f"unsupported prompt transport {agent.prompt_transport}")
        if not adapter.supports_prompt_transport(agent.prompt_transport):
            raise RuntimeError(f"agent {agent_name} does not support prompt transport {agent.prompt_transport}")

        if track_repo_diff is None:
            track_repo_diff = self._should_track_repo_diff(agent)
        baseline = None
        baseline_contents: Dict[str, PathContentState] = {}
        if track_repo_diff:
            baseline = self._repo_status_snapshot()
            baseline_contents = self._repo_baseline_content_snapshot(baseline)

        agent_prompt = prompt
        execution_prompt = policy_prompt(self.execution_profile, self._effective_permission(agent)) + "\n\n" + agent_prompt

        cfg_prompt = None
        prompt_file: Optional[Path] = None
        if agent.prompt_transport == "argv":
            command = command + [execution_prompt]
        elif agent.prompt_transport == "file":
            tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".md")
            tmp.write(execution_prompt)
            tmp.flush()
            tmp.close()
            prompt_file = Path(tmp.name)
            command = command + [tmp.name]
        else:
            cfg_prompt = execution_prompt

        env_overrides = dict(agent.environment_overrides)
        if extra_env:
            env_overrides.update(extra_env)
        env_overrides = self._apply_subagent_policy_env(env_overrides)
        env = merged_environment(env_overrides)
        if self.config.output.redact_environment_values:
            redaction_values = environment_redaction_values(env)
        else:
            redaction_values = []

        policy_metadata = {
            "selected_agent": original_agent_name,
            "effective_agent": agent_name,
            "requested_profile": self.state.state.execution_profile,
            "effective_profile": self.execution_profile,
            "execution_profile": self.execution_profile,
            "effective_permission": self._effective_permission(agent),
            "denied_commands": list(DENIED_COMMANDS),
            "denied_git_subcommands": list(DENIED_GIT_SUBCOMMANDS),
            "policy_bin": str(self.policy_bin_dir),
            "policy_wrappers_active": self.policy_bin_dir.is_dir(),
            "policy_preload_path": str(self.policy_bin_dir / POLICY_PRELOAD_NAME),
            "policy_preload_active": (self.policy_bin_dir / POLICY_PRELOAD_NAME).is_file(),
            "policy_retry_attempt": policy_retry_attempt,
            "policy_retry_limit": self.config.workflow.max_policy_violation_retries,
        }

        def record_execution_failure(parse_error: str, raw_stderr: str) -> None:
            timestamp = _now_iso()
            call_result = AgentCallResult(
                agent_name=agent_name,
                role=role,
                stage=stage,
                command=command,
                exit_code=None,
                duration_seconds=0.0,
                start_time=timestamp,
                end_time=timestamp,
                raw_stdout="",
                raw_stderr=raw_stderr,
                parsed_output=None,
                parse_error=parse_error,
                diff_delta_files=None,
                metadata=dict(policy_metadata),
            )
            self.state.state.call_count += 1
            idx = self._run_dir_index()
            self.call_store.write_call_files(idx, agent_name, execution_prompt, call_result)
            self.state.write()

        stream_agent_output = bool(self.config.output.stream_agent_output and not self.quiet)
        codex_agent_output = bool(self.codex_progress and not stream_agent_output)
        if stream_agent_output:
            self._agent_stream_formatter = LiveStreamFormatter(self._write_agent_stream_line)
            self._log(f"[stringbean] starting {role} agent: {agent_name}")
        elif codex_agent_output:
            self._agent_stream_formatter = LiveStreamFormatter(self._write_codex_agent_stream_line)
        if self.codex_progress:
            self._progress_agent_start(role, agent_name, agent)
        should_stream_agent_output = stream_agent_output or codex_agent_output
        callback = self._stream_agent_chunk if should_stream_agent_output else None
        progress_callback = (
            (lambda elapsed: self._progress_agent_wait(role, agent_name, elapsed))
            if self.codex_progress
            else None
        )
        previous_redaction_values = self._agent_output_redaction_values
        self._agent_output_redaction_values = redaction_values
        try:
            result = await run_subprocess(
                RunnerConfig(
                    command=command,
                    working_directory=self.repo_root / agent.working_directory,
                    env=env,
                    timeout_seconds=agent.timeout_seconds,
                    prompt=cfg_prompt,
                    on_stdout_line=callback,
                    on_stderr_line=callback,
                    on_progress=progress_callback,
                    progress_interval_seconds=self.progress_interval_seconds,
                )
            )
        except TimeoutError as exc:
            if should_stream_agent_output:
                self._flush_agent_stream()
                self._agent_stream_formatter = None
            if self.codex_progress:
                self._progress(f"Agent: {role} {agent_name} timed out.")
            record_execution_failure(f"agent {agent_name} timed out", str(exc))
            raise RuntimeError(f"agent {agent_name} timed out") from exc
        except Exception as exc:
            if should_stream_agent_output:
                self._flush_agent_stream()
                self._agent_stream_formatter = None
            if self.codex_progress:
                self._progress(f"Agent: {role} {agent_name} failed to execute: {self._shorten_text(exc)}")
            record_execution_failure(f"agent {agent_name} execution failed: {exc}", str(exc))
            raise RuntimeError(f"agent {agent_name} execution failed: {exc}") from exc
        finally:
            if prompt_file is not None:
                prompt_file.unlink(missing_ok=True)
            self._agent_output_redaction_values = previous_redaction_values
        if should_stream_agent_output:
            self._flush_agent_stream()
        if stream_agent_output:
            self._log(f"[stringbean] finished {role} agent: {agent_name} (exit {result.exit_code})")
        if should_stream_agent_output:
            self._agent_stream_formatter = None
        if self.codex_progress:
            self._progress_agent_finish(role, agent_name, result.exit_code, result.duration_seconds)

        parse_error: Optional[str] = None
        model_payload = None
        raw_payload = None

        if result.exit_code not in {0, None}:
            parse_error = f"agent exited with status {result.exit_code}"
        else:
            parsed, raw_payload, parse_error = parse_structured_output(result.raw_stdout, expected)
            model_payload = parsed.model_dump(mode="json") if parsed is not None else None
        stored_stdout = redact_environment_text(result.raw_stdout, redaction_values) if redaction_values else result.raw_stdout
        stored_stderr = redact_environment_text(result.raw_stderr, redaction_values) if redaction_values else result.raw_stderr
        stored_payload = (
            redact_environment_payload(model_payload, redaction_values)
            if model_payload is not None and redaction_values
            else model_payload
        )
        if self.codex_progress:
            if parse_error:
                self._progress(f"Progress: {role} result could not be accepted — {self._shorten_text(parse_error)}")
            elif stored_payload:
                self._progress_payload(role, stored_payload)

        call_result = AgentCallResult(
            agent_name=agent_name,
            role=role,
            stage=stage,
            command=result.command,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            start_time=result.start_time,
            end_time=result.end_time,
            raw_stdout=stored_stdout,
            raw_stderr=stored_stderr,
            parsed_output=stored_payload,
            parse_error=parse_error,
            diff_delta_files=None,
            metadata={},
        )

        changed_paths: List[str] | None = None
        denied_paths: List[str] = []
        if track_repo_diff and baseline is not None:
            after = self._repo_status_snapshot()
            after_contents = self._repo_content_snapshot_for_paths(baseline_contents)
            changed_path_keys, allowed_path_keys, denied_path_keys = self._classify_repo_delta(
                baseline,
                after,
                allow_creates=self.execution_profile == "ro",
                before_contents=baseline_contents,
                after_contents=after_contents,
            )
            changed_paths = self._display_status_paths(changed_path_keys)
            allowed_paths = self._display_status_paths(allowed_path_keys)
            denied_paths = self._display_status_paths(denied_path_keys)
            call_result.diff_delta_files = changed_paths
            call_result.metadata["allowed_create_paths"] = allowed_paths
            call_result.metadata["denied_change_paths"] = denied_paths
            if denied_paths:
                self._rollback_read_only_changes(changed_path_keys, baseline, after, baseline_contents)
                parse_error = self._policy_violation_message(agent_name, denied_paths)
                call_result.parse_error = parse_error
        call_result.metadata.update(policy_metadata)
        if stored_payload and parse_error is None:
            self._remember_agent_response(role, stored_payload)

        self.state.state.call_count += 1
        idx = self._run_dir_index()
        self.call_store.write_call_files(idx, agent_name, execution_prompt, call_result)
        self.state.write()

        retry_limit = max(0, int(self.config.workflow.max_policy_violation_retries))
        if denied_paths and policy_retry_attempt < retry_limit:
            next_attempt = policy_retry_attempt + 1
            self._mark(
                stage,
                "policy-retry",
                {
                    "agent": agent_name,
                    "role": role,
                    "attempt": next_attempt,
                    "limit": retry_limit,
                    "denied_paths": denied_paths,
                },
            )
            self._progress(
                f"Progress: Retrying {role} {agent_name} after policy violation "
                f"({next_attempt}/{retry_limit}); forbidden paths: {', '.join(denied_paths[:4])}"
                f"{'…' if len(denied_paths) > 4 else ''}."
            )
            retry_prompt = self._policy_retry_prompt(
                agent_prompt,
                agent_name=agent_name,
                role=role,
                denied_paths=denied_paths,
                retry_attempt=next_attempt,
                retry_limit=retry_limit,
            )
            return await self._run_agent(
                agent_name,
                role,
                stage,
                retry_prompt,
                expected,
                track_repo_diff=track_repo_diff,
                extra_env=extra_env,
                policy_retry_attempt=next_attempt,
            )

        return call_result, parse_error

    @staticmethod
    def _split_status_path(path: str) -> Tuple[str, Optional[str]]:
        if _STATUS_RENAME_SEPARATOR not in path:
            return path, None
        before, after = path.split(_STATUS_RENAME_SEPARATOR, 1)
        return before, after

    def _remove_created_path(self, path: str) -> None:
        full_path = self.repo_root / path.rstrip("/")
        if not os.path.lexists(full_path):
            return
        if full_path.is_dir() and not full_path.is_symlink():
            shutil.rmtree(full_path, ignore_errors=True)
            return
        try:
            full_path.unlink()
        except FileNotFoundError:
            pass

    def _prune_empty_created_parents(self, path: str, before_contents: Dict[str, PathContentState]) -> None:
        current = (self.repo_root / path.rstrip("/")).parent
        while current != self.repo_root and current != current.parent:
            try:
                rel_path = current.relative_to(self.repo_root).as_posix()
            except ValueError:
                return
            if rel_path in before_contents:
                return
            if not os.path.lexists(current):
                current = current.parent
                continue
            if not current.is_dir() or current.is_symlink():
                return
            try:
                current.rmdir()
            except OSError:
                return
            current = current.parent

    def _remove_created_path_and_empty_parents(
        self,
        path: str,
        before_contents: Dict[str, PathContentState],
    ) -> None:
        self._remove_created_path(path)
        self._prune_empty_created_parents(path, before_contents)

    def _ensure_parent_directory(self, path: Path) -> None:
        parent = path.parent
        if os.path.lexists(parent) and not parent.is_dir():
            self._remove_created_path(parent.relative_to(self.repo_root).as_posix())
        parent.mkdir(parents=True, exist_ok=True)

    def _restore_path_content(self, path: str, content_state: PathContentState) -> None:
        kind, content, mode = content_state
        full_path = self.repo_root / path
        if kind == "missing":
            self._remove_created_path(path)
            return
        if kind == "dir":
            if os.path.lexists(full_path) and (not full_path.is_dir() or full_path.is_symlink()):
                self._remove_created_path(path)
            self._ensure_parent_directory(full_path)
            full_path.mkdir(parents=True, exist_ok=True)
            if mode is not None:
                full_path.chmod(mode)
            return
        if kind == "symlink":
            if os.path.lexists(full_path):
                self._remove_created_path(path)
            self._ensure_parent_directory(full_path)
            os.symlink(content.decode("utf-8", errors="surrogateescape"), full_path)
            return
        if os.path.lexists(full_path) and full_path.is_dir() and not full_path.is_symlink():
            shutil.rmtree(full_path, ignore_errors=True)
        elif os.path.lexists(full_path) and (full_path.is_symlink() or not full_path.is_file()):
            self._remove_created_path(path)
        self._ensure_parent_directory(full_path)
        full_path.write_bytes(content)
        if mode is not None:
            full_path.chmod(mode)

    def _rollback_read_only_changes(
        self,
        changed_paths: List[str],
        before: Optional[Dict[str, str]] = None,
        after: Optional[Dict[str, str]] = None,
        before_contents: Optional[Dict[str, PathContentState]] = None,
    ) -> None:
        before = before or {}
        after = after or {}
        before_contents = before_contents or {}
        for path in changed_paths:
            old_path, new_path = self._split_status_path(path)
            after_status = after.get(path)
            before_status = before.get(path)

            if path in before_contents:
                self._restore_path_content(path, before_contents[path])
                continue

            if self._is_created_path_status(before_status, after_status):
                self._remove_created_path_and_empty_parents(new_path or old_path, before_contents)
                continue

            if new_path:
                if old_path in before_contents:
                    self._restore_path_content(old_path, before_contents[old_path])
                if new_path in before_contents:
                    self._restore_path_content(new_path, before_contents[new_path])
                else:
                    self._remove_created_path_and_empty_parents(new_path, before_contents)
                continue

            if old_path in before_contents:
                self._restore_path_content(old_path, before_contents[old_path])
                continue

            full_path = self.repo_root / old_path
            if os.path.lexists(full_path) and self._is_created_path_status(before_status, after_status):
                self._remove_created_path_and_empty_parents(old_path, before_contents)

    async def _ensure_plan(self, task: str, orchestrator: str) -> OrchestratorPlan:
        if self.run_dir.plan_path.exists():
            return OrchestratorPlan.model_validate_json(self.run_dir.plan_path.read_text(encoding="utf-8"))

        self._mark(RunStatus.PLANNING, "start-planning")
        prompt = render_template(
            "orchestrator-planning",
            self.repo_root,
            {
                "TASK": task,
                "CONTEXT": json.dumps(collect_repo_context(self.repo_root), indent=2),
                "REPO_ROOT": str(self.repo_root),
            },
        )
        result, parse_error = await self._run_agent(
            orchestrator,
            "orchestrator",
            RunStatus.PLANNING,
            prompt,
            OrchestratorPlan,
        )
        if parse_error or not result.parsed_output:
            raise RuntimeError(result.parse_error or "planner did not return structured output")

        plan = OrchestratorPlan.model_validate(result.parsed_output)
        self.run_dir.plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
        self.state.state.review_history.append("plan")
        self._mark(RunStatus.PLANNING, "plan-complete", {"tasks": [t.id for t in plan.tasks]})
        self.state.write()
        return plan

    async def _run_advisor(self, advisor: str, task: str, plan: OrchestratorPlan) -> Optional[AdvisorResponse]:
        if "advisor-done" in self.state.state.review_history:
            return None

        self._mark(RunStatus.ADVISOR_REVIEW, "start-advisor")
        prompt = render_template(
            "advisor-review",
            self.repo_root,
            {"TASK": task, "PLAN": plan.model_dump_json(indent=2)},
        )
        result, parse_error = await self._run_agent(
            advisor,
            "advisor",
            RunStatus.ADVISOR_REVIEW,
            prompt,
            AdvisorResponse,
        )
        if parse_error or not result.parsed_output:
            raise RuntimeError(parse_error or result.parse_error)
        advisor_response = AdvisorResponse.model_validate(result.parsed_output or {})

        if advisor_response.verdict == "block":
            self.state.state.review_history.append("advisor-done")
            self.state.write()
            self._mark(RunStatus.FAILED, "advisor-blocked", {"agent": advisor})
            self.state.state.last_error = "advisor-blocked"
            self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
            self.state.write()
            return advisor_response

        if advisor_response.verdict == "revise":
            self._mark(RunStatus.PLAN_REVISION, "advisor-revision-requested")
            revision_prompt = render_template(
                "orchestrator-revision",
                self.repo_root,
                {
                    "TASK": task,
                    "PLAN": plan.model_dump_json(indent=2),
                    "ADVICE": advisor_response.model_dump_json(indent=2),
                },
            )
            orchestrator = self._agent_for_role("orchestrator")
            result2, parse_error2 = await self._run_agent(
                orchestrator,
                "orchestrator",
                RunStatus.PLAN_REVISION,
                revision_prompt,
                OrchestratorPlan,
                extra_env={"PLAN_REVISION_ENABLED": "1"},
            )
            if parse_error2 or not result2.parsed_output:
                raise RuntimeError(parse_error2 or "advisor revision malformed")
            new_plan = OrchestratorPlan.model_validate(result2.parsed_output or {})
            self.run_dir.plan_path.write_text(new_plan.model_dump_json(indent=2), encoding="utf-8")
            self._log(f"Revised plan to {len(new_plan.tasks)} task(s)")

        self.state.state.review_history.append("advisor-done")
        self.state.write()
        self._mark(RunStatus.ADVISOR_REVIEW, "advisor-complete")
        self.state.state.advisory_blocks += 1
        return advisor_response

    async def _implement_plan(self, implementer: str, task: str, plan: OrchestratorPlan) -> bool:
        if "implementer-complete" in self.state.state.review_history:
            return True
        self._mark(RunStatus.IMPLEMENTING, "start-implementation")
        already = set(self.state.state.implemented_task_ids)
        for task_entry in plan.tasks:
            if task_entry.id in already:
                continue

            prompt = render_template(
                "implementer-task",
                self.repo_root,
                {
                    "TASK": task,
                    "PLAN_TASK_ID": task_entry.id,
                    "PLAN_TASK_TITLE": task_entry.title,
                    "CONTEXT": "\n".join(task_entry.verification or []),
                    "ADVISOR_NOTES": ", ".join(task_entry.recommended_role and [task_entry.recommended_role] or []),
                    "FILE_SCOPE": ", ".join(task_entry.dependencies or []),
                    "PLAN_TASK": task_entry.model_dump_json(indent=2),
                    "CONSTRAINTS": "\n".join(task_entry.verification),
                },
            )
            result, parse_error = await self._run_agent(
                implementer,
                "implementer",
                RunStatus.IMPLEMENTING,
                prompt,
                ImplementerResponse,
            )
            if result.diff_delta_files:
                # this is allowed for write-capable implementers
                pass
            if parse_error or not result.parsed_output:
                raise RuntimeError(parse_error or result.parse_error)
            response = ImplementerResponse.model_validate(result.parsed_output or {})
            if not self._validate_implementer_completed(
                response,
                "implementer-incomplete",
                {"task": task_entry.id},
            ):
                return False
            self.state.state.implemented_task_ids.append(task_entry.id)
            self.state.write()

        self.state.state.review_history.append("implementer-complete")
        self._mark(RunStatus.IMPLEMENTING, "implementing-complete", {"count": len(self.state.state.implemented_task_ids)})
        return True

    def _validate_implementer_completed(
        self,
        response: ImplementerResponse,
        event: str,
        payload: Optional[Dict[str, object]] = None,
    ) -> bool:
        if response.status.strip().lower() == "completed":
            return True

        remaining = "; ".join(response.remaining_issues)
        detail = remaining or response.summary or response.status
        self.state.state.last_error = f"implementer incomplete: {detail}"
        event_payload = dict(payload or {})
        event_payload.update(
            {
                "status": response.status,
                "remaining_issues": response.remaining_issues,
            }
        )
        self._mark(RunStatus.FAILED, event, event_payload)
        return False

    async def _review_and_fix(self, reviewer: str, implementer: str, task: str, max_rounds: int) -> bool:
        if "review-complete" in self.state.state.review_history:
            return True

        round_idx = self.state.state.review_round
        if max_rounds <= 0:
            self.state.state.review_history.append("review-skipped")
            self.state.write()
            self._mark(RunStatus.FINALIZING, "review-skipped", {"max_rounds": max_rounds})
            return True

        if round_idx >= max_rounds:
            return False

        while round_idx < max_rounds:
            round_idx += 1
            self.state.state.review_round = round_idx
            self._mark(RunStatus.REVIEWING, "reviewing", {"round": round_idx})
            prompt = render_template(
                "reviewer-review",
                self.repo_root,
                {
                    "TASK": task,
                    "PLAN_PATH": str(self.run_dir.plan_path),
                    "RUN_DIR": str(self.run_dir.path),
                },
            )
            result, parse_error = await self._run_agent(
                reviewer,
                "reviewer",
                RunStatus.REVIEWING,
                prompt,
                ReviewerResponse,
            )
            if parse_error or not result.parsed_output:
                raise RuntimeError(parse_error or result.parse_error)
            review = ReviewerResponse.model_validate(result.parsed_output or {})
            if review.verdict == "approve":
                self.state.state.review_history.append(f"review-approve-{round_idx}")
                self.state.state.last_error = None
                self.state.write()
                self._mark(RunStatus.REVIEWING, "review-approved", {"round": round_idx})
                self.state.state.review_history.append("review-complete")
                return True

            if review.required_fixes and round_idx < max_rounds:
                self._mark(RunStatus.FIXING, "start-fix", {"round": round_idx})
                fix_prompt = render_template(
                    "implementer-fix-request",
                    self.repo_root,
                    {
                        "TASK": task,
                        "REVIEW": review.model_dump_json(indent=2),
                        "REQUIRED_FIXES": "\n".join(review.required_fixes),
                    },
                )
                fix_result, parse_error2 = await self._run_agent(
                    implementer,
                    "implementer",
                    RunStatus.FIXING,
                    fix_prompt,
                    ImplementerResponse,
                )
                if parse_error2 or not fix_result.parsed_output:
                    raise RuntimeError(parse_error2 or fix_result.parse_error)
                fix_response = ImplementerResponse.model_validate(fix_result.parsed_output or {})
                if not self._validate_implementer_completed(
                    fix_response,
                    "fix-incomplete",
                    {"round": round_idx},
                ):
                    return False
                self.state.state.review_history.append(f"review-fix-round-{round_idx}")
                self.state.write()
                self._mark(RunStatus.FIXING, "fixes-complete", {"round": round_idx})
                continue

            self.state.state.review_history.append(f"review-reject-{round_idx}")
            self.state.state.last_error = "reviewer rejected"
            self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
            self.state.write()
            self._mark(RunStatus.FAILED, "review-rejected")
            return False

        self.state.state.last_error = "max review rounds exceeded"
        self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
        self.state.write()
        self._mark(RunStatus.FAILED, "review-round-limit", {"max_rounds": max_rounds})
        return False

    async def _finalize(self) -> Dict[str, object]:
        status = RunStatus.COMPLETED if not self.state.state.last_error else RunStatus.FAILED
        self.state.state.completed = status == RunStatus.COMPLETED
        self.state.state.mark(status, datetime.now(timezone.utc))
        self.state.write()

        summary = {
            "status": status.value,
            "result": self._latest_implementation_summary or self._latest_response_summary,
            "implemented": self.state.state.implemented_task_ids,
            "review_round": self.state.state.review_round,
            "run_id": self.state.state.run_id,
            "errors": self.state.state.last_error,
            "event_log": str(self.run_dir.events_path),
        }
        summary_text = render_template(
            "final-summary",
            self.repo_root,
            {
                "TASK": self.state.state.task,
                "SUMMARY": json.dumps(summary, indent=2),
            },
        )
        self.run_dir.final_summary.write_text(summary_text, encoding="utf-8")
        self._mark(status, "finalized")
        return summary

    async def run(
        self,
        task: str,
        no_advisor: bool = False,
        max_review_rounds: Optional[int] = None,
        dry_run: bool = False,
        global_mode: str = "auto",
        role_modes: Optional[Dict[str, str]] = None,
    ) -> Dict[str, object]:
        if self.state.state.completed and self.state.state.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            return {"status": self.state.state.status.value, "message": "run already completed"}

        max_rounds = self.config.workflow.max_review_rounds if max_review_rounds is None else max_review_rounds
        review_enabled = max_rounds > 0
        resolved_modes = self._resolve_modes(task, global_mode, role_modes)
        orchestrator = self._agent_for_role("orchestrator", mode=resolved_modes["orchestrator"])
        implementer = self._agent_for_role("implementer", mode=resolved_modes["implementer"])
        reviewer = self._agent_for_role("reviewer", mode=resolved_modes["reviewer"]) if review_enabled else ""
        advisor = (
            None
            if no_advisor
            else (
                self._agent_for_role("advisor", mode=resolved_modes["advisor"])
                if self.config.workflow.advisors
                else None
            )
        )

        selected_agents = {
            "orchestrator": orchestrator,
            "advisor": advisor or "",
            "implementer": implementer,
            "reviewer": reviewer,
        }
        selected_preview = ", ".join(
            f"{role}={agent_name or 'none'}"
            for role, agent_name in selected_agents.items()
        )
        mode_preview = ", ".join(
            f"{role}={self._agent_mode(agent_name) or resolved_modes[role]}"
            for role, agent_name in selected_agents.items()
            if agent_name
        )
        self._progress(
            f"Progress: Selected agents — {selected_preview}; modes: {mode_preview}; profile={self.execution_profile}."
        )

        repository_git = self._is_git_worktree()
        git_required_blocked = self.config.repository.require_git and not repository_git
        dirty_status = git_status_short(self.repo_root) if repository_git else ""
        repository_dirty = bool(dirty_status.strip())
        clean_start_blocked = repository_dirty and self.config.repository.require_clean_start

        if dry_run:
            plan_exists = self.run_dir.plan_path.exists()
            dry_run_commands = {}
            dry_run_permissions = {}
            for role_name, name in selected_agents.items():
                if not name:
                    continue
                agent = self.config.agents[name]
                adapter = self.adapters[name]
                try:
                    cmd = adapter.build_command("<prompt omitted>", self.repo_root)
                except Exception:
                    cmd = agent.command or []
                cmd = self._prepare_agent_command(agent, list(cmd))
                dry_run_commands[role_name] = cmd
                dry_run_permissions[role_name] = self._effective_permission(agent)

            return {
                "dry_run": True,
                "selected_agents": selected_agents,
                "selected_modes": resolved_modes,
                "execution_profile": self.execution_profile,
                "stages": [
                    RunStatus.PLANNING.value,
                    RunStatus.ADVISOR_REVIEW.value if advisor else None,
                    RunStatus.IMPLEMENTING.value,
                    RunStatus.REVIEWING.value if review_enabled else None,
                    RunStatus.FINALIZING.value,
                ],
                "commands": dry_run_commands,
                "permissions": dry_run_permissions,
                "denied_commands": list(DENIED_COMMANDS),
                "denied_git_subcommands": list(DENIED_GIT_SUBCOMMANDS),
                "state_dir": str(self.run_dir.path),
                "plan_exists": plan_exists,
                "repo_status": dirty_status,
                "repository_git": repository_git,
                "require_git": self.config.repository.require_git,
                "repository_dirty": repository_dirty,
                "require_clean_start": self.config.repository.require_clean_start,
                "would_fail": git_required_blocked or clean_start_blocked,
                "failure_reason": (
                    "repository is not a git worktree"
                    if git_required_blocked
                    else "repository has uncommitted changes"
                    if clean_start_blocked
                    else None
                ),
            }

        if git_required_blocked:
            self._mark(RunStatus.RECEIVED, "git-required-missing", {"repo_root": str(self.repo_root)})
            self.state.state.last_error = "repository is not a git worktree"
            self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
            self.state.write()
            return {"status": RunStatus.FAILED.value, "error": self.state.state.last_error}

        if repository_dirty:
            self._mark(RunStatus.RECEIVED, "dirty-repo", {"status": dirty_status})
            if self.config.repository.require_clean_start:
                self.state.state.last_error = "repository has uncommitted changes"
                self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
                self.state.write()
                return {"status": RunStatus.FAILED.value, "error": self.state.state.last_error}
            self._log("Warning: repository has uncommitted changes. Proceeding anyway.")

        self.state.state.selected_agents = selected_agents
        self.state.state.task = task
        self.state.state.execution_profile = self.execution_profile
        self.run_dir.task_path.write_text(task, encoding="utf-8")
        self.state.write()

        if not self.config_snapshot_written:
            from .config import FileResourceMixin

            FileResourceMixin.dump_yaml(self.run_dir.config_snapshot, self.config.model_dump(mode="json"))
            self.config_snapshot_written = True

        plan = await self._ensure_plan(task, orchestrator)
        if advisor and self.config.workflow.advisor_policy == "before_implementation":
            if "advisor-complete" not in self.state.state.review_history:
                advisor_response = await self._run_advisor(advisor, task, plan)
                if advisor_response and advisor_response.verdict == "block":
                    return await self._finalize()
                if advisor_response and advisor_response.verdict == "revise":
                    plan = OrchestratorPlan.model_validate_json(self.run_dir.plan_path.read_text(encoding="utf-8"))

        if "implementer-complete" not in self.state.state.review_history:
            if not await self._implement_plan(implementer, task, plan):
                return await self._finalize()

        approved = await self._review_and_fix(reviewer, implementer, task, max_rounds)

        if not approved and not self.state.state.last_error:
            # reviewer required fixes but no approval
            self.state.state.last_error = "reviewer did not approve"

        summary = await self._finalize()
        return summary
