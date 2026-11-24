import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("LLM_API_KEYS", "fake-key-one,fake-key-two")
    monkeypatch.setenv("LLM_MODEL", "gemini/gemini-2.5-flash")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "true")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("CACHE_TYPE", "local")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000")

    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


@pytest.fixture()
def sid():
    return lambda: f"s-{uuid.uuid4().hex[:8]}"
