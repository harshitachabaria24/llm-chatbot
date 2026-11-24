from __future__ import annotations

import litellm
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def chat(client, message="What is the capital of France?", session="s-1", headers=None, **extra):
    return client.post("/chat", json={"session_id": session, "message": message, **extra}, headers=headers or {})


# ---- health -------------------------------------------------------------
def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}


# ---- basic chat + metadata ---------------------------------------------
def test_chat_live_inference_contract(client):
    r = chat(client)
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == "s-1"
    assert body["reply"]
    m = body["metadata"]
    assert m["resolution_source"] == "LIVE_INFERENCE"
    assert m["model"] == "fake/echo"
    assert isinstance(m["latency_ms"], int)
    assert m["input_tokens"] > 0 and m["output_tokens"] > 0
    assert m["key_id"] == "key_1"
    assert m["fallback_attempts"] == 0
    assert "sk-" not in r.text


# ---- caching --------------------------------------------------------------
def test_cache_hit_in_new_session_is_identical_and_faster(client, provider):
    first = chat(client, session="s-a").json()
    second = chat(client, session="s-b").json()  # same first message, new session
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    m = second["metadata"]
    assert m["resolution_source"] == "LLM_CACHE"
    assert second["reply"] == first["reply"]
    assert m["key_id"] is None
    assert m["fallback_attempts"] == 0
    assert m["input_tokens"] == first["metadata"]["input_tokens"]
    assert m["output_tokens"] == first["metadata"]["output_tokens"]
    assert m["latency_ms"] < first["metadata"]["latency_ms"]


