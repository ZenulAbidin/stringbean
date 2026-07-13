from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .config import (
    AgentConfig,
    Config,
    PROJECT_DIR_NAME,
    PROJECT_NAME,
    active_project_dir,
    OutputConfig,
    RepositoryConfig,
    RUN_DIR_NAME,
    WorkflowConfig,
    config_path,
    load_config,
    save_config,
)
from .state import RunDirectory, RunState, create_new_run, list_runs
from .templates import available_template_names
from .utils import git_status_short, stable_id
from .workflow import WorkflowEngine
from .policy import normalize_execution_profile

app = typer.Typer(help=f"{PROJECT_NAME} local orchestrator CLI.")
console = Console()
CLI_CAPABILITIES_FILE = "cli-capabilities.json"
BUILTIN_EXECUTABLES = ("codex", "claude", "grok")
MODE_CHOICES = {"auto", "high", "medium", "low"}


def _codex_command(model: str, reasoning_effort: str) -> list[str]:
    return [
        "codex",
        "--ask-for-approval",
        "never",
        "--sandbox",
        "workspace-write",
        "exec",
        "-m",
        model,
        "-c",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]


def _capabilities_path(root: Path) -> Path:
    return _agent_relay_root(root) / CLI_CAPABILITIES_FILE


def _project_root() -> Path:
    return Path.cwd().resolve()


def _agent_relay_root(root: Path) -> Path:
    return active_project_dir(root)


def _template_dir(root: Path) -> Path:
    return _agent_relay_root(root) / "templates"


def _coerce_mode(value: Optional[str], option_name: str) -> Optional[str]:
    if value is None:
        return None
    value = value.strip().lower()
    if value not in MODE_CHOICES:
        raise typer.BadParameter(f"{option_name} must be one of: auto, low, medium, high")
    return value


def _default_config() -> Config:
    return Config(
        agents={
            "sol": AgentConfig(
                name="sol",
                adapter="codex",
                model="gpt-5.6-sol",
                role="orchestrator",
                permissions="read_write",
                command=None,
                prompt_transport="stdin",
            ),
            "fable": AgentConfig(
                name="fable",
                adapter="claude",
                model="fable-5",
                role="advisor",
                permissions="read_only",
                command=None,
                prompt_transport="stdin",
            ),
            "grok": AgentConfig(
                name="grok",
                adapter="grok",
                model="grok-4.5",
                role="implementer",
                permissions="read_write",
                command=None,
                prompt_transport="stdin",
            ),
            "sol-review": AgentConfig(
                name="sol-review",
                adapter="codex",
                model="gpt-5.6-sol",
                role="reviewer",
                permissions="read_only",
                command=None,
                prompt_transport="stdin",
            ),
        },
        workflow=WorkflowConfig(
            orchestrator="sol",
            advisors=["fable"],
            implementers=["grok"],
            reviewers=["sol-review"],
            advisor_policy="before_implementation",
        ),
        repository=RepositoryConfig(),
        output=OutputConfig(),
    )


