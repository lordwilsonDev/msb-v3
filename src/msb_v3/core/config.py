"""Core config — env-var-first, no pydantic-settings dependency."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Repo root: MSB_HOME env wins, then the shell convention MSB_REPO (CI sets
# this), then derived from this file's location (src/msb_v3/core/config.py ->
# parents[3] = repo root). Everything repo-relative in the app reads
# settings.msb_home, so the repo is portable to any checkout path.
_REPO_ROOT = Path(os.getenv("MSB_HOME") or os.getenv("MSB_REPO") or str(Path(__file__).resolve().parents[3]))

# Vault root is user data, not repo data, so it derives from the home dir
# (MSB_VAULT_PATH overrides). Same default tenants.py has always used.
_VAULT_ROOT = Path(os.getenv("MSB_VAULT_PATH") or str(Path.home() / "Documents" / "Vault"))


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("MSB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("MSB_PORT", "8766")))
    reload: bool = field(default_factory=lambda: os.getenv("MSB_RELOAD", "0") == "1")
    reasoning_scorer: bool = field(default_factory=lambda: os.getenv("MSB_REASONING_SCORER", "1") == "1")
    msb_home: str = field(default_factory=lambda: str(_REPO_ROOT))
    vault_path: str = field(default_factory=lambda: str(_VAULT_ROOT))
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "deepseek-r1:1.5b"))
    db_path: str = field(default_factory=lambda: os.getenv("MSB_DB_PATH", "data/msb_v3.db"))
    log_level: str = field(default_factory=lambda: os.getenv("MSB_LOG_LEVEL", "info"))
    cors_origins: str = field(default_factory=lambda: os.getenv("MSB_CORS_ORIGINS", "*"))
    request_timeout_s: float = field(default_factory=lambda: float(os.getenv("MSB_REQUEST_TIMEOUT_S", "60.0")))
    llama_cpp_url: str = field(default_factory=lambda: os.getenv("LLAMA_CPP_URL", "http://127.0.0.1:8080"))
    # Home-derived (user model files live outside the repo); LLAMA_CPP_MODEL overrides.
    llama_cpp_model: str = field(
        default_factory=lambda: os.getenv("LLAMA_CPP_MODEL", str(Path.home() / "models" / "gemma-4-12b-it" / "gemma-4-12b-it-q4_k_m.gguf"))
    )
    # NotebookLM active cluster index (user data, home-derived); NOTEBOOKLM_ACTIVE_INDEX overrides.
    notebooklm_active_index: str = field(
        default_factory=lambda: os.getenv("NOTEBOOKLM_ACTIVE_INDEX", str(Path.home() / "notebooklm-library-deep-dive" / "active-index.json"))
    )
    tavily_api_key: str = field(default_factory=lambda: os.getenv("TAVILY_API_KEY", ""))
    operator_token: str = field(default_factory=lambda: os.getenv("MSB_OPERATOR_TOKEN", ""))
    # Bearer key for the OpenAI-compatible /v1 adapter (Open WebUI etc.).
    # Empty = adapter closed (503) until configured — fail-closed.
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    # /v1/embeddings guards: per-request batch cap (413 when exceeded) and a
    # per-client sliding-window cap on total embedded items (429). A batch of
    # N items consumes N units toward the window cap.
    openai_embed_max_batch: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_MAX_BATCH", "32")))
    openai_embed_rate_max: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_RATE_MAX", "120")))
    openai_embed_rate_window_s: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_RATE_WINDOW_S", "60")))
    _active_backend: str = field(default_factory=lambda: os.getenv("MSB_ACTIVE_BACKEND", "ollama"))


settings = Settings()
