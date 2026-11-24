import litellm
import pytest

from app.simulation import parse_simulation_header


def chat(client, session_id, message, headers=None, **extra):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


# ---------------------------------------------------------------- health
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# ---------------------------------------------------------------- chat contract
def test_chat_live_response_shape(client, sid):
    s = sid()
    r = chat(client, s, "What is the capital of France?")
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == s
    assert body["reply"]
    m = body["metadata"]
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert m["model"] == "gemini/gemini-2.5-flash"
    assert isinstance(m["latency_ms"], int)
    assert m["input_tokens"] > 0 and m["output_tokens"] > 0
    assert m["key_id"] == "key_1"
    assert m["fallback_attempts"] == 0
    assert "fake-key" not in r.text  # never leak the key itself


def test_cache_hit_same_first_message_new_session(client, sid):
    first = chat(client, sid(), "Tell me a fact about owls").json()
    second = chat(client, sid(), "Tell me a fact about owls").json()
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["fallback_attempts"] == 0
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert second["metadata"]["output_tokens"] == first["metadata"]["output_tokens"]


def test_different_message_never_from_cache(client, sid):
    chat(client, sid(), "message A")
    r = chat(client, sid(), "message B").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_tokens_are_per_request_not_cumulative(client, sid):
    a = chat(client, sid(), "aaaa bbbb").json()["metadata"]
    b = chat(client, sid(), "cccc dddd").json()["metadata"]
    assert abs(a["input_tokens"] - b["input_tokens"]) <= 2


def test_system_prompt_changes_cache_key(client, sid):
    chat(client, sid(), "hello there")
    r = chat(client, sid(), "hello there", system_prompt="You are a pirate.").json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---------------------------------------------------------------- multi-turn + history
def test_multi_turn_history_and_isolation(client, sid):
    s1, s2 = sid(), sid()
    chat(client, s1, "My name is Kripansh")
    second = chat(client, s1, "What is my name?").json()
    # second request carries system + 2 history msgs + new msg -> more input tokens
    first_only = chat(client, s2, "What is my name?").json()
    assert second["metadata"]["input_tokens"] > first_only["metadata"]["input_tokens"]

    h = client.get(f"/sessions/{s1}/messages").json()
    assert h["session_id"] == s1
    assert [m["role"] for m in h["messages"]] == ["user", "assistant", "user", "assistant"]
    assert h["messages"][0] == {"role": "user", "content": "My name is Kripansh"}
    assert h["messages"][1]["metadata"]["resolution_source"] in ("LIVE_INFERENCE", "LLM_CACHE")
    assert "input_tokens" in h["messages"][1]["metadata"]
    assert all(m["role"] != "system" for m in h["messages"])

    h2 = client.get(f"/sessions/{s2}/messages").json()
    assert len(h2["messages"]) == 2


def test_unknown_session_404(client):
    r = client.get("/sessions/does-not-exist/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s1"},
        {"message": "hi"},
        {"session_id": "", "message": "hi"},
        {"session_id": "s1", "message": "   "},
        {"session_id": "s1", "message": "x" * 4001},
        {"session_id": "x" * 129, "message": "hi"},
    ],
)
def test_validation_422(client, payload):
    r = client.post("/chat", json=payload)
    assert r.status_code == 422
    assert "detail" in r.json()


def test_message_trimmed_length(client, sid):
    r = chat(client, sid(), "  " + "x" * 4000 + "  ")
    assert r.status_code == 200


def test_malformed_json_422(client):
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "detail" in r.json()


# ---------------------------------------------------------------- failover
@pytest.mark.parametrize("header", ["key_1=429", "key_1=500", "key_1=502", "key_1=503"])
def test_simulated_failover_to_key_2(client, sid, header):
    r = chat(client, sid(), f"failover test {header}", headers={"X-Simulate-Failure": header})
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2"
    assert m["fallback_attempts"] == 1
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert "[mock reply" in r.json()["reply"]  # a real answer, not an error in a 200


def test_simulated_failover_ignores_cache(client, sid):
    chat(client, sid(), "cached question")
    r = chat(client, sid(), "cached question", headers={"X-Simulate-Failure": "key_1=429"}).json()
    assert r["metadata"]["key_id"] == "key_2"


def test_all_keys_fail_503_and_nothing_stored(client, sid):
    s = sid()
    for header in ["*=429", "key_1=429,key_2=500"]:
        r = chat(client, s, "will fail", headers={"X-Simulate-Failure": header})
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}
    assert client.get(f"/sessions/{s}/messages").status_code == 404


def test_failed_request_not_added_to_existing_history(client, sid):
    s = sid()
    chat(client, s, "first")
    chat(client, s, "second", headers={"X-Simulate-Failure": "*=503"})
    assert len(client.get(f"/sessions/{s}/messages").json()["messages"]) == 2


def test_simulation_disabled_ignores_header(tmp_path, monkeypatch, sid):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/t2.db")
    monkeypatch.setenv("LLM_API_KEYS", "a,b")
    monkeypatch.setenv("MOCK_LLM", "true")
    monkeypatch.setenv("ENABLE_FAILURE_SIMULATION", "false")
    from fastapi.testclient import TestClient
    from app.main import create_app

    with TestClient(create_app()) as c:
        r = chat(c, sid(), "hi", headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200
        assert r.json()["metadata"]["key_id"] == "key_1"


def test_real_provider_errors_fail_over(client, sid, monkeypatch):
    """Real LiteLLM exceptions (not the simulation hook) also trigger failover."""
    gateway = client.app.state.gateway
    original = gateway._call

    async def flaky(slot, messages):
        if slot.key_id == "key_1":
            raise litellm.RateLimitError("quota exceeded", "gemini", slot.model)
        return await original(slot, messages)

    monkeypatch.setattr(gateway, "_call", flaky)
    r = chat(client, sid(), "real error path").json()
    assert r["metadata"]["key_id"] == "key_2"
    assert r["metadata"]["fallback_attempts"] == 1

    async def always_down(slot, messages):
        raise litellm.ServiceUnavailableError("down", "gemini", slot.model)

    monkeypatch.setattr(gateway, "_call", always_down)
    r = chat(client, sid(), "all down")
    assert r.status_code == 503


def test_parse_simulation_header():
    assert parse_simulation_header("key_1=429, key_2=500") == {"key_1": 429, "key_2": 500}
    assert parse_simulation_header("*=429") == {"*": 429}
    assert parse_simulation_header("garbage,key_1=418,key_2=abc") == {}
    assert parse_simulation_header(None) == {}
