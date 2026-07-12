from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _coerce_string_list(value: Any) -> Any:
    if value is None or not isinstance(value, list):
        return value
    out: List[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            command = item.get("command") or item.get("cmd") or item.get("path") or item.get("file")
            issue = item.get("issue") or item.get("title") or item.get("summary") or item.get("message")
            if command is not None and "exit_code" in item:
                out.append(f"{command} (exit_code={item['exit_code']})")
            elif command is not None:
                out.append(str(command))
            elif issue is not None:
                out.append(str(issue))
            else:
                out.append(json.dumps(item, sort_keys=True))
        else:
            out.append(str(item))
    return out


class RunStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    ADVISOR_REVIEW = "ADVISOR_REVIEW"
    PLAN_REVISION = "PLAN_REVISION"
    IMPLEMENTING = "IMPLEMENTING"
    REVIEWING = "REVIEWING"
    FIXING = "FIXING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"


class AgentCallResult(BaseModel):
    agent_name: str
    role: str
    stage: RunStatus
    command: List[str]
    exit_code: Optional[int]
    duration_seconds: float
    start_time: str
    end_time: str
    raw_stdout: str
    raw_stderr: str
    parsed_output: Optional[Dict[str, Any]] = None
    parse_error: Optional[str] = None
    stream_file: Optional[str] = None
    diff_delta_files: Optional[List[str]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RunEvent(BaseModel):
    timestamp: str
    stage: RunStatus
    event: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class OrchestratorTask(BaseModel):
    id: str
    title: str
    description: str
    dependencies: List[str] = Field(default_factory=list)
    recommended_role: str = "implementer"
    permissions: str = "read_write"
    verification: List[str] = Field(default_factory=list)


class OrchestratorPlan(BaseModel):
    summary: str
    assumptions: List[str] = Field(default_factory=list)
    tasks: List[OrchestratorTask] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    advisor_questions: List[str] = Field(default_factory=list)


class AdvisorResponse(BaseModel):
    verdict: str
    severity: str = "none"
    summary: str
    blockers: List[str] = Field(default_factory=list)
    concerns: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)

    @field_validator("blockers", "concerns", "recommendations", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> Any:
        return _coerce_string_list(value)


class ImplementerResponse(BaseModel):
    status: str
    summary: str
    files_changed: List[str] = Field(default_factory=list)
    commands_run: List[str] = Field(default_factory=list)
    tests: List[str] = Field(default_factory=list)
    remaining_issues: List[str] = Field(default_factory=list)
    handoff_notes: List[str] = Field(default_factory=list)

    @field_validator("files_changed", "commands_run", "tests", "remaining_issues", "handoff_notes", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> Any:
        return _coerce_string_list(value)


class ReviewerResponse(BaseModel):
    verdict: str
    summary: str
    blocking_issues: List[str] = Field(default_factory=list)
    non_blocking_issues: List[str] = Field(default_factory=list)
    required_fixes: List[str] = Field(default_factory=list)
    tests_recommended: List[str] = Field(default_factory=list)

    @field_validator("blocking_issues", "non_blocking_issues", "required_fixes", "tests_recommended", mode="before")
    @classmethod
    def _coerce_string_list(cls, value: Any) -> Any:
        return _coerce_string_list(value)


class RunStateModel(BaseModel):
    run_id: str
    task: str
    created_at: str
    updated_at: str
    status: RunStatus = RunStatus.RECEIVED
    stage: RunStatus = RunStatus.RECEIVED
    review_round: int = 0
    selected_agents: Dict[str, str] = Field(default_factory=dict)
    execution_profile: str = "ro"
    plan_id: Optional[str] = None
    implemented_task_ids: List[str] = Field(default_factory=list)
    advisory_blocks: int = 0
    review_history: List[str] = Field(default_factory=list)
    call_count: int = 0
    completed_stages: List[RunStatus] = Field(default_factory=list)
    last_error: Optional[str] = None
    run_dir: str
    total_calls_limit: int = 20
    resume_from_stage: Optional[RunStatus] = None
    completed: bool = False

    def mark(self, status: RunStatus, at: datetime) -> None:
        self.status = status
        self.stage = status
        stamp = at.isoformat()
        self.updated_at = stamp
        if status not in self.completed_stages:
            self.completed_stages.append(status)

    def done(self) -> bool:
        return self.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
