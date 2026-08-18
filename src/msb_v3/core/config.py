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
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen3:8b"))
    # Absolute by default (msb_home-relative): a CWD-relative default would
    # scatter the audit chain and DBs under whatever directory a CLI or
    # script happens to be run from instead of the deployment. MSB_DB_PATH
    # still overrides.
    db_path: str = field(default_factory=lambda: os.getenv("MSB_DB_PATH") or str(_REPO_ROOT / "data" / "msb_v3.db"))
    log_level: str = field(default_factory=lambda: os.getenv("MSB_LOG_LEVEL", "info"))
    # Canonical evidence-receipt event stream (one JSON line per handle()
    # cycle). Absolute by default (msb_home-relative) so the stream lands in
    # the deployment's logs/, not whatever CWD a script runs from.
    # MSB_AUDIT_LOG_PATH overrides (the cockpit tail + tests point it at a
    # temp file).
    audit_log_path: str = field(default_factory=lambda: os.getenv("MSB_AUDIT_LOG_PATH") or str(_REPO_ROOT / "logs" / "audit.jsonl"))
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
    # Sovereign Node: dedicated state, sandbox, pairing, and replay controls.
    node_db_path: str = field(default_factory=lambda: os.getenv("MSB_NODE_DB_PATH", "data/node/node.db"))
    node_audit_db_path: str = field(default_factory=lambda: os.getenv("MSB_NODE_AUDIT_DB_PATH", "data/node/audit_chain.db"))
    node_sandbox_root: str = field(default_factory=lambda: os.getenv("MSB_NODE_SANDBOX_ROOT", "runtime/node-sandbox"))
    node_pairing_code: str = field(default_factory=lambda: os.getenv("MSB_NODE_PAIRING_CODE", ""))
    node_session_ttl_s: int = field(default_factory=lambda: int(os.getenv("MSB_NODE_SESSION_TTL_S", "900")))
    node_clock_skew_s: int = field(default_factory=lambda: int(os.getenv("MSB_NODE_CLOCK_SKEW_S", "60")))
    node_max_read_bytes: int = field(default_factory=lambda: int(os.getenv("MSB_NODE_MAX_READ_BYTES", "1048576")))
    # Vesta transport admission. Disabled for local development; production
    # enables it after the WireGuard interface and firewall are verified.
    vesta_require_tunnel: bool = field(default_factory=lambda: os.getenv("MSB_VESTA_REQUIRE_TUNNEL", "0") == "1")
    vesta_allowed_cidrs: str = field(default_factory=lambda: os.getenv("MSB_VESTA_ALLOWED_CIDRS", "127.0.0.1/32,::1/128"))
    vesta_task_db_path: str = field(default_factory=lambda: os.getenv("MSB_VESTA_TASK_DB_PATH", "data/vesta/tasks.db"))
    vesta_evidence_root: str = field(default_factory=lambda: os.getenv("MSB_VESTA_EVIDENCE_ROOT", "runtime/vesta-evidence"))
    vesta_evidence_db_path: str = field(default_factory=lambda: os.getenv("MSB_VESTA_EVIDENCE_DB_PATH", "data/vesta/evidence.db"))
    vesta_shell_timeout_s: float = field(default_factory=lambda: float(os.getenv("MSB_VESTA_SHELL_TIMEOUT_S", "10.0")))
    vesta_shell_max_output_bytes: int = field(default_factory=lambda: int(os.getenv("MSB_VESTA_SHELL_MAX_OUTPUT_BYTES", "65536")))
    # Evidence Spine (completion blueprint Phase 2): decision-level, causally
    # linked provenance records cross-referencing the audit chain. General
    # (not Vesta-specific) so it lives under data/evidence/.
    decision_spine_db_path: str = field(default_factory=lambda: os.getenv("MSB_DECISION_SPINE_DB_PATH", "data/evidence/decision_spine.db"))
    # Bearer key for the OpenAI-compatible /v1 adapter (Open WebUI etc.).
    # Empty = adapter closed (503) until configured — fail-closed.
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    # Frontier seam for the hybrid model router (Phase 2): the /v1 adapter
    # URL + the model id used for long-horizon plan/verify-synth work. The
    # seam is "closed" (router degrades to local) until OPENAI_API_KEY is set.
    openai_frontier_url: str = field(default_factory=lambda: os.getenv("OPENAI_FRONTIER_URL", "http://127.0.0.1:8766/v1"))
    openai_frontier_model: str = field(default_factory=lambda: os.getenv("OPENAI_FRONTIER_MODEL", "frontier"))
    # /v1/embeddings guards: per-request batch cap (413 when exceeded) and a
    # per-client sliding-window cap on total embedded items (429). A batch of
    # N items consumes N units toward the window cap.
    openai_embed_max_batch: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_MAX_BATCH", "32")))
    openai_embed_rate_max: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_RATE_MAX", "120")))
    openai_embed_rate_window_s: int = field(default_factory=lambda: int(os.getenv("OPENAI_EMBED_RATE_WINDOW_S", "60")))
    # /v1/chat/completions guard: per-client sliding-window cap on requests
    # (1 unit per request, streaming included). Mirrors the embeddings rate
    # guard; 0 denies all (fail-closed).
    openai_chat_rate_max: int = field(default_factory=lambda: int(os.getenv("OPENAI_CHAT_RATE_MAX", "120")))
    openai_chat_rate_window_s: int = field(default_factory=lambda: int(os.getenv("OPENAI_CHAT_RATE_WINDOW_S", "60")))
    # Paseo execution surface (unified-architecture §7): the daemon's
    # agent-management MCP endpoint (Streamable HTTP, default 127.0.0.1:6767
    # per Paseo's config.ts DEFAULT_PORT). MSB_PASEO_URL overrides; the
    # adapter is inert (reports FAILED) when the daemon is unreachable.
    paseo_url: str = field(default_factory=lambda: os.getenv("MSB_PASEO_URL", "http://127.0.0.1:6767/mcp/agents"))
    # Operator-gated permission decisions: a Paseo agent's permission request
    # parks the run until an operator decides within this TTL; after that the
    # run is interrupted and the task fails (never silently completes).
    paseo_permission_ttl_s: int = field(default_factory=lambda: int(os.getenv("MSB_PASEO_PERMISSION_TTL_S", "900")))
    # Upper bound on how long a Paseo worker may run before MSB interrupts it.
    paseo_run_timeout_s: float = field(default_factory=lambda: float(os.getenv("MSB_PASEO_RUN_TIMEOUT_S", "600.0")))
    # Code Graph subsystem (sovereign-architecture §4.2.1, P0): SQLite
    # graph store per repo. Code-graph indexes are derived data — safe to
    # rebuild, but kept on disk so queries stay <1s (validation gate G1).
    codegraph_db_path: str = field(default_factory=lambda: os.getenv("MSB_CODEGRAPH_DB_PATH", "data/codegraph/graph.db"))
    # Memory Fabric (sovereign-architecture §4.2.2, P0): SQLite store for
    # provenance-tracked agent memory (types, verification states, decay).
    # Separate from the message-history DB — the fabric is durable
    # cross-session memory, not a conversation log.
    memory_fabric_db_path: str = field(default_factory=lambda: os.getenv("MSB_MEMORY_FABRIC_DB_PATH", "data/memory_fabric/memory.db"))
    # --- Governance brakes (Phase 0B) ---
    # Budget caps per rolling window; -1 = unlimited, 0 = deny all (fail-closed).
    gov_budget_research_calls: int = field(default_factory=lambda: int(os.getenv("GOV_BUDGET_RESEARCH_CALLS", "50")))
    gov_budget_tokens: int = field(default_factory=lambda: int(os.getenv("GOV_BUDGET_TOKENS", "200000")))
    gov_budget_iterations: int = field(default_factory=lambda: int(os.getenv("GOV_BUDGET_ITERATIONS", "100")))
    gov_budget_window_min: int = field(default_factory=lambda: int(os.getenv("GOV_BUDGET_WINDOW_MIN", "1440")))
    # Ouroboros governor thresholds (convergence enforced, not requested).
    gov_governor_stall_limit: int = field(default_factory=lambda: int(os.getenv("GOV_GOVERNOR_STALL_LIMIT", "6")))
    gov_governor_novelty_min: float = field(default_factory=lambda: float(os.getenv("GOV_GOVERNOR_NOVELTY_MIN", "0.05")))
    gov_governor_dup_ratio_halt: float = field(default_factory=lambda: float(os.getenv("GOV_GOVERNOR_DUP_RATIO_HALT", "0.5")))
    gov_governor_history: int = field(default_factory=lambda: int(os.getenv("GOV_GOVERNOR_HISTORY", "20")))
    _active_backend: str = field(default_factory=lambda: os.getenv("MSB_ACTIVE_BACKEND", "ollama"))


settings = Settings()
