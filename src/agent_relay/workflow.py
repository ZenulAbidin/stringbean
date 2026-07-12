from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import os
import shutil
import subprocess
from typing import Dict, List, Optional, Tuple, Type

from rich.console import Console

from .connectors import Adapter, AdapterCapabilities, ClaudeConnector, CodexConnector, GenericConnector, GrokConnector
from .config import Config
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
    apply_codex_execution_profile,
    install_command_policy_wrappers,
    normalize_execution_profile,
    policy_prompt,
)
from .runner import RunnerConfig, RunnerOutput, run_subprocess
from .state import CallStore, RunDirectory, RunEventStore, RunState, now_iso
from .streaming import LiveStreamFormatter
from .templates import render_template
from .utils import file_status_set, git_status_short
from .utils import sanitize_environment
from pydantic import BaseModel


ADAPTERS = {
    "codex": CodexConnector,
    "claude": ClaudeConnector,
    "grok": GrokConnector,
    "generic": GenericConnector,
}


MODE_CHOICES = {"auto", "low", "medium", "high"}


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
        execution_profile: str = "ro",
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.state = run_state
        self.console = console or Console()
        self.quiet = quiet
        self.execution_profile = normalize_execution_profile(execution_profile)
        self.adapters = build_adapters(config)
        self.events = RunEventStore(self.run_dir.events_path)
        self.call_store = CallStore(self.run_dir.calls_dir)
        repo_root = self.run_dir.path
        for _ in range(3):
            repo_root = repo_root.parent
        self.repo_root = repo_root
        self.call_counter = 0
        if self.run_dir.calls_dir.exists():
            existing = [p for p in self.run_dir.calls_dir.iterdir() if p.is_dir()]
            self.call_counter = len(existing)
        self.config_snapshot_written = False
        self._agent_stream_open_line = False
        self._agent_stream_formatter: Optional[LiveStreamFormatter] = None
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
        print(message, flush=True)

    def _write_agent_stream_line(self, line: str) -> None:
        print(line, flush=True)
        self._agent_stream_open_line = False

    def _ensure_agent_stream_formatter(self) -> LiveStreamFormatter:
        if self._agent_stream_formatter is None:
            self._agent_stream_formatter = LiveStreamFormatter(self._write_agent_stream_line)
        return self._agent_stream_formatter

    def _flush_agent_stream(self) -> None:
        if self._agent_stream_formatter is not None:
            self._agent_stream_formatter.flush()

    def _stream_agent_chunk(self, chunk: str) -> None:
        if self.quiet or not chunk:
            return
        self._ensure_agent_stream_formatter().feed(chunk)

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
        return f"{prefix} in {agent_name}: modified files {', '.join(changed_files)}"

    def _prepare_agent_command(self, agent: AgentConfig, command: List[str]) -> List[str]:
        if agent.adapter.lower() == "codex" or (command and Path(command[0]).name == "codex"):
            return apply_codex_execution_profile(command, self.execution_profile)
        return command

    def _apply_subagent_policy_env(self, env_overrides: Dict[str, str]) -> Dict[str, str]:
        out = dict(env_overrides)
        existing_path = out.get("PATH") or os.environ.get("PATH", "")
        out["PATH"] = f"{self.policy_bin_dir}{os.pathsep}{existing_path}" if existing_path else str(self.policy_bin_dir)
        out["STRINGBEAN_EXECUTION_PROFILE"] = self.execution_profile
        out["STRINGBEAN_DENIED_COMMANDS"] = ",".join(DENIED_COMMANDS)
        out["STRINGBEAN_DENIED_GIT_SUBCOMMANDS"] = ",".join(DENIED_GIT_SUBCOMMANDS)
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
    ) -> Tuple[AgentCallResult, Optional[str]]:
        if self.state.state.call_count >= self.state.state.total_calls_limit:
            raise RuntimeError("agent call limit reached")

        agent = self.config.agents[agent_name]
        adapter = self.adapters[agent_name]
        original_agent_name = agent_name

        if agent.prompt_transport not in {"stdin", "argv", "file"}:
            raise RuntimeError(f"unsupported prompt transport {agent.prompt_transport}")
        if not adapter.supports_prompt_transport(agent.prompt_transport):
            raise RuntimeError(f"agent {agent_name} does not support prompt transport {agent.prompt_transport}")

        if track_repo_diff is None:
            track_repo_diff = self._should_track_repo_diff(agent)
        baseline = None
        if track_repo_diff:
            baseline = file_status_set(git_status_short(self.repo_root))

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

        prompt = policy_prompt(self.execution_profile, self._effective_permission(agent)) + "\n\n" + prompt

        cfg_prompt = None
        if agent.prompt_transport == "argv":
            command = command + [prompt]
        elif agent.prompt_transport == "file":
            tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".md")
            tmp.write(prompt)
            tmp.flush()
            tmp.close()
            command = command + [tmp.name]
        else:
            cfg_prompt = prompt

        env_overrides = dict(agent.environment_overrides)
        if extra_env:
            env_overrides.update(extra_env)
        env_overrides = self._apply_subagent_policy_env(env_overrides)
        if self.config.output.redact_environment_values:
            env = sanitize_environment(env_overrides)
        else:
            env = dict(os.environ)
            env.update(env_overrides)

        stream_agent_output = bool(self.config.output.stream_agent_output and not self.quiet)
        if stream_agent_output:
            self._agent_stream_formatter = LiveStreamFormatter(self._write_agent_stream_line)
            self._log(f"[stringbean] starting {role} agent: {agent_name}")
        callback = self._stream_agent_chunk if stream_agent_output else None
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
                )
            )
        except TimeoutError as exc:
            if stream_agent_output:
                self._flush_agent_stream()
                self._agent_stream_formatter = None
            raise RuntimeError(f"agent {agent_name} timed out") from exc
        except Exception as exc:
            if stream_agent_output:
                self._flush_agent_stream()
                self._agent_stream_formatter = None
            raise RuntimeError(f"agent {agent_name} execution failed: {exc}") from exc
        if stream_agent_output:
            self._flush_agent_stream()
            self._log(f"[stringbean] finished {role} agent: {agent_name} (exit {result.exit_code})")
            self._agent_stream_formatter = None

        parse_error: Optional[str] = None
        model_payload = None
        raw_payload = None

        if result.exit_code not in {0, None}:
            parse_error = f"agent exited with status {result.exit_code}"
        else:
            parsed, raw_payload, parse_error = parse_structured_output(result.raw_stdout, expected)
            model_payload = parsed.model_dump(mode="json") if parsed is not None else None

        call_result = AgentCallResult(
            agent_name=agent_name,
            role=role,
            stage=stage,
            command=result.command,
            exit_code=result.exit_code,
            duration_seconds=result.duration_seconds,
            start_time=result.start_time,
            end_time=result.end_time,
            raw_stdout=result.raw_stdout,
            raw_stderr=result.raw_stderr,
            parsed_output=model_payload,
            parse_error=parse_error,
            diff_delta_files=None,
            metadata={},
        )

        added: List[str] | None = None
        if track_repo_diff and baseline is not None:
            after = file_status_set(git_status_short(self.repo_root))
            added = sorted(after - baseline)
            call_result.diff_delta_files = added
            if added:
                self._rollback_read_only_changes(added)
                parse_error = self._policy_violation_message(agent_name, added)
                call_result.parse_error = parse_error
        call_result.metadata.update(
            {
                "execution_profile": self.execution_profile,
                "effective_permission": self._effective_permission(agent),
                "denied_commands": list(DENIED_COMMANDS),
                "denied_git_subcommands": list(DENIED_GIT_SUBCOMMANDS),
            }
        )
        if model_payload and parse_error is None:
            self._remember_agent_response(role, model_payload)

        self.state.state.call_count += 1
        idx = self._run_dir_index()
        self.call_store.write_call_files(idx, agent_name, prompt, call_result)
        self.state.write()

        return call_result, parse_error

    def _rollback_read_only_changes(self, changed_paths: List[str]) -> None:
        for path in changed_paths:
            full_path = self.repo_root / path
            try:
                tracked = (
                    subprocess.run(
                        ["git", "ls-files", "--error-unmatch", path],
                        cwd=self.repo_root,
                        check=False,
                        capture_output=True,
                        text=True,
                    ).returncode
                    == 0
                )
            except FileNotFoundError:
                tracked = False

            if tracked:
                subprocess.run(
                    ["git", "checkout", "--", path],
                    cwd=self.repo_root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                continue
            if full_path.exists():
                if full_path.is_dir():
                    continue
                try:
                    full_path.unlink()
                except FileNotFoundError:
                    pass

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
        self.state.state.review_history.append("advisor-done")
        self.state.write()

        if advisor_response.verdict == "block":
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

        self._mark(RunStatus.ADVISOR_REVIEW, "advisor-complete")
        self.state.state.advisory_blocks += 1
        return advisor_response

    async def _implement_plan(self, implementer: str, task: str, plan: OrchestratorPlan) -> None:
        if "implementer-complete" in self.state.state.review_history:
            return
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
            if response.status != "completed" and response.remaining_issues:
                self._mark(RunStatus.FAILED, "implementer-incomplete", {"task": task_entry.id})
            self.state.state.implemented_task_ids.append(task_entry.id)
            self.state.write()

        self.state.state.review_history.append("implementer-complete")
        self._mark(RunStatus.IMPLEMENTING, "implementing-complete", {"count": len(self.state.state.implemented_task_ids)})

    async def _review_and_fix(self, reviewer: str, implementer: str, task: str, max_rounds: int) -> bool:
        if "review-complete" in self.state.state.review_history:
            return True

        round_idx = self.state.state.review_round
        if max_rounds <= 0:
            self.state.state.review_history.append("review-skipped")
            self.state.write()
            self._mark(RunStatus.REVIEWING, "review-skipped", {"max_rounds": max_rounds})
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
                _, parse_error2 = await self._run_agent(
                    implementer,
                    "implementer",
                    RunStatus.FIXING,
                    fix_prompt,
                    ImplementerResponse,
                )
                if parse_error2:
                    raise RuntimeError(parse_error2)
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

        dirty_status = git_status_short(self.repo_root)
        if dirty_status.strip():
            self._mark(RunStatus.RECEIVED, "dirty-repo", {"status": dirty_status})
            if self.config.repository.require_clean_start:
                self.state.state.last_error = "repository has uncommitted changes"
                self.state.state.mark(RunStatus.FAILED, datetime.now(timezone.utc))
                self.state.write()
                return {"status": RunStatus.FAILED.value, "error": self.state.state.last_error}
            self._log("Warning: repository has uncommitted changes. Proceeding anyway.")

        max_rounds = self.config.workflow.max_review_rounds if max_review_rounds is None else max_review_rounds
        resolved_modes = self._resolve_modes(task, global_mode, role_modes)
        orchestrator = self._agent_for_role("orchestrator", mode=resolved_modes["orchestrator"])
        implementer = self._agent_for_role("implementer", mode=resolved_modes["implementer"])
        reviewer = self._agent_for_role("reviewer", mode=resolved_modes["reviewer"])
        advisor = (
            None
            if no_advisor
            else (
                self._agent_for_role("advisor", mode=resolved_modes["advisor"])
                if self.config.workflow.advisors
                else None
            )
        )

        self.state.state.selected_agents = {
            "orchestrator": orchestrator,
            "advisor": advisor or "",
            "implementer": implementer,
            "reviewer": reviewer,
        }

        self.state.state.task = task
        self.state.state.execution_profile = self.execution_profile
        self.run_dir.task_path.write_text(task, encoding="utf-8")
        self.state.write()

        if dry_run:
            plan_exists = self.run_dir.plan_path.exists()
            dry_run_commands = {}
            dry_run_permissions = {}
            for role_name, name in self.state.state.selected_agents.items():
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
                "selected_agents": self.state.state.selected_agents,
                "selected_modes": resolved_modes,
                "execution_profile": self.execution_profile,
                "stages": [
                    RunStatus.PLANNING.value,
                    RunStatus.ADVISOR_REVIEW.value if advisor else None,
                    RunStatus.IMPLEMENTING.value,
                    RunStatus.REVIEWING.value,
                    RunStatus.FINALIZING.value,
                ],
                "commands": dry_run_commands,
                "permissions": dry_run_permissions,
                "denied_commands": list(DENIED_COMMANDS),
                "denied_git_subcommands": list(DENIED_GIT_SUBCOMMANDS),
                "state_dir": str(self.run_dir.path),
                "plan_exists": plan_exists,
            }

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

        if "implementer-complete" not in self.state.state.review_history:
            await self._implement_plan(implementer, task, plan)

        approved = await self._review_and_fix(reviewer, implementer, task, max_rounds)

        if not approved and not self.state.state.last_error:
            # reviewer required fixes but no approval
            self.state.state.last_error = "reviewer did not approve"

        summary = await self._finalize()
        return summary
