from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any

import pytest
import yaml

from agent_relay.policy import ACTIVE_CHILD_ENV, ACTIVE_CHILD_ERROR


REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "plugins" / "stringbean" / "mcp" / "server.py"


class McpSession:
    def __init__(
        self,
        *,
        env: dict[str, str] | None = None,
        command: list[str] | None = None,
        cwd: Path = REPO,
    ) -> None:
        self.process = subprocess.Popen(
            command or [sys.executable, str(SERVER)],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.next_id = 1

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"MCP server exited without a response: {stderr}")
        response = json.loads(line)
        assert response["id"] == request_id
        return response

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self.process.stdin is not None
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)


def _call_meta(workspace: Path, *, thread_id: str = "thread-one") -> dict[str, Any]:
    return {
        "threadId": thread_id,
        "codex/sandbox-state-meta": {
            "permissionProfile": {"type": "managed"},
            "codexLinuxSandboxExe": None,
            "sandboxCwd": workspace.resolve().as_uri(),
            "useLegacyLandlock": False,
        },
    }


def _tool_payload(response: dict[str, Any]) -> dict[str, Any]:
    result = response["result"]
    assert not result.get("isError", False), result
    return result["structuredContent"]


def _fake_plugin(
    tmp_path: Path,
    runner_body: str,
    *,
    max_log_bytes: int | None = None,
) -> tuple[Path, Path]:
    plugin_root = tmp_path / "plugin"
    server = plugin_root / "mcp" / "server.py"
    runner = plugin_root / "runtime" / "scripts" / "sbx"
    server.parent.mkdir(parents=True)
    runner.parent.mkdir(parents=True)
    shutil.copy2(SERVER, server)
    if max_log_bytes is not None:
        server_text = server.read_text(encoding="utf-8").replace(
            "MAX_LOG_BYTES = 16 * 1024 * 1024",
            f"MAX_LOG_BYTES = {max_log_bytes}",
        )
        server.write_text(server_text, encoding="utf-8")
    runner.write_text("#!/bin/bash\nset -euo pipefail\n" + runner_body, encoding="utf-8")
    runner.chmod(0o755)
    return plugin_root, server