def _preset_config(preset: str) -> Config:
    preset = preset.upper()
    base = _default_config()
    if preset == "A":
        return base
    if preset == "B":
        return Config(
            agents={
                "sol": AgentConfig(
                    name="sol",
                    adapter="claude",
                    model="fable-5",
                    role="orchestrator",
                    permissions="read_write",
                    command=None,
                    prompt_transport="stdin",
                ),
                "fable": AgentConfig(
                    name="fable",
                    adapter="codex",
                    model="gpt-5.6-sol",
                    role="advisor",
                    permissions="read_only",
                    command=None,
                    prompt_transport="stdin",
                ),
                "grok": AgentConfig(
                    name="grok",
                    adapter="grok",
                    model="grok-4.5",
                    role="implementer",
                    permissions="read_write",
                    command=None,
                    prompt_transport="stdin",
                ),
                "reviewer": AgentConfig(
                    name="reviewer",
                    adapter="codex",
                    model="gpt-5.6-sol",
                    role="reviewer",
                    permissions="read_only",
                    command=None,
                    prompt_transport="stdin",
                ),
            },
            workflow=WorkflowConfig(
                orchestrator="sol",
                advisors=["fable"],
                implementers=["grok"],
                reviewers=["reviewer"],
                advisor_policy="before_implementation",
            ),
            repository=RepositoryConfig(),
            output=OutputConfig(),
        )
    if preset == "C":
        return Config(
            agents={
                "solo": AgentConfig(
                    name="solo",
                    adapter="generic",
                    model="local-fallback",
                    role="orchestrator",
                    permissions="read_write",
                    command=["cat"],
                    prompt_transport="stdin",
                ),
                "solo-advisor": AgentConfig(
                    name="solo-advisor",
                    adapter="generic",
                    model="local-fallback",
                    role="advisor",
                    permissions="read_only",
                    command=["cat"],
                    prompt_transport="stdin",
                ),
                "solo-impl": AgentConfig(
                    name="solo-impl",
                    adapter="generic",
                    model="local-fallback",
                    role="implementer",
                    permissions="read_write",
                    command=["cat"],
                    prompt_transport="stdin",
                ),
                "solo-review": AgentConfig(
                    name="solo-review",
                    adapter="generic",
                    model="local-fallback",
                    role="reviewer",
                    permissions="read_only",
                    command=["cat"],
                    prompt_transport="stdin",
                ),
            },
            workflow=WorkflowConfig(
                orchestrator="solo",
                advisors=["solo-advisor"],
                implementers=["solo-impl"],
                reviewers=["solo-review"],
                advisor_policy="before_implementation",
            ),
            repository=RepositoryConfig(),
            output=OutputConfig(),
        )
    if preset == "D":
        return Config(
            agents={
                "gpt56-high": AgentConfig(
                    name="gpt56-high",
                    adapter="codex",
                    model="gpt-5.6",
                    role="orchestrator",
                    permissions="read_write",
                    command=_codex_command("gpt-5.6", "high"),
                    mode="high",
                    prompt_transport="stdin",
                ),
                "gpt56-medium": AgentConfig(
                    name="gpt56-medium",
                    adapter="codex",
                    model="gpt-5.6",
                    role="advisor",
                    permissions="read_only",
                    command=_codex_command("gpt-5.6", "medium"),
                    mode="medium",
                    prompt_transport="stdin",
                ),
                "gpt56-low": AgentConfig(
                    name="gpt56-low",
                    adapter="codex",
                    model="gpt-5.6",
                    role="reviewer",
                    permissions="read_only",
                    command=_codex_command("gpt-5.6", "low"),
                    mode="low",
                    prompt_transport="stdin",
                ),
                "gpt55-high": AgentConfig(
                    name="gpt55-high",
                    adapter="codex",
                    model="gpt-5.5",
                    role="implementer",
                    permissions="read_write",
                    command=_codex_command("gpt-5.5", "high"),
                    mode="high",
                    prompt_transport="stdin",
                ),
                "gpt55-medium": AgentConfig(
                    name="gpt55-medium",
                    adapter="codex",
                    model="gpt-5.5",
                    role="advisor",
                    permissions="read_only",
                    command=_codex_command("gpt-5.5", "medium"),
                    mode="medium",
                    prompt_transport="stdin",
                ),
                "gpt55-low": AgentConfig(
                    name="gpt55-low",
                    adapter="codex",
                    model="gpt-5.5",
                    role="advisor",
                    permissions="read_only",
                    command=_codex_command("gpt-5.5", "low"),
                    mode="low",
                    prompt_transport="stdin",
                ),
                "claude-opus-4-8": AgentConfig(
                    name="claude-opus-4-8",
                    adapter="claude",
                    model="opus-4.8",
                    role="reviewer",
                    permissions="read_only",
                    command=["claude", "--model", "opus-4.8"],
                    mode="high",
                    prompt_transport="stdin",
                ),
                "claude-fable-5": AgentConfig(
                    name="claude-fable-5",
                    adapter="claude",
                    model="fable-5",
                    role="advisor",
                    permissions="read_only",
                    command=["claude", "--model", "fable-5"],
                    mode="medium",
                    prompt_transport="stdin",
                ),
                "claude-sonnet-5": AgentConfig(
                    name="claude-sonnet-5",
                    adapter="claude",
                    model="sonnet-5",
                    role="reviewer",
                    permissions="read_only",
                    command=["claude", "--model", "sonnet-5"],
                    mode="low",
                    prompt_transport="stdin",
                ),
                "grok-build": AgentConfig(
                    name="grok-build",
                    adapter="grok",
                    model="grok-4.5",
                    role="implementer",
                    permissions="read_write",
                    command=["grok", "--model", "grok-4.5", "--reasoning-effort", "high"],
                    mode="high",
                    prompt_transport="stdin",
                ),
                "grok-review": AgentConfig(
                    name="grok-review",
                    adapter="grok",
                    model="grok-4.5",
                    role="reviewer",
                    permissions="read_only",
                    command=["grok", "--model", "grok-4.5", "--reasoning-effort", "low"],
                    mode="low",
                    prompt_transport="stdin",
                ),
            },
            workflow=WorkflowConfig(
                orchestrator="gpt55-high",
                advisors=["gpt55-medium", "claude-fable-5", "gpt56-medium"],
                implementers=["gpt55-high", "grok-build"],
                reviewers=["gpt55-low", "claude-opus-4-8", "claude-sonnet-5", "grok-review", "gpt56-low"],
                advisor_policy="before_implementation",
                max_review_rounds=2,
                max_total_agent_calls=24,
            ),
            repository=RepositoryConfig(),
            output=OutputConfig(),
        )
    return base


