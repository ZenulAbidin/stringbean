from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def force_tmp_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
