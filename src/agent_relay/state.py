from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel

from .config import RUN_DIR_NAME, active_project_dir
from .models import AgentCallResult, RunEvent, RunStateModel, RunStatus


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_atomic_json(path: Path, payload: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


class RunDirectory:
    def __init__(self, root: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = active_project_dir(root) / RUN_DIR_NAME / run_id

    @property
    def manifest(self) -> Path:
        return self.path / "manifest.json"

    @property
    def state_path(self) -> Path:
        return self.path / "state.json"

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def config_snapshot(self) -> Path:
        return self.path / "config.snapshot.yaml"

    @property
    def task_path(self) -> Path:
        return self.path / "task.md"

    @property
    def plan_path(self) -> Path:
        return self.path / "plan.json"

    @property
    def final_summary(self) -> Path:
        return self.path / "final-summary.md"

    @property
    def calls_dir(self) -> Path:
        return self.path / "calls"

    def create(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.calls_dir.mkdir(parents=True, exist_ok=True)


class RunState:
    def __init__(self, state_path: Path, state: Optional[RunStateModel] = None):
        self.path = state_path
        self.state = state or RunStateModel(
            run_id="",
            task="",
            created_at=now_iso(),
            updated_at=now_iso(),
            run_dir=str(state_path.parent),
        )

    @classmethod
    def load(cls, path: Path) -> "RunState":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, RunStateModel.model_validate(data))

    def write(self) -> None:
        payload = self.state.model_dump(mode="json")
        payload["status"] = self.state.status.value
        payload["stage"] = self.state.stage.value
        payload["completed_stages"] = [x.value for x in self.state.completed_stages]
        write_atomic_json(self.path, payload)

    def mark(self, status: RunStatus) -> None:
        self.state.mark(status, datetime.now(timezone.utc))


class RunEventStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: RunEvent) -> None:
        entry = event.model_dump(mode="json")
        entry["stage"] = event.stage.value
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def create_new_run(
    root: Path,
    run_id: str,
    task: str,
    run_limit: int,
    selected_agents: Dict[str, str],
    execution_profile: str = "ro",
) -> RunDirectory:
    rd = RunDirectory(root, run_id)
    if rd.path.exists():
        from shutil import rmtree

        rmtree(rd.path, ignore_errors=True)
    rd.create()
    state = RunState(
        rd.state_path,
        RunStateModel(
            run_id=run_id,
            task=task,
            created_at=now_iso(),
            updated_at=now_iso(),
            status=RunStatus.RECEIVED,
            stage=RunStatus.RECEIVED,
            selected_agents=selected_agents,
            execution_profile=execution_profile,
            run_dir=str(rd.path),
            total_calls_limit=run_limit,
        ),
    )
    state.write()
    rd.manifest.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "task": task,
                "status": RunStatus.RECEIVED.value,
                "execution_profile": execution_profile,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return rd


def list_runs(root: Path) -> List[RunDirectory]:
    base = active_project_dir(root) / RUN_DIR_NAME
    if not base.exists():
        return []
    runs = []
    for p in sorted(base.iterdir()):
        if p.is_dir() and (p / "state.json").exists():
            runs.append(RunDirectory(root, p.name))
    return runs


class CallStore:
    def __init__(self, calls_dir: Path) -> None:
        self.calls_dir = calls_dir
        self.calls_dir.mkdir(parents=True, exist_ok=True)

    def next_call_dir(self, index: int, agent_name: str) -> Path:
        name = f"{index:03d}-{agent_name}"
        path = self.calls_dir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_call_files(
        self,
        index: int,
        call_name: str,
        prompt: str,
        result: AgentCallResult,
    ) -> BaseModel:
        call_dir = self.next_call_dir(index, call_name)
        (call_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        (call_dir / "stdout.txt").write_text(result.raw_stdout, encoding="utf-8")
        (call_dir / "stderr.txt").write_text(result.raw_stderr, encoding="utf-8")
        (call_dir / "result.json").write_text(json.dumps(result.model_dump(mode="json"), indent=2), encoding="utf-8")
        meta = {
            "command": result.command,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration_seconds": result.duration_seconds,
            "exit_code": result.exit_code,
        }
        (call_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return result
