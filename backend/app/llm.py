"""LiteLLM gateway: Router-based key pool with fallbacks, plus LiteLLM's built-in cache."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import Router

from .config import Settings

logger = logging.getLogger("chatbot.llm")

litellm.suppress_debug_info = True
litellm.set_verbose = False
logging.getLogger("LiteLLM").setLevel(logging.WARNING)
# The Router logs verbose debug blobs on total failure; we log our own concise line.
logging.getLogger("LiteLLM Router").setLevel(logging.CRITICAL)

SIMULATABLE_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    """Every key in the pool failed for this request."""


class SimulatedProviderError(Exception):
    """Stands in for a provider returning an HTTP error (X-Simulate-Failure).

    LiteLLM converts an Exception carrying ``status_code`` into a MockException with
    that status, which the Router treats like a real provider failure. Kept simple so
    it survives the deep copies the Router makes of its configuration.
    """

    def __init__(self, status_code: int = 500):
        super().__init__(status_code)
        self.status_code = status_code
        self.text = f"simulated provider error {status_code}"


@dataclass
class LLMResult:
    reply: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int


def parse_failure_header(value: str | None) -> dict[str, int]:
    """Parse ``key_1=429,key_2=500`` / ``*=429``. Invalid pairs are ignored."""
    simulated: dict[str, int] = {}
    if not value:
        return simulated
    for pair in value.split(","):
        key, sep, status = pair.partition("=")
        key, status = key.strip(), status.strip()
        if sep and key and status.isdigit() and int(status) in SIMULATABLE_STATUSES:
            simulated[key] = int(status)
    return simulated


def configure_cache(settings: Settings) -> None:
    """Enable LiteLLM's built-in in-memory cache.

    (LiteLLM >= 1.110 no longer ships a disk backend; in-memory is allowed by the spec.)
    """
    litellm.cache = None
    if settings.cache_enabled:
        litellm.enable_cache(type="local")


class LLMGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        # key ids follow the order of LLM_API_KEYS: key_1, key_2, ...
        self.keys: list[tuple[str, str]] = [
            (f"key_{i}", key) for i, key in enumerate(settings.llm_api_keys, start=1)
        ]
        self._order = {key_id: index for index, (key_id, _) in enumerate(self.keys)}
        litellm.max_fallbacks = max(5, len(self.keys))
        configure_cache(settings)
        self._router = self._build_router({}) if self.keys else None

    @property
    def keys_configured(self) -> int:
        return len(self.keys)

    def _build_router(self, simulate: dict[str, int]) -> Router:
        """One deployment per key. key_N falls back to key_N+1, key_N+2, ... in order.

        ``simulate`` makes the listed keys raise the given HTTP status for this router
        only, so a simulated request never touches the real router's cooldown state.
        """
        model_list = []
        for key_id, api_key in self.keys:
            params: dict[str, Any] = {
                "model": self.settings.llm_model,
                "api_key": api_key,
                "timeout": self.settings.llm_timeout_seconds,
            }
            forced = simulate.get(key_id, simulate.get("*"))
            if forced is not None:
                params["mock_response"] = SimulatedProviderError(forced)
            model_list.append(
                {"model_name": key_id, "litellm_params": params, "model_info": {"id": key_id}}
            )
        ids = [key_id for key_id, _ in self.keys]
        fallbacks = [{ids[i]: ids[i + 1:]} for i in range(len(ids) - 1)]
        return Router(
            model_list=model_list,
            fallbacks=fallbacks,
            num_retries=0,
            cooldown_time=self.settings.key_cooldown_seconds,
            allowed_fails=0,
            cache_responses=self.settings.cache_enabled,
        )

    def _redact(self, text: str) -> str:
        """Provider errors can echo request URLs; never let a raw key reach the logs."""
        for _, api_key in self.keys:
            if api_key:
                text = text.replace(api_key, "***")
        return text

    async def warm_up(self) -> None:
        """Exercise the cache-hit path once at startup using LiteLLM's mock mode.

        LiteLLM lazily imports a few modules the first time a cached response is
        served; doing it here keeps the first real cache hit fast. No network is used.
        """
        if not self.settings.cache_enabled:
            return
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [{"role": "user", "content": "__warm_up__"}],
            "mock_response": "ok",
            "caching": True,
            "api_key": "warm-up",
        }
        try:
            await litellm.acompletion(**kwargs)
            await asyncio.sleep(0.1)  # cache writes complete in the background
            await litellm.acompletion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - warm-up must never block startup
            logger.warning("cache warm-up skipped: %s", exc)

    async def complete(
        self, messages: list[dict[str, str]], simulate: dict[str, int] | None = None
    ) -> LLMResult:
        simulate = simulate or {}
        if not self.keys:
            raise AllProvidersUnavailable()

        router = self._build_router(simulate) if simulate else self._router
        kwargs: dict[str, Any] = {"caching": self.settings.cache_enabled}
        if simulate:
            # A simulated request must show a real, live answer from a surviving key,
            # so it skips the cache lookup.
            kwargs["cache"] = {"no-cache": True}

        try:
            # Always start at key_1; the Router walks the fallback chain on 429 / quota / 5xx.
            response = await router.acompletion(
                model=self.keys[0][0], messages=messages, **kwargs
            )
            reply = (response.choices[0].message.content or "").strip()
            if not reply:
                raise ValueError("provider returned an empty reply")
        except Exception as exc:  # noqa: BLE001 - never leak provider errors to the client
            logger.warning("all keys failed (%s: %s)", type(exc).__name__, self._redact(str(exc))[:300])
            raise AllProvidersUnavailable() from exc

        hidden = getattr(response, "_hidden_params", None) or {}
        cache_hit = bool(hidden.get("cache_hit"))
        answered_by = hidden.get("model_id")
        key_id = None if cache_hit else answered_by
        # Keys earlier in the pool were tried first and failed (or were cooling down).
        attempts = 0 if cache_hit else self._order.get(answered_by, 0)

        input_tokens, output_tokens = self._usage(response, messages, reply)
        return LLMResult(
            reply=reply,
            model=self.settings.llm_model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=cache_hit,
            key_id=key_id,
            fallback_attempts=attempts,
        )

    def _usage(self, response: Any, messages: list[dict[str, str]], reply: str) -> tuple[int, int]:
        usage = getattr(response, "usage", None)
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        model = self.settings.llm_model
        if prompt is None:
            prompt = litellm.token_counter(model=model, messages=messages)
        if completion is None:
            completion = litellm.token_counter(model=model, text=reply)
        return int(prompt), int(completion)
