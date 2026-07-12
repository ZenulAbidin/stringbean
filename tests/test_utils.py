from __future__ import annotations

from agent_relay.utils import sanitize_environment


def test_environment_values_are_redacted():
    env = {"API_KEY": "super-secret", "USER": "alice", "PRIVATE_TOKEN": "x"}
    redacted = sanitize_environment(env)
    assert redacted["API_KEY"] == "REDACTED"
    assert redacted["USER"] == "alice"
    assert redacted["PRIVATE_TOKEN"] == "REDACTED"