def test_codex_plugin_preapproves_only_the_typed_start_boundary():
    manifest = json.loads(
        (REPO / "plugins" / "stringbean" / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    config = json.loads(
        (REPO / "plugins" / "stringbean" / ".mcp.json").read_text(encoding="utf-8")
    )["mcpServers"]["stringbean"]

    assert manifest["mcpServers"] == "./.mcp.json"
    assert config["default_tools_approval_mode"] == "auto"
    assert config["enabled_tools"] == ["start_sbx", "poll_sbx", "cancel_sbx"]
    assert config["tools"] == {"start_sbx": {"approval_mode": "approve"}}
    assert config["supports_parallel_tool_calls"] is False
    assert ACTIVE_CHILD_ENV in config["env_vars"]
    command = Path(config["command"])
    assert command.is_absolute()
    assert command.is_file()
    assert REPO not in command.resolve().parents


def test_codex_sbx_skill_remains_discoverable_for_unqualified_invocation():
    metadata = yaml.safe_load(
        (
            REPO
            / "plugins"
            / "stringbean"
            / "skills"
            / "sbx"
            / "agents"
            / "openai.yaml"
        ).read_text(encoding="utf-8")
    )

    # Plugin skills are namespaced as ``stringbean:sbx``. Keeping implicit
    # discovery enabled lets the established, unqualified ``$sbx`` spelling
    # bring the skill into context, where SKILL.md still requires an explicit
    # invocation before the pre-approved provider boundary is used.
    assert metadata.get("policy", {}).get("allow_implicit_invocation", True) is True


@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex CLI is not installed")
def test_codex_plain_dollar_sbx_discovers_installed_plugin(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    workspace = tmp_path / "workspace"
    codex_home.mkdir()
    workspace.mkdir()
    env = {**os.environ, "CODEX_HOME": str(codex_home)}

    for command in (
        ["codex", "plugin", "marketplace", "add", str(REPO), "--json"],
        ["codex", "plugin", "add", "stringbean@stringbean-local", "--json"],
    ):
        result = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    discovered = subprocess.run(
        ["codex", "debug", "prompt-input", "$sbx test"],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert discovered.returncode == 0, discovered.stderr
    assert "stringbean:sbx" in discovered.stdout


def test_actual_mcp_config_cannot_start_workspace_python(tmp_path: Path):
    config = json.loads(
        (REPO / "plugins" / "stringbean" / ".mcp.json").read_text(encoding="utf-8")
    )["mcpServers"]["stringbean"]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "workspace-python-ran"
    fake_python = fake_bin / "python3"
    fake_python.write_text(f"#!/bin/sh\nprintf used > {marker}\nexit 91\n", encoding="utf-8")
    fake_python.chmod(0o755)
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}

    session = McpSession(
        env=env,
        command=[config["command"], *config["args"]],
        cwd=REPO / "plugins" / "stringbean",
    )
    try:
        response = session.request("ping")
        assert response["result"] == {}
    finally:
        session.close()
    assert not marker.exists()


def test_codex_mcp_handshake_lists_narrow_honestly_annotated_tools():
    session = McpSession()
    try:
        initialized = session.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )["result"]
        assert initialized["capabilities"]["experimental"] == {
            "codex/sandbox-state-meta": {}
        }
        session.notify("notifications/initialized")

        tools = session.request("tools/list")["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        assert set(by_name) == {"start_sbx", "poll_sbx", "cancel_sbx"}
        assert by_name["start_sbx"]["inputSchema"]["required"] == ["task"]
        assert "working_directory" not in by_name["start_sbx"]["inputSchema"]["properties"]
        assert by_name["start_sbx"]["annotations"] == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        }
        assert by_name["poll_sbx"]["annotations"]["readOnlyHint"] is True
        assert by_name["cancel_sbx"]["annotations"]["destructiveHint"] is True
    finally:
        session.close()


def test_codex_mcp_hides_tools_and_rejects_calls_for_active_child(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = {**os.environ, ACTIVE_CHILD_ENV: "1"}
    session = McpSession(env=env)
    try:
        assert session.request("tools/list")["result"]["tools"] == []
        response = session.request(
            "tools/call",
            {
                "name": "start_sbx",
                "arguments": {"task": "must not run", "dry_run": True},
                "_meta": _call_meta(workspace),
            },
        )
        assert response["result"]["isError"] is True
        assert response["result"]["structuredContent"]["error"] == ACTIVE_CHILD_ERROR
    finally:
        session.close()


def test_codex_mcp_fails_closed_without_host_workspace_metadata():
    session = McpSession()
    try:
        response = session.request(
            "tools/call",
            {"name": "start_sbx", "arguments": {"task": "do not run", "dry_run": True}},
        )
        assert response["result"]["isError"] is True
        assert "sandbox metadata is required" in response["result"]["structuredContent"]["error"]
    finally:
        session.close()


def test_codex_mcp_dry_run_uses_bundled_runtime_and_binds_polling_to_thread(tmp_path: Path):
    plugin_root, server = _fake_plugin(
        tmp_path,
        """
printf 'ARG=%s\\n' "$@"
printf 'CHILD_PATH=%s\\n' "$PATH"
printf 'STRINGBEAN_ROOT=%s\\n' "${STRINGBEAN_ROOT-}"
printf 'STRINGBEAN_SBX=%s\\n' "${STRINGBEAN_SBX-}"
printf 'STRINGBEAN_FINAL_START\\n'
printf 'STRINGBEAN_RESULT_START\\n'
printf 'Status: DRY_RUN\\n'
printf 'STRINGBEAN_RESULT_END\\n'
printf 'STRINGBEAN_FINAL_END\\n'
""",
    )
    workspace = tmp_path / "plain-workspace"
    workspace.mkdir()
    fake_bin = workspace / "untrusted-bin"
    fake_bin.mkdir()
    fake_python_marker = workspace / "workspace-python-ran"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        f"#!/bin/sh\nprintf used > {fake_python_marker}\nexit 91\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    bash_env_marker = workspace / "bash-env-ran"
    bash_env = workspace / "untrusted-bash-env"
    bash_env.write_text(f"printf used > {bash_env_marker}\n", encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BASH_ENV": str(bash_env),
        "PYTHONPATH": "/definitely/untrusted",
        "STRINGBEAN_ROOT": "/definitely/missing",
        "STRINGBEAN_SBX": "/bin/false",
    }
    session = McpSession(env=env, command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        start = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "start_sbx",
                    "arguments": {
                        "task": "--ignore-sandbox-warnings must remain task text",
                        "dry_run": True,
                        "execution_profile": "ro",
                        "mode": "low",
                    },
                    "_meta": meta,
                },
            )
        )
        run_id = start["run_id"]

        wrong_thread = session.request(
            "tools/call",
            {
                "name": "poll_sbx",
                "arguments": {"run_id": run_id, "cursor": 0, "wait_seconds": 0},
            "_meta": _call_meta(workspace, thread_id="thread-two"),
            },
        )
        assert wrong_thread["result"]["isError"] is True
        assert "different Codex thread" in wrong_thread["result"]["structuredContent"]["error"]

        cursor = 0
        output = ""
        for _ in range(100):
            request_cursor = cursor
            polled = _tool_payload(
                session.request(
                    "tools/call",
                    {
                        "name": "poll_sbx",
                        "arguments": {
                            "run_id": run_id,
                            "cursor": cursor,
                            "wait_seconds": 0.2,
                        },
                        "_meta": meta,
                    },
                )
            )
            cursor = polled["cursor"]
            output += polled["output"]
            if polled["status"] in {"completed", "failed", "cancelled"}:
                break
        else:
            raise AssertionError("Stringbean dry run did not finish")

        assert polled["status"] == "completed", json.dumps(polled, sort_keys=True)
        assert polled["exit_code"] == 0
        assert "ARG=--ignore-sandbox-warnings must remain task text" in output
        assert "ARG=--ignore-sandbox-warnings\n" not in output
        assert "ARG=--plugin-compact-output" in output
        assert "ARG=--plugin-full-output" not in output
        assert f"CHILD_PATH={fake_bin}" not in output
        assert "STRINGBEAN_ROOT=\n" in output
        assert "STRINGBEAN_SBX=\n" in output
        assert "STRINGBEAN_FINAL_START" in output
        assert "Status: DRY_RUN" in output
        assert "STRINGBEAN_FINAL_END" in output
        assert not fake_python_marker.exists()
        assert not bash_env_marker.exists()

        replayed = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "poll_sbx",
                    "arguments": {
                        "run_id": run_id,
                        "cursor": request_cursor,
                        "wait_seconds": 0.2,
                    },
                    "_meta": meta,
                },
            )
        )
        assert replayed == polled
        acknowledged = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "poll_sbx",
                    "arguments": {
                        "run_id": run_id,
                        "cursor": polled["cursor"],
                        "wait_seconds": 0,
                    },
                    "_meta": meta,
                },
            )
        )
        assert acknowledged["status"] == "completed"
        assert acknowledged["output"] == ""
    finally:
        session.close()


