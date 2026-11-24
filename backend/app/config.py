"""All configuration comes from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str = "") -> list[str]:
    return [p.strip() for p in os.getenv(name, default).split(",") if p.strip()]


def _sqlite_path(url: str) -> str:
    """sqlite:////data/app.db -> /data/app.db ; sqlite:///./app.db -> ./app.db"""
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("DATABASE_URL must look like sqlite:////data/app.db")
    return url[len(prefix):] or "app.db"


@dataclass(frozen=True)
class KeySlot:
    key_id: str   # key_1, key_2, ... (safe to expose)
    api_key: str  # secret, never returned to clients
    model: str    # LiteLLM model string used with this key


@dataclass
class Settings:
    database_path: str
    keys: list[KeySlot]
    allowed_origins: list[str]
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_type: str          # "local" (in-memory) or "disk"
    cache_dir: str
    cache_ttl_seconds: int
    request_timeout_seconds: float
    history_limit: int
    mock_llm: bool           # offline mode for tests / demos without real keys
    max_output_tokens: int = 1024
    models: list[str] = field(default_factory=list)


def load_settings() -> Settings:
    api_keys = _list("LLM_API_KEYS")
    default_model = os.getenv("LLM_MODEL", "gemini/gemini-2.5-flash").strip()
    # Optional: one model per key (same order) to spread the pool across models.
    per_key_models = _list("LLM_MODELS")
    keys = [
        KeySlot(
            key_id=f"key_{i + 1}",
            api_key=k,
            model=per_key_models[i] if i < len(per_key_models) else default_model,
        )
        for i, k in enumerate(api_keys)
    ]
    models: list[str] = []
    for slot in keys:
        if slot.model not in models:
            models.append(slot.model)

    return Settings(
        database_path=_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///./app.db")),
        keys=keys,
        allowed_origins=_list("ALLOWED_ORIGINS", "http://localhost:3000"),
        enable_failure_simulation=_bool("ENABLE_FAILURE_SIMULATION"),
        default_system_prompt=os.getenv(
            "DEFAULT_SYSTEM_PROMPT", "You are a helpful, concise assistant."
        ),
        cache_type=os.getenv("CACHE_TYPE", "local").strip().lower(),
        cache_dir=os.getenv("CACHE_DIR", "/data/llm_cache"),
        cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "86400")),
        request_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        history_limit=int(os.getenv("HISTORY_LIMIT", "40")),
        mock_llm=_bool("MOCK_LLM"),
        max_output_tokens=int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1024")),
        models=models,
    )