def test_different_message_never_cached(client):
    chat(client, "hello there", session="s-a")
    other = chat(client, "something else entirely", session="s-b").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_different_system_prompt_is_a_cache_miss(client):
    chat(client, "hi", session="s-a")
    other = chat(client, "hi", session="s-b", system_prompt="Answer like a pirate.").json()
    assert other["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# ---- multi-turn -----------------------------------------------------------
def test_multi_turn_history_is_sent_and_stored(client, provider):
    chat(client, "my name is Asha", session="s-m")
    r = chat(client, "what is my name?", session="s-m").json()
    # system + (user, assistant) + new user = 4 messages reached the LLM
    assert r["reply"].startswith("echo[4]")
    hist = client.get("/sessions/s-m/messages").json()
    assert [m["role"] for m in hist["messages"]] == ["user", "assistant", "user", "assistant"]
    assert hist["messages"][0]["content"] == "my name is Asha"
    assert "metadata" in hist["messages"][1]
    assert hist["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert all(m["role"] != "system" for m in hist["messages"])


def test_sessions_are_isolated(client):
    chat(client, "alpha", session="s-1")
    chat(client, "beta", session="s-2")
    one = client.get("/sessions/s-1/messages").json()["messages"]
    two = client.get("/sessions/s-2/messages").json()["messages"]
    assert [m["content"] for m in one if m["role"] == "user"] == ["alpha"]
    assert [m["content"] for m in two if m["role"] == "user"] == ["beta"]


def test_system_prompt_is_used_and_persists_for_session(client, provider):
    chat(client, "hi", session="s-p", system_prompt="Be a pirate.")
    chat(client, "and again", session="s-p")  # no prompt sent this time
    first_call, second_call = provider.sent[-2], provider.sent[-1]
    assert first_call[0] == {"role": "system", "content": "Be a pirate."}
    assert second_call[0] == {"role": "system", "content": "Be a pirate."}
    # a different session still gets the default prompt
    chat(client, "yo", session="s-other")
    assert provider.sent[-1][0]["content"] == "You are a helpful, concise assistant."


def test_history_unknown_session_404(client):
    r = client.get("/sessions/nope/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}


def test_history_survives_new_app_instance(settings, provider):
    with TestClient(create_app(settings)) as c1:
        chat(c1, "persist me", session="s-persist")
    with TestClient(create_app(settings)) as c2:
        msgs = c2.get("/sessions/s-persist/messages").json()["messages"]
    assert msgs[0]["content"] == "persist me"
    litellm.cache = None


# ---- failover -------------------------------------------------------------
def test_simulated_429_on_key_1_uses_key_2(client):
    r = chat(client, "sim 429", headers={"X-Simulate-Failure": "key_1=429"})
    assert r.status_code == 200
    m = r.json()["metadata"]
    assert m["key_id"] == "key_2"
    assert m["fallback_attempts"] == 1
    assert m["resolution_source"] == "LIVE_INFERENCE"


def test_simulated_500_on_key_1_uses_key_2(client):
    r = chat(client, "sim 500", headers={"X-Simulate-Failure": "key_1=500"})
    assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_2"


def test_simulated_failure_bypasses_cache_for_that_request(client):
    chat(client, "cached already", session="s-a")  # populates cache via key_1
    r = chat(client, "cached already", session="s-b", headers={"X-Simulate-Failure": "key_1=429"}).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert r["metadata"]["key_id"] == "key_2"


def test_all_keys_simulated_failed_returns_503_and_not_stored(client):
    r = chat(client, "doomed", session="s-503", headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-503/messages").status_code == 404


def test_explicit_both_keys_failing_503(client):
    r = chat(client, "doomed2", headers={"X-Simulate-Failure": "key_1=429,key_2=500"})
    assert r.status_code == 503


def test_header_ignored_when_switch_off(settings, provider):
    settings.enable_failure_simulation = False
    with TestClient(create_app(settings)) as c:
        r = chat(c, "no sim", headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200 and r.json()["metadata"]["key_id"] == "key_1"
    litellm.cache = None


def test_real_provider_429_fails_over(client, provider):
    provider.failing["sk-aaa"] = litellm.RateLimitError(
        message="quota", llm_provider="gemini", model="m"
    )
    r = chat(client, "real 429")
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"
    assert r.json()["metadata"]["fallback_attempts"] == 1


def test_real_provider_5xx_and_generic_error_fail_over(client, provider):
    provider.failing["sk-aaa"] = litellm.ServiceUnavailableError(
        message="down", llm_provider="gemini", model="m"
    )
    assert chat(client, "real 503").json()["metadata"]["key_id"] == "key_2"
    provider.failing["sk-aaa"] = RuntimeError("socket exploded")
    assert chat(client, "real generic").json()["metadata"]["key_id"] == "key_2"


import pytest as _pytest


@_pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_every_simulated_status_fails_over(client, status):
    r = chat(client, f"sim {status}", headers={"X-Simulate-Failure": f"key_1={status}"})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_2"


def test_simulated_failure_does_not_poison_real_router(client, provider):
    chat(client, "sim only", headers={"X-Simulate-Failure": "key_1=429"})
    follow_up = chat(client, "normal afterwards").json()["metadata"]
    assert follow_up["key_id"] == "key_1" and follow_up["fallback_attempts"] == 0


def test_failed_real_key_is_skipped_during_cooldown(client, provider):
    provider.failing["sk-aaa"] = litellm.RateLimitError(message="quota", llm_provider="g", model="m")
    chat(client, "first")
    provider.keys_called.clear()
    r = chat(client, "second").json()["metadata"]
    assert r["key_id"] == "key_2" and r["fallback_attempts"] == 1
    assert "sk-aaa" not in provider.keys_called  # cooled-down key is not hammered again


def test_all_real_keys_failing_returns_clean_503(client, provider):
    for k in ("sk-aaa", "sk-bbb"):
        provider.failing[k] = RuntimeError("boom with secret " + k)
    r = chat(client, "all down")
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    assert "sk-" not in r.text and "Traceback" not in r.text


# ---- validation -------------------------------------------------------------
def test_validation_errors_422(client):
    assert client.post("/chat", json={"session_id": "s", "message": ""}).status_code == 422
    assert client.post("/chat", json={"session_id": "s", "message": "   "}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "x" * 129, "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "s", "message": "a" * 4001}).status_code == 422
    r = client.post("/chat", content="{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and "detail" in r.json()


def test_message_at_limit_ok_and_trimmed(client):
    assert chat(client, "a" * 4000).status_code == 200
    chat(client, "   padded   ", session="s-trim")
    msgs = client.get("/sessions/s-trim/messages").json()["messages"]
    assert msgs[0]["content"] == "padded"


def test_422_not_added_to_history(client):
    client.post("/chat", json={"session_id": "s-bad", "message": ""})
    assert client.get("/sessions/s-bad/messages").status_code == 404


def test_unknown_route_and_method_return_json(client):
    r = client.get("/nope")
    assert r.status_code == 404 and "detail" in r.json()
    r = client.get("/chat")
    assert r.status_code == 405 and "detail" in r.json()


# ---- CORS -------------------------------------------------------------------
def test_cors_allows_configured_origin_and_failure_header(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in r.headers["access-control-allow-headers"].lower()


def test_api_keys_never_appear_in_logs(client, provider, caplog):
    for k in ("sk-aaa", "sk-bbb"):
        provider.failing[k] = RuntimeError(f"GET https://api.example.com/v1?key={k} failed")
    with caplog.at_level("WARNING"):
        r = chat(client, "leak check")
    assert r.status_code == 503
    assert "sk-aaa" not in caplog.text and "sk-bbb" not in caplog.text