def test_codex_mcp_drains_split_utf8_and_large_final_burst(tmp_path: Path):
    plugin_root, server = _fake_plugin(
        tmp_path,
        f"""
/usr/bin/python3 - <<'PY'
import os
os.write(1, b'a' * (64 * 1024 - 1))
os.write(1, '😀'.encode('utf-8'))
os.write(1, b'\\nSTRINGBEAN_FINAL_START\\nSTRINGBEAN_RESULT_START\\nStatus: DRY_RUN\\nSTRINGBEAN_RESULT_END\\nSTRINGBEAN_FINAL_END\\n')
PY
""",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = McpSession(command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        start = _tool_payload(
            session.request(
                "tools/call",
                {"name": "start_sbx", "arguments": {"task": "burst"}, "_meta": meta},
            )
        )
        cursor = 0
        output = ""
        statuses: list[str] = []
        for _ in range(20):
            polled = _tool_payload(
                session.request(
                    "tools/call",
                    {
                        "name": "poll_sbx",
                        "arguments": {
                            "run_id": start["run_id"],
                            "cursor": cursor,
                            "wait_seconds": 0.5,
                        },
                        "_meta": meta,
                    },
                )
            )
            cursor = polled["cursor"]
            output += polled["output"]
            statuses.append(polled["status"])
            if polled["status"] in {"completed", "failed", "cancelled"}:
                break
        assert polled["status"] == "completed"
        assert output.count("😀") == 1
        assert output.endswith("STRINGBEAN_FINAL_END\n")
        assert "completed" not in statuses[:-1]
    finally:
        session.close()


def test_codex_mcp_caps_unpolled_output_and_stops_the_run(tmp_path: Path):
    plugin_root, server = _fake_plugin(
        tmp_path,
        """
/usr/bin/python3 - <<'PY'
import os
import time
while True:
    os.write(1, b'x' * 65536)
    time.sleep(0.001)
PY
""",
        max_log_bytes=64 * 1024,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = McpSession(command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        start = _tool_payload(
            session.request(
                "tools/call",
                {"name": "start_sbx", "arguments": {"task": "noisy"}, "_meta": meta},
            )
        )
        time.sleep(0.5)
        cursor = 0
        output = ""
        for _ in range(20):
            polled = _tool_payload(
                session.request(
                    "tools/call",
                    {
                        "name": "poll_sbx",
                        "arguments": {
                            "run_id": start["run_id"],
                            "cursor": cursor,
                            "wait_seconds": 0.5,
                        },
                        "_meta": meta,
                    },
                )
            )
            cursor = polled["cursor"]
            output += polled["output"]
            if polled["status"] in {"completed", "failed", "cancelled"}:
                break
        assert polled["status"] == "failed"
        assert "output exceeded the 16 MiB safety limit" in polled["error"]
        assert "plugin output exceeded the 16 MiB safety limit" in output
        assert len(output.encode("utf-8")) <= 64 * 1024
    finally:
        session.close()


def test_codex_mcp_rejects_malformed_requests_without_exiting():
    session = McpSession()
    try:
        invalid_params = session.request("initialize", [])  # type: ignore[arg-type]
        assert invalid_params["error"]["code"] == -32602
        assert session.request("ping")["result"] == {}

        assert session.process.stdin is not None
        assert session.process.stdout is not None
        session.process.stdin.write("[]\n")
        session.process.stdin.flush()
        invalid_request = json.loads(session.process.stdout.readline())
        assert invalid_request["error"]["code"] == -32600
        assert session.request("ping")["result"] == {}
    finally:
        session.close()


def test_codex_mcp_cancellation_is_confirmed_and_idempotent(tmp_path: Path):
    plugin_root, server = _fake_plugin(
        tmp_path,
        """
exec /usr/bin/python3 - <<'PY'
import signal
import sys
import time

def stop(_signum, _frame):
    print('STRINGBEAN_FINAL_START', flush=True)
    print('STRINGBEAN_RESULT_START', flush=True)
    print('Status: CANCELLED', flush=True)
    print('STRINGBEAN_RESULT_END', flush=True)
    print('STRINGBEAN_FINAL_END', flush=True)
    raise SystemExit(130)

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
print('READY', flush=True)
while True:
    time.sleep(1)
PY
""",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = McpSession(command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        start = _tool_payload(
            session.request(
                "tools/call",
                {"name": "start_sbx", "arguments": {"task": "wait"}, "_meta": meta},
            )
        )
        run_id = start["run_id"]
        denied = session.request(
            "tools/call",
            {
                "name": "cancel_sbx",
                "arguments": {"run_id": run_id, "confirmed_by_user": False},
                "_meta": meta,
            },
        )
        assert denied["result"]["isError"] is True

        cursor = 0
        output = ""
        for _ in range(20):
            ready = _tool_payload(
                session.request(
                    "tools/call",
                    {
                        "name": "poll_sbx",
                        "arguments": {
                            "run_id": run_id,
                            "cursor": cursor,
                            "wait_seconds": 0.2,
                        },
                        "_meta": meta,
                    },
                )
            )
            cursor = ready["cursor"]
            output += ready["output"]
            if "READY" in output:
                break
        assert "READY" in output

        cancelled = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "cancel_sbx",
                    "arguments": {"run_id": run_id, "confirmed_by_user": True},
                    "_meta": meta,
                },
            )
        )
        assert cancelled["status"] == "cancelled"
        repeated = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "cancel_sbx",
                    "arguments": {"run_id": run_id, "confirmed_by_user": True},
                    "_meta": meta,
                },
            )
        )
        assert repeated["status"] == "already_cancelled"

        for _ in range(20):
            polled = _tool_payload(
                session.request(
                    "tools/call",
                    {
                        "name": "poll_sbx",
                        "arguments": {
                            "run_id": run_id,
                            "cursor": cursor,
                            "wait_seconds": 0.2,
                        },
                        "_meta": meta,
                    },
                )
            )
            cursor = polled["cursor"]
            output += polled["output"]
            if polled["status"] == "cancelled":
                break
        assert polled["status"] == "cancelled"
        assert "Status: CANCELLED" in output
    finally:
        session.close()