def _copy_builtin_templates(root: Path) -> None:
    src = Path(__file__).resolve().parent / "templates"
    target = _template_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    for template in src.glob("*.md"):
        (target / template.name).write_text(template.read_text(encoding="utf-8"), encoding="utf-8")


def _probe_cli_tool(executable: str) -> tuple[bool, str]:
    if not executable:
        return False, "no executable configured"
    if shutil.which(executable) is None:
        return False, "not in PATH"

    tried = []
    for flag in ("--help", "-h", "--version"):
        tried.append(flag)
        try:
            proc = subprocess.run(
                [executable, flag],
                cwd=Path.cwd(),
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if proc.returncode == 0:
                return True, f"{executable} {flag} ok"
        except FileNotFoundError:
            return False, f"{executable} not executable"
        except Exception as exc:
            continue

    return False, f"{executable} probe failed (tested: {', '.join(tried)})"


def _provider_capability_rows(cfg: Optional[Config], root: Path):
    caps = {}
    if cfg is not None:
        for name, agent in cfg.agents.items():
            exe = (agent.command or [agent.adapter])[0]
            ok, detail = _probe_cli_tool(exe)
            caps[f"agent:{name}"] = {"executable": exe, "available": ok, "details": detail, "model": agent.model}
    for exe in BUILTIN_EXECUTABLES:
        ok, detail = _probe_cli_tool(exe)
        existing = None
        # Prefer capability for a configured instance if one exists.
        if cfg is not None:
            existing = next((a for a in cfg.agents.values() if a.adapter == exe), None)
        caps[f"{exe} (builtin)"] = {
            "executable": exe,
            "available": ok,
            "details": detail,
            "configured_agent": existing.name if existing else None,
        }
    return caps


def _check_template_availability(root: Path) -> dict[str, bool]:
    out = {}
    for name in available_template_names():
        local = _template_dir(root) / f"{name}.md"
        if local.exists():
            out[name] = True
        else:
            out[name] = (Path(__file__).resolve().parent / "templates" / f"{name}.md").exists()
    return out


@app.command()
def init(
    preset: str = typer.Option("A", "--preset", help="Preset A, B, or C"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
    create_templates: bool = typer.Option(False, "--templates", help="Create local editable templates"),
):
    """
    Create .stringbean/config.yaml.
    """
    root = _project_root()
    cfg_path = config_path(root)
    if cfg_path.exists() and not force:
        console.print(f"{cfg_path} already exists. Use --force to overwrite.")
        raise typer.Exit(code=1)

    cfg = _preset_config(preset)
    _agent_relay_root(root).mkdir(parents=True, exist_ok=True)

    save_config(cfg, cfg_path)
    capabilities = _provider_capability_rows(cfg, root)
    _capabilities_path(root).parent.mkdir(parents=True, exist_ok=True)
    _capabilities_path(root).write_text(json.dumps(capabilities, indent=2), encoding="utf-8")

    for check, detail in sorted((k, v["details"]) for k, v in capabilities.items() if k.startswith("agent:") or k.startswith(tuple(BUILTIN_EXECUTABLES))):
        console.print(f"{check}: {detail}")
    console.print(f"Created configuration: {cfg_path}")
    console.print(yaml.safe_dump(cfg.model_dump(), sort_keys=True))

    if create_templates:
        _copy_builtin_templates(root)
        console.print(f"Created templates: {_template_dir(root)}")


@app.command()
def doctor(
    config: Optional[Path] = typer.Option(None, "--config", help="Optional path override for config.yaml"),
):
    """
    Check local tooling and config validity.
    """
    root = _project_root()
    cfg_path = config or config_path(root)
    cfg: Optional[Config] = None
    problems: list[str] = []
    table = Table(title=f"{PROJECT_NAME} doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("details")

    py_ok = sys.version_info >= (3, 10)
    table.add_row("python", "ok" if py_ok else "failed", ".".join(map(str, sys.version_info[:2])))
    if not py_ok:
        problems.append("python version is below 3.10")

    git_ok = shutil.which("git") is not None
    table.add_row("git", "ok" if git_ok else "missing", "installed" if git_ok else "not in PATH")
    if not git_ok:
        problems.append("git missing")

    if not cfg_path.exists():
        table.add_row("config", "missing", str(cfg_path))
        problems.append("config file missing")
    else:
        try:
            cfg = load_config(cfg_path)
            table.add_row("config", "ok", str(cfg_path))
        except Exception as exc:
            table.add_row("config", "invalid", str(exc))
            problems.append("invalid config")

    if cfg is not None:
        repo_status = git_status_short(root)
        table.add_row("repo status", "clean" if not repo_status.strip() else "dirty", repo_status or "clean")
        if repo_status.strip() and cfg.repository.require_clean_start:
            problems.append("repository is dirty and require_clean_start=true")
        if cfg.repository.require_git and not git_ok:
            problems.append("git required by repository config")

        for role, agent in cfg.agents.items():
            exe = (agent.command or [agent.adapter])[0]
            ok, detail = _probe_cli_tool(exe)
            table.add_row(f"agent:{role}", "ok" if ok else "missing", detail)
            if not ok:
                problems.append(f"agent {role} executable unavailable")

        template_state = _check_template_availability(root)
        missing_templates = sorted(name for name, exists in template_state.items() if not exists)
        table.add_row("templates", "missing" if missing_templates else "ok", ", ".join(missing_templates) or "all present")
        if missing_templates:
            problems.append("template files missing")

        if cfg.workflow.orchestrator not in cfg.agents:
            problems.append("workflow orchestrator references unknown agent")
        for role, names in (
            ("advisor", cfg.workflow.advisors),
            ("implementer", cfg.workflow.implementers),
            ("reviewer", cfg.workflow.reviewers),
        ):
            missing = [name for name in names if name not in cfg.agents]
            if missing:
                problems.append(f"workflow {role} references unknown agent(s): {', '.join(missing)}")

        has_writer = any(a.permissions == "read_write" for a in cfg.agents.values())
        table.add_row(
            "write agents",
            "ok" if has_writer else "none",
            str(sum(1 for a in cfg.agents.values() if a.permissions == "read_write")),
        )
        if not has_writer:
            problems.append("no writable agents configured")

    run_dir = _agent_relay_root(root) / RUN_DIR_NAME
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        table.add_row("state directory", "ok", str(run_dir))
    except Exception as exc:
        table.add_row("state directory", "failed", str(exc))
        problems.append("state directory not writable")

    capabilities = _provider_capability_rows(cfg, root)
    _capabilities_path(root).write_text(json.dumps(capabilities, indent=2), encoding="utf-8")
    for name, payload in sorted(capabilities.items()):
        status = "ok" if payload["available"] else "missing"
        table.add_row(f"cli:{name}", status, payload["details"])

    console.print(table)
    if problems:
        console.print("Problems:")
        for problem in problems:
            console.print(f"- {problem}")
        raise typer.Exit(code=1)


@app.command("agents")
def agents(config: Optional[Path] = typer.Option(None, "--config")):
    """
    Print configured agents and command availability.
    """
    root = _project_root()
    cfg = load_config(config or config_path(root))
    table = Table(title="Configured agents")
    table.add_column("name")
    table.add_column("role")
    table.add_column("adapter")
    table.add_column("model")
    table.add_column("permissions")
    table.add_column("command[0]")
    table.add_column("available")
    for name, agent in cfg.agents.items():
        exe = (agent.command or [agent.adapter])[0]
        table.add_row(
            name,
            agent.role,
            agent.adapter,
            agent.model or "",
            agent.permissions,
            exe,
            "yes" if shutil.which(exe) else "no",
        )
    console.print(table)


def _run_engine(
    cfg: Config,
    task: str,
    run_id: Optional[str],
    no_advisor: bool,
    dry_run: bool,
    quiet: bool,
    max_review_rounds: Optional[int],
    mode: str,
    role_modes: Optional[dict[str, str]],
    execution_profile: str,
    codex_progress: bool = False,
    progress_interval_seconds: float = 30.0,
) -> dict:
    root = _project_root()
    selected_run_id = run_id or stable_id(PROJECT_NAME, task)
    run_dir = create_new_run(root, selected_run_id, task, cfg.workflow.max_total_agent_calls, {}, execution_profile=execution_profile)
    run_state = RunState.load(run_dir.state_path)
    engine = WorkflowEngine(
        cfg,
        run_dir,
        run_state,
        console=console,
        quiet=quiet,
        execution_profile=execution_profile,
        codex_progress=codex_progress,
        progress_interval_seconds=progress_interval_seconds,
        repo_root=root,
    )
    summary = asyncio.run(
        engine.run(
            task=task,
            no_advisor=no_advisor,
            max_review_rounds=max_review_rounds,
            dry_run=dry_run,
            global_mode=mode,
            role_modes=role_modes,
        )
    )
    return {"run_id": run_dir.run_id, "summary": summary}


def _latest_stderr(run_path: Path) -> Optional[Path]:
    calls_dir = run_path / "calls"
    if not calls_dir.exists():
        return None
    call_dirs = [path for path in calls_dir.iterdir() if path.is_dir()]
    for call_dir in sorted(call_dirs, reverse=True):
        stderr_path = call_dir / "stderr.txt"
        if stderr_path.exists() and stderr_path.stat().st_size > 0:
            return stderr_path
    return None


def _print_run_failure(run_id: str, task: str, exc: Exception) -> None:
    run_path = RunDirectory(_project_root(), run_id).path
    console.print(f"Run ID: {run_id}")
    console.print(f"Run failed: {exc}")
    console.print(f"Run location: {run_path}")
    stderr_path = _latest_stderr(run_path)
    if stderr_path:
        lines = stderr_path.read_text(encoding="utf-8", errors="replace").splitlines()
        preview = "\n".join(lines[:20])
        console.print(f"Latest stderr: {stderr_path}")
        console.print(preview)
    if task:
        console.print("Use `stringbean status {}` or `stringbean logs {}` for details.".format(run_id, run_id))


def _apply_overrides(cfg: Config, orchestrator: Optional[str], advisor: Optional[str], implementer: Optional[str], reviewer: Optional[str]) -> None:
    if orchestrator:
        cfg.workflow.orchestrator = orchestrator
    if advisor:
        cfg.workflow.advisors = [advisor]
    if implementer:
        cfg.workflow.implementers = [implementer]
    if reviewer:
        cfg.workflow.reviewers = [reviewer]


def _apply_output_flags(cfg: Config, quiet: bool, no_agent_stream: bool) -> None:
    if quiet or no_agent_stream:
        cfg.output.stream_agent_output = False


def _print_run_summary(summary: dict, *, dry_run: bool, codex_final: bool = False) -> None:
    if codex_final:
        _print_codex_final_summary(summary, dry_run=dry_run)
        return

    if dry_run:
        console.print(summary)
        console.print("Dry run mode - no agents were launched.")
        return

    status = summary.get("status", "UNKNOWN")
    result = summary.get("result")
    errors = summary.get("errors")
    implemented = summary.get("implemented") or []
    review_round = summary.get("review_round")
    event_log = summary.get("event_log")

    _print_labeled("Status", str(status))
    if result:
        _print_labeled("Result", str(result))
    if errors:
        _print_labeled("Error", str(errors))
    if implemented:
        _print_labeled("Tasks", ", ".join(str(item) for item in implemented))
    if review_round is not None:
        _print_labeled("Review rounds", str(review_round))
    if event_log:
        _print_labeled("Artifacts", str(Path(str(event_log)).parent))


def _print_labeled(label: str, value: str) -> None:
    console.print(Text.assemble((label, "bold white"), (": ", "bold white"), (value, "white")))


def _print_codex_final_summary(summary: dict, *, dry_run: bool) -> None:
    print("STRINGBEAN_RESULT_START")
    if dry_run:
        print("Status: DRY_RUN")
        selected = summary.get("selected_agents")
        if selected:
            print(f"Selected agents: {selected}")
        state_dir = summary.get("state_dir")
        if state_dir:
            print(f"Artifacts: {state_dir}")
    else:
        print(f"Status: {summary.get('status', 'UNKNOWN')}")
        result = summary.get("result")
        if result:
            print(f"Result: {result}")
        errors = summary.get("errors")
        if errors:
            print(f"Error: {errors}")
        implemented = summary.get("implemented") or []
        if implemented:
            print(f"Tasks: {', '.join(str(item) for item in implemented)}")
        review_round = summary.get("review_round")
        if review_round is not None:
            print(f"Review rounds: {review_round}")
        event_log = summary.get("event_log")
        if event_log:
            print(f"Artifacts: {Path(str(event_log)).parent}")
    print("STRINGBEAN_RESULT_END")


def _resolve_execution_profile(profile: str, ro: bool, rw: bool) -> str:
    if ro and rw:
        raise typer.BadParameter("Use only one of --ro or --rw")
    if rw:
        return "rw"
    if ro:
        return "ro"
    try:
        return normalize_execution_profile(profile)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def run(
    task: str = typer.Argument(...),
    config: Optional[Path] = typer.Option(None, "--config", help="Config path"),
    orchestrator: Optional[str] = typer.Option(None, "--orchestrator"),
    advisor: Optional[str] = typer.Option(None, "--advisor"),
    implementer: Optional[str] = typer.Option(None, "--implementer"),
    reviewer: Optional[str] = typer.Option(None, "--reviewer"),
    mode: str = typer.Option("auto", "--mode", help="Selection mode for role matching: auto, high, medium, low"),
    orchestrator_mode: Optional[str] = typer.Option(None, "--orchestrator-mode"),
    advisor_mode: Optional[str] = typer.Option(None, "--advisor-mode"),
    implementer_mode: Optional[str] = typer.Option(None, "--implementer-mode"),
    reviewer_mode: Optional[str] = typer.Option(None, "--reviewer-mode"),
    execution_profile: str = typer.Option("rw", "--profile", help="Execution profile: ro or rw. Default rw lets write-capable agents modify files."),
    ro: bool = typer.Option(False, "--ro", "-ro", help="Read-only create-only execution profile."),
    rw: bool = typer.Option(False, "--rw", "-rw", help="Read-write execution profile. This is the default."),
    max_review_rounds: Optional[int] = typer.Option(None, "--max-review-rounds"),
    policy_retries: Optional[int] = typer.Option(
        None,
        "--policy-retries",
        help="Retry an agent call after filesystem policy violations by reframing the role prompt. Default comes from workflow.max_policy_violation_retries.",
    ),
    no_advisor: bool = typer.Option(False, "--no-advisor"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    quiet: bool = typer.Option(False, "--quiet"),
    no_agent_stream: bool = typer.Option(
        False,
        "--no-agent-stream",
        "--no-agent-output",
        help="Hide live provider agent stdout/stderr. Raw output is still retained in run artifacts.",
    ),
    codex_final: bool = typer.Option(
        False,
        "--codex-final",
        help="Emit compact Codex progress plus a sentinel-wrapped final block for Codex prompts/skills.",
    ),
    codex_progress: bool = typer.Option(
        True,
        "--codex-progress/--no-codex-progress",
        help="Show compact phase progress while --codex-final runs. Raw provider output remains hidden.",
    ),
    codex_progress_interval: float = typer.Option(
        30.0,
        "--codex-progress-interval",
        help="Seconds between still-running heartbeat lines in --codex-final mode.",
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id"),
):
    """
    Execute a full workflow run.
    """
    cfg_path = config or config_path(_project_root())
    cfg = load_config(cfg_path)

    if orchestrator and orchestrator not in cfg.agents:
        raise typer.BadParameter(f"Unknown orchestrator: {orchestrator}")
    if advisor and advisor not in cfg.agents:
        raise typer.BadParameter(f"Unknown advisor: {advisor}")
    if implementer and implementer not in cfg.agents:
        raise typer.BadParameter(f"Unknown implementer: {implementer}")
    if reviewer and reviewer not in cfg.agents:
        raise typer.BadParameter(f"Unknown reviewer: {reviewer}")

    mode = _coerce_mode(mode, "--mode") or "auto"
    orchestrator_mode = _coerce_mode(orchestrator_mode, "--orchestrator-mode")
    advisor_mode = _coerce_mode(advisor_mode, "--advisor-mode")
    implementer_mode = _coerce_mode(implementer_mode, "--implementer-mode")
    reviewer_mode = _coerce_mode(reviewer_mode, "--reviewer-mode")
    resolved_execution_profile = _resolve_execution_profile(execution_profile, ro, rw)

    role_modes: dict[str, str] = {}
    if orchestrator_mode:
        role_modes["orchestrator"] = orchestrator_mode
    if advisor_mode:
        role_modes["advisor"] = advisor_mode
    if implementer_mode:
        role_modes["implementer"] = implementer_mode
    if reviewer_mode:
        role_modes["reviewer"] = reviewer_mode
    if cfg.workflow.max_total_agent_calls <= 0:
        raise typer.BadParameter(
            f"Invalid workflow.max_total_agent_calls={cfg.workflow.max_total_agent_calls} in {cfg_path}. "
            "Set it to 1 or higher."
        )
    if max_review_rounds is not None and max_review_rounds < 0:
        raise typer.BadParameter("max-review-rounds must be 0 or higher.")
    if policy_retries is not None and policy_retries < 0:
        raise typer.BadParameter("policy-retries must be 0 or higher.")
    if codex_progress_interval <= 0:
        raise typer.BadParameter("codex-progress-interval must be greater than 0.")

    _apply_overrides(cfg, orchestrator, advisor, implementer, reviewer)
    if policy_retries is not None:
        cfg.workflow.max_policy_violation_retries = policy_retries
    _apply_output_flags(cfg, quiet=quiet or codex_final, no_agent_stream=no_agent_stream or codex_final)
    effective_codex_progress = bool(codex_final and codex_progress)
    selected_run_id = run_id or stable_id(PROJECT_NAME, task)
    try:
        out = _run_engine(
            cfg=cfg,
            task=task,
            run_id=selected_run_id,
            no_advisor=no_advisor,
            dry_run=dry_run,
            quiet=quiet or codex_final,
            max_review_rounds=max_review_rounds,
            mode=mode,
            role_modes=role_modes or None,
            execution_profile=resolved_execution_profile,
            codex_progress=effective_codex_progress,
            progress_interval_seconds=codex_progress_interval,
        )
    except RuntimeError as exc:
        _print_run_failure(selected_run_id, task, exc)
        raise typer.Exit(code=1) from exc
    if not codex_final:
        _print_labeled("Run ID", str(out["run_id"]))
    _print_run_summary(out["summary"], dry_run=dry_run, codex_final=codex_final)


@app.command()
def resume(
    run_id: str = typer.Argument(...),
    config: Optional[Path] = typer.Option(None, "--config", help="Optional override config snapshot path"),
    execution_profile: Optional[str] = typer.Option(None, "--profile", help="Override execution profile for resume: ro or rw"),
    ro: bool = typer.Option(False, "--ro", "-ro", help="Resume with read-only execution profile."),
    rw: bool = typer.Option(False, "--rw", "-rw", help="Resume with read-write execution profile."),
    quiet: bool = typer.Option(False, "--quiet"),
    no_agent_stream: bool = typer.Option(
        False,
        "--no-agent-stream",
        "--no-agent-output",
        help="Hide live provider agent stdout/stderr. Raw output is still retained in run artifacts.",
    ),
):
    """
    Resume a partial run from the persisted state.
    """
    root = _project_root()
    path = _agent_relay_root(root) / RUN_DIR_NAME / run_id
    if not path.exists():
        console.print(f"Run not found: {run_id}")
        raise typer.Exit(code=1)

    state_path = path / "state.json"
    snapshot_path = path / "config.snapshot.yaml"
    if not state_path.exists():
        console.print("Run state missing; cannot resume.")
        raise typer.Exit(code=1)

    state = RunState.load(state_path)
    if state.state.completed:
        console.print("Run already completed.")
        return
    if execution_profile is None:
        execution_profile = state.state.execution_profile or "rw"
    resolved_execution_profile = _resolve_execution_profile(execution_profile, ro, rw)
    console.print(f"Resuming run {run_id} at stage {state.state.stage.value}")
    console.print(f"Execution profile: {resolved_execution_profile}")
    if state.state.last_error:
        console.print(f"Previous error: {state.state.last_error}")
    completed = sorted([s.value for s in state.state.completed_stages])
    if completed:
        console.print(f"Completed stages: {', '.join(completed)}")

    cfg: Config
    if config:
        cfg = load_config(config)
    elif snapshot_path.exists():
        cfg = load_config(snapshot_path)
    else:
        cfg = load_config(config_path(root))
    _apply_output_flags(cfg, quiet=quiet, no_agent_stream=no_agent_stream)

    run_dir = RunDirectory(root, run_id)
    engine = WorkflowEngine(
        cfg,
        run_dir,
        state,
        console=console,
        quiet=quiet,
        execution_profile=resolved_execution_profile,
        repo_root=root,
    )
    try:
        result = asyncio.run(
            engine.run(
                task=state.state.task,
                no_advisor=False,
                max_review_rounds=cfg.workflow.max_review_rounds,
                dry_run=False,
            )
        )
    except RuntimeError as exc:
        _print_run_failure(run_id, state.state.task, exc)
        raise typer.Exit(code=1) from exc
    _print_labeled("Run ID", run_id)
    _print_run_summary(result, dry_run=False)


@app.command()
def status(run_id: Optional[str] = typer.Argument(None)):
    """
    Show latest run status or status for one run.
    """
    root = _project_root()
    runs = list_runs(root)
    if not runs:
        console.print("No runs found.")
        raise typer.Exit(code=1)

    selected = None
    if run_id is None:
        selected = sorted(runs, key=lambda run: run.state_path.stat().st_mtime, reverse=True)[0]
    else:
        for run in runs:
            if run.path.name == run_id:
                selected = run
                break
    if selected is None:
        console.print(f"Run not found: {run_id}")
        raise typer.Exit(code=1)

    state = RunState.load(selected.state_path).state
    console.print(f"Run: {state.run_id}")
    console.print(f"Stage: {state.stage.value}")
    console.print(f"Status: {state.status.value}")
    console.print(f"Task: {state.task}")
    console.print(f"Execution profile: {state.execution_profile}")
    console.print(f"Selected agents: {state.selected_agents}")
    console.print(f"Implemented tasks: {state.implemented_task_ids}")
    console.print(f"Review rounds: {state.review_round}")
    console.print(f"Last error: {state.last_error or '(none)'}")
    console.print(f"Completed stages: {[s.value for s in state.completed_stages]}")
    console.print(f"Calls: {state.call_count}")
    console.print(f"Run location: {selected.path}")
    console.print(f"Log: {selected.path / 'events.jsonl'}")


@app.command()
def logs(run_id: str = typer.Argument(...)):
    """
    Print run log and event trail.
    """
    root = _project_root()
    run_dir = _agent_relay_root(root) / RUN_DIR_NAME / run_id
    if not run_dir.exists():
        console.print(f"Run not found: {run_id}")
        raise typer.Exit(code=1)
    event_file = run_dir / "events.jsonl"
    if not event_file.exists():
        console.print("No events recorded.")
        return
    calls_dir = run_dir / "calls"
    console.print(f"== Events: {event_file}")
    for line in event_file.read_text(encoding="utf-8").splitlines():
        console.print(line)
    if calls_dir.exists():
        console.print("\n== Call artifacts:")
        for call_dir in sorted(calls_dir.iterdir()):
            if not call_dir.is_dir():
                continue
            console.print(f"- {call_dir.name}")
            for name in ("result.json", "metadata.json", "stdout.txt", "stderr.txt"):
                path = call_dir / name
                if path.exists():
                    console.print(f"  - {name}")
                    if name == "result.json":
                        try:
                            parsed = json.loads(path.read_text(encoding="utf-8"))
                            summary = parsed.get("agent_name", "agent")
                        except Exception:
                            summary = "unreadable"
                        console.print(f"    agent: {summary}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
