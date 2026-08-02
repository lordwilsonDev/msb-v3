"""Core config — env-var-first, no pydantic-settings dependency."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("MSB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("MSB_PORT", "8766")))
    reload: bool = field(default_factory=lambda: os.getenv("MSB_RELOAD", "0") == "1")
    reasoning_scorer: bool = field(default_factory=lambda: os.getenv("MSB_REASONING_SCORER", "1") == "1")
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "deepseek-r1:1.5b"))
    db_path: str = field(default_factory=lambda: os.getenv("MSB_DB_PATH", "data/msb_v3.db"))
    log_level: str = field(default_factory=lambda: os.getenv("MSB_LOG_LEVEL", "info"))
    cors_origins: str = field(default_factory=lambda: os.getenv("MSB_CORS_ORIGINS", "*"))
    request_timeout_s: float = field(default_factory=lambda: float(os.getenv("MSB_REQUEST_TIMEOUT_S", "60.0")))
    llama_cpp_url: str = field(default_factory=lambda: os.getenv("LLAMA_CPP_URL", "http://127.0.0.1:8080"))
    llama_cpp_model: str = field(default_factory=lambda: os.getenv("LLAMA_CPP_MODEL", "/Users/lordwilson/models/gemma-4-12b-it/gemma-4-12b-it-q4_k_m.gguf"))
    _active_backend: str = field(default_factory=lambda: os.getenv("MSB_ACTIVE_BACKEND", "ollama"))


settings = Settings()