def test_codex_mcp_does_not_relabel_a_naturally_finished_run_as_cancelled(tmp_path: Path):
    plugin_root, server = _fake_plugin(
        tmp_path,
        """
printf 'STRINGBEAN_FINAL_START\\n'
printf 'STRINGBEAN_RESULT_START\\n'
printf 'Status: DRY_RUN\\n'
printf 'STRINGBEAN_RESULT_END\\n'
printf 'STRINGBEAN_FINAL_END\\n'
""",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = McpSession(command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        start = _tool_payload(
            session.request(
                "tools/call",
                {"name": "start_sbx", "arguments": {"task": "finish"}, "_meta": meta},
            )
        )
        time.sleep(0.2)
        result = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "cancel_sbx",
                    "arguments": {"run_id": start["run_id"], "confirmed_by_user": True},
                    "_meta": meta,
                },
            )
        )
        assert result["status"] == "already_finished"
        polled = _tool_payload(
            session.request(
                "tools/call",
                {
                    "name": "poll_sbx",
                    "arguments": {"run_id": start["run_id"], "cursor": 0, "wait_seconds": 0},
                    "_meta": meta,
                },
            )
        )
        assert polled["status"] == "completed"
        assert polled["workflow_status"] == "DRY_RUN"
    finally:
        session.close()


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        state = stat_path.read_text(encoding="utf-8").split()[2]
    except (OSError, IndexError):
        return True
    return state != "Z"


