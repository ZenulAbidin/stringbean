from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROJECT_NAME = "stringbean"
PROJECT_DIR_NAME = f".{PROJECT_NAME}"
CONFIG_FILE_NAME = "config.yaml"
RUN_DIR_NAME = "runs"


class UnsupportedConfigWarning(UserWarning):
    """Warning emitted when accepted config has no runtime implementation yet."""


def _warn_reserved_config(field: str, detail: str) -> None:
    warnings.warn(
        f"{field} is reserved and currently has no effect. {detail}",
        UnsupportedConfigWarning,
        stacklevel=3,
    )


class FileResourceMixin:
    """Shared helpers for writing/reading yaml files."""

    @staticmethod
    def load_yaml(path: Path) -> Dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path} must contain a YAML mapping")
        return data

    @staticmethod
    def dump_yaml(path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=True)


class AgentConfig(BaseModel):
    name: str
    adapter: str
    model: Optional[str] = None
    role: str
    permissions: str
    command: Optional[List[str]] = None
    prompt_transport: str = "stdin"
    environment_overrides: Dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 1200
    working_directory: str = "."
    fallback_agent: Optional[str] = None
    mode: Optional[str] = None

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("permissions")
    @classmethod
    def _validate_permissions(cls, value: str) -> str:
        if value not in {"read_only", "read_write"}:
            raise ValueError("permissions must be read_only or read_write")
        return value

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = value.strip().lower()
        if value not in {"high", "medium", "low"}:
            raise ValueError("mode must be high, medium, or low")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout_seconds(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return float(value)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: str) -> str:
        valid_roles = {
            "orchestrator",
            "advisor",
            "implementer",
            "reviewer",
            "tester",
            "researcher",
            "generic",
        }
        if value not in valid_roles:
            raise ValueError(f"invalid role: {value}")
        return value


class WorkflowConfig(BaseModel):
    orchestrator: str
    advisors: List[str] = Field(default_factory=list)
    implementers: List[str] = Field(default_factory=list)
    reviewers: List[str] = Field(default_factory=list)
    testers: List[str] = Field(default_factory=list)
    researcher: List[str] = Field(default_factory=list)
    advisor_policy: str = "before_implementation"
    max_review_rounds: int = 2
    max_total_agent_calls: int = 20
    max_policy_violation_retries: int = 2
    parallel_read_only_agents: bool = False
    parallel_write_agents: bool = False

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    @field_validator("advisor_policy")
    @classmethod
    def _validate_policy(cls, value: str) -> str:
        if value not in {"before_implementation", "never"}:
            raise ValueError("advisor_policy must be before_implementation or never")
        return value

    @field_validator("max_review_rounds")
    @classmethod
    def _validate_non_negative_review_rounds(cls, value: int) -> int:
        if value < 0:
            raise ValueError("workflow.max_review_rounds must be 0 or higher")
        return int(value)

    @field_validator("max_total_agent_calls")
    @classmethod
    def _validate_positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("workflow limits must be positive integers")
        return int(value)

    @field_validator("max_policy_violation_retries")
    @classmethod
    def _validate_non_negative_int(cls, value: int) -> int:
        if value < 0:
            raise ValueError("workflow.max_policy_violation_retries must be 0 or higher")
        return int(value)

    @model_validator(mode="after")
    def _warn_reserved_contracts(self) -> "WorkflowConfig":
        if self.testers:
            _warn_reserved_config("workflow.testers", "Tester agents are not scheduled by the current workflow.")
        if self.researcher:
            _warn_reserved_config("workflow.researcher", "Researcher agents are not scheduled by the current workflow.")
        if self.parallel_read_only_agents is not False:
            _warn_reserved_config(
                "workflow.parallel_read_only_agents",
                "Agent execution is sequential in the current workflow.",
            )
        if self.parallel_write_agents is not False:
            _warn_reserved_config(
                "workflow.parallel_write_agents",
                "Write-capable agent execution is serialized in the current workflow.",
            )
        return self


class RepositoryConfig(BaseModel):
    require_git: bool = True
    require_clean_start: bool = False
    create_checkpoint_commits: bool = False

    @model_validator(mode="after")
    def _warn_reserved_contracts(self) -> "RepositoryConfig":
        if self.create_checkpoint_commits:
            _warn_reserved_config(
                "repository.create_checkpoint_commits",
                "Stringbean does not create checkpoint commits.",
            )
        return self


class OutputConfig(BaseModel):
    stream_agent_output: bool = True
    retain_raw_output: bool = True
    redact_environment_values: bool = True


class Config(BaseModel):
    version: int = 1
    agents: Dict[str, AgentConfig]
    workflow: WorkflowConfig
    repository: RepositoryConfig = Field(default_factory=RepositoryConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    model_config = ConfigDict(extra="forbid")

    @property
    def project_dir(self) -> str:
        return PROJECT_DIR_NAME


def active_project_dir(cwd: Optional[Path] = None) -> Path:
    # Prefer a .stringbean directory near the target path, then fallback to the
    # current user's home directory as the default workspace.
    cwd = Path(cwd or ".").resolve()
    current = cwd
    while True:
        preferred = current / PROJECT_DIR_NAME
        if preferred.exists():
            return preferred
        if current.parent == current:
            break
        current = current.parent

    return Path.home() / PROJECT_DIR_NAME


def _resolve_project_dir(cwd: Optional[Path] = None) -> Path:
    return active_project_dir(cwd)


def config_path(cwd: Optional[Path] = None) -> Path:
    return _resolve_project_dir(cwd) / CONFIG_FILE_NAME


def load_config(path: Optional[Path] = None) -> Config:
    if path is None:
        path = config_path()
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    payload = FileResourceMixin.load_yaml(path)
    return Config.model_validate(payload)


def save_config(config: Config, path: Optional[Path] = None) -> Path:
    if path is None:
        path = config_path()
    payload = config.model_dump()
    FileResourceMixin.dump_yaml(path, payload)
    return path
