"""LiteLLM gateway: response cache + ordered key-pool failover.

Flow for one chat request:
  1. Build the full message list (system prompt + history + new message).
  2. Look it up in LiteLLM's cache (key = model + full message list).
     Hit  -> LLM_CACHE, stored reply + stored usage, key_id = null.
  3. Miss -> try key_1, key_2, ... in order via litellm.acompletion.
     429 / quota / 5xx / timeout / auth error on a key -> next key.
     First success -> LIVE_INFERENCE, written to the cache.
  4. Every key failed -> AllProvidersUnavailable (mapped to HTTP 503).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import litellm
from litellm.caching.caching import Cache

from .config import KeySlot, Settings
from .simulation import simulated_status

log = logging.getLogger("gateway")

litellm.suppress_debug_info = True
litellm.drop_params = True  # ignore params a given provider doesn't support

LLM_CACHE = "LLM_CACHE"
LIVE_INFERENCE = "LIVE_INFERENCE"


class AllProvidersUnavailable(Exception):
    def __init__(self, attempts: list[dict[str, Any]]):
        super().__init__("all providers unavailable")
        self.attempts = attempts


class SimulatedProviderError(Exception):
    def __init__(self, key_id: str, status_code: int):
        super().__init__(f"simulated HTTP {status_code} on {key_id}")
        self.status_code = status_code


@dataclass
class Completion:
    reply: str
    resolution_source: str
    model: str
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


def build_cache(settings: Settings) -> Cache:
    if settings.cache_type == "disk":
        return Cache(type="disk", disk_cache_dir=settings.cache_dir, ttl=settings.cache_ttl_seconds)
    return Cache(type="local", ttl=settings.cache_ttl_seconds)


def _status_of(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


class LLMGateway:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.keys: list[KeySlot] = settings.keys
        self.cache = build_cache(settings)

    # ---- cache ----------------------------------------------------------
    def _cache_key(self, model: str, messages: list[dict[str, str]]) -> str:
        return self.cache.get_cache_key(model=model, messages=messages)

    async def _cache_get(self, model: str, messages: list[dict[str, str]]) -> dict | None:
        try:
            value = await self.cache.async_get_cache(cache_key=self._cache_key(model, messages))
        except Exception:  # cache must never break a request
            log.exception("cache read failed")
            return None
        if isinstance(value, dict) and value.get("reply"):
            return value
        return None

    async def _cache_set(self, model: str, messages: list[dict[str, str]], value: dict) -> None:
        try:
            await self.cache.async_add_cache(value, cache_key=self._cache_key(model, messages))
        except Exception:
            log.exception("cache write failed")

    # ---- provider call --------------------------------------------------
    async def _call(self, slot: KeySlot, messages: list[dict[str, str]]) -> tuple[str, int, int]:
        kwargs: dict[str, Any] = dict(
            model=slot.model,
            messages=messages,
            api_key=slot.api_key,
            timeout=self.settings.request_timeout_seconds,
            max_tokens=self.settings.max_output_tokens,
            num_retries=0,  # failover is handled across keys, not by retrying one key
        )
        if self.settings.mock_llm:
            last = messages[-1]["content"]
            turns = sum(1 for m in messages if m["role"] == "user")
            kwargs["mock_response"] = f"[mock reply #{turns}] You said: {last}"

        response = await litellm.acompletion(**kwargs)
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("empty completion")  # treat as provider failure

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        if self.settings.mock_llm or not input_tokens:
            input_tokens = litellm.token_counter(model=slot.model, messages=messages)
            output_tokens = litellm.token_counter(model=slot.model, text=text, count_response_tokens=True)
        return text, input_tokens, output_tokens

    # ---- public ---------------------------------------------------------
    async def complete(
        self,
        messages: list[dict[str, str]],
        simulate: dict[str, int] | None = None,
    ) -> Completion:
        simulate = simulate or {}

        # 1) cache. A simulated-failure request skips the cache *read* so the
        #    failover path is always exercised (it still writes on success).
        if not simulate:
            for model in self.settings.models:
                hit = await self._cache_get(model, messages)
                if hit:
                    return Completion(
                        reply=hit["reply"],
                        resolution_source=LLM_CACHE,
                        model=hit.get("model", model),
                        input_tokens=int(hit.get("input_tokens", 0)),
                        output_tokens=int(hit.get("output_tokens", 0)),
                        key_id=None,
                        fallback_attempts=0,
                    )

        # 2) key pool, in order
        attempts: list[dict[str, Any]] = []
        for slot in self.keys:
            try:
                forced = simulated_status(simulate, slot.key_id)
                if forced is not None:
                    raise SimulatedProviderError(slot.key_id, forced)
                text, in_tok, out_tok = await self._call(slot, messages)
            except Exception as exc:  # noqa: BLE001 - any provider failure -> next key
                status = _status_of(exc)
                attempts.append({"key_id": slot.key_id, "status": status, "error": type(exc).__name__})
                log.warning("key %s failed (%s %s), failing over", slot.key_id, status, type(exc).__name__)
                continue

            await self._cache_set(
                slot.model,
                messages,
                {"reply": text, "model": slot.model, "input_tokens": in_tok, "output_tokens": out_tok},
            )
            return Completion(
                reply=text,
                resolution_source=LIVE_INFERENCE,
                model=slot.model,
                input_tokens=in_tok,
                output_tokens=out_tok,
                key_id=slot.key_id,
                fallback_attempts=len(attempts),
            )

        raise AllProvidersUnavailable(attempts)