@pytest.mark.parametrize("shutdown", ["terminate", "kill"])
def test_codex_mcp_server_shutdown_does_not_orphan_run_processes(
    tmp_path: Path,
    shutdown: str,
):
    plugin_root, server = _fake_plugin(
        tmp_path,
        """
exec /usr/bin/python3 - <<'PY'
import os
from pathlib import Path
import signal
import subprocess
import time

child = subprocess.Popen(['/bin/sleep', '300'])
Path(os.environ['MCP_TEST_PID_FILE']).write_text(f'{os.getpid()} {child.pid}', encoding='utf-8')

def stop(_signum, _frame):
    child.terminate()
    try:
        child.wait(timeout=2)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    raise SystemExit(130)

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)
while True:
    time.sleep(1)
PY
""",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pid_file = tmp_path / "run-pids"
    temp_root = tmp_path / "server-temp"
    temp_root.mkdir()
    env = {**os.environ, "MCP_TEST_PID_FILE": str(pid_file), "TMPDIR": str(temp_root)}
    session = McpSession(env=env, command=[sys.executable, str(server)], cwd=plugin_root)
    try:
        meta = _call_meta(workspace)
        _tool_payload(
            session.request(
                "tools/call",
                {"name": "start_sbx", "arguments": {"task": "wait"}, "_meta": meta},
            )
        )
        deadline = time.monotonic() + 3
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert pid_file.exists()
        run_pid, child_pid = (int(value) for value in pid_file.read_text(encoding="utf-8").split())

        getattr(session.process, shutdown)()
        session.process.wait(timeout=5)
        deadline = time.monotonic() + 3
        while any(_pid_is_running(pid) for pid in (run_pid, child_pid)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _pid_is_running(run_pid)
        assert not _pid_is_running(child_pid)
        if shutdown == "terminate":
            assert not list(temp_root.glob("stringbean-codex-mcp-*"))
    finally:
        session.close()


def test_codex_plugin_runtime_snapshot_matches_the_reviewed_source():
    source_root = REPO / "src" / "agent_relay"
    snapshot_root = REPO / "plugins" / "stringbean" / "runtime" / "src" / "agent_relay"

    def file_map(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }

    assert file_map(snapshot_root) == file_map(source_root)
    for relative in ("scripts/sbx", "pyproject.toml", "README.md", "LICENSE"):
        assert (REPO / "plugins" / "stringbean" / "runtime" / relative).read_bytes() == (
            REPO / relative
        ).read_bytes()
    assert os.access(REPO / "plugins" / "stringbean" / "runtime" / "scripts" / "sbx", os.X_OK)
    assert not list((REPO / "plugins" / "stringbean" / "runtime").rglob("*.pyc"))
    assert not list((REPO / "plugins" / "stringbean" / "runtime").rglob("__pycache__"))
