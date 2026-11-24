"""Test fixtures.

A LiteLLM *custom provider* ("fake/echo") plays the upstream API. Everything above
it is real: the LiteLLM Router (key pool + fallbacks), LiteLLM's cache, usage
accounting and the FastAPI app. No network access or real keys are needed.
"""

from __future__ import annotations

import asyncio

import litellm
import pytest
from fastapi.testclient import TestClient
from litellm import CustomLLM
from litellm.types.utils import Usage

from app.config import Settings
from app.main import create_app


class FakeProvider(CustomLLM):
    """Echoes the last user message. Keys listed in ``failing`` raise like a real API."""

    def __init__(self):
        super().__init__()
        self.failing: dict[str, Exception] = {}  # api_key -> exception to raise
        self.sent: list[list[dict]] = []         # messages of every call that reached the provider
        self.keys_called: list[str] = []
        self.delay = 0.05                        # network latency of a live call

    async def acompletion(self, model, messages, api_base, custom_prompt_dict, model_response,
                          print_verbose, encoding, api_key, logging_obj, optional_params,
                          acompletion=None, litellm_params=None, logger_fn=None, headers={},
                          timeout=None, client=None):
        self.keys_called.append(api_key)
        self.sent.append(messages)
        if api_key in self.failing:
            raise self.failing[api_key]
        await asyncio.sleep(self.delay)
        reply = f"echo[{len(messages)}]: {messages[-1]['content']}"
        model_response.choices[0].message.content = reply
        prompt_tokens = sum(len(m["content"].split()) + 3 for m in messages)
        completion_tokens = len(reply.split())
        model_response.usage = Usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        return model_response


@pytest.fixture()
def provider():
    fake = FakeProvider()
    litellm.custom_provider_map = [{"provider": "fake", "custom_handler": fake}]
    litellm.disable_cooldowns = False
    yield fake
    litellm.custom_provider_map = []


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        llm_api_keys=["sk-aaa", "sk-bbb"],
        llm_model="fake/echo",
        database_url=f"sqlite:///{tmp_path}/app.db",
        allowed_origins=["http://localhost:3000"],
        enable_failure_simulation=True,
        cache_enabled=True,
        key_cooldown_seconds=5,
    )


@pytest.fixture()
def client(settings, provider):
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    litellm.cache = None
