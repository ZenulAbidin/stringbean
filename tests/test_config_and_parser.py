from __future__ import annotations

import json
from pathlib import Path

from agent_relay.config import AgentConfig, Config, RepositoryConfig, WorkflowConfig, OutputConfig, load_config, save_config
from agent_relay.parser import parse_structured_output
from agent_relay.models import ImplementerResponse, OrchestratorPlan
def test_config_roundtrip_and_validation(tmp_path: Path):
    cfg = Config(
        agents={
            "local": AgentConfig(
                name="local",
                adapter="generic",
                model=None,
                role="orchestrator",
                permissions="read_write",
                command=["cat"],
            )
        },
        workflow=WorkflowConfig(orchestrator="local"),
        repository=RepositoryConfig(),
        output=OutputConfig(),
    )
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    loaded = load_config(path)
    assert loaded.agents["local"].name == "local"
    assert loaded.workflow.orchestrator == "local"


def test_generic_command_construction_from_config(tmp_path: Path):
    from agent_relay.adapters import GenericCLIAdapter

    cfg = AgentConfig(
        name="local",
        adapter="generic",
        role="generic",
        permissions="read_write",
        command=["echo", "hello"],
    )
    adapter = GenericCLIAdapter(cfg)
    cmd = adapter.build_command("prompt", tmp_path)
    assert cmd == ["echo", "hello"]


def test_parser_designated_block_and_fallback(tmp_path: Path):
    text = """
noise
```json
{"summary":"a","assumptions":[],"tasks":[],"risks":[],"advisor_questions":[]}
```
"""
    parsed, raw, err = parse_structured_output(text, OrchestratorPlan)
    assert err is None
    assert parsed is not None
    assert parsed.summary == "a"
    assert isinstance(raw, dict)

    text2 = '{"summary":"fallback","assumptions":[],"tasks":[],"risks":[],"advisor_questions":[]}'
    parsed2, raw2, err2 = parse_structured_output(text2, OrchestratorPlan)
    assert err2 is None
    assert parsed2 is not None
    assert raw2 == json.loads(text2)


def test_parser_malformed_response():
    text = "not-json"
    parsed, raw, err = parse_structured_output(text, OrchestratorPlan)
    assert parsed is None
    assert raw is None
    assert err == "no-json-found"


def test_implementer_response_coerces_structured_command_results():
    payload = {
        "status": "completed",
        "summary": "done",
        "files_changed": [],
        "commands_run": [
            {"command": "python3 -m pytest -q", "exit_code": 0},
        ],
        "tests": [],
        "remaining_issues": [],
        "handoff_notes": [],
    }
    parsed = ImplementerResponse.model_validate(payload)
    assert parsed.commands_run == ["python3 -m pytest -q (exit_code=0)"]


def test_invalid_mode_rejected():
    try:
        AgentConfig(
            name="bad",
            adapter="generic",
            role="orchestrator",
            permissions="read_write",
            command=["cat"],
            mode="extreme",
        )
    except ValueError as exc:
        assert "mode must be" in str(exc)
    else:
        raise AssertionError("Invalid mode was accepted")
