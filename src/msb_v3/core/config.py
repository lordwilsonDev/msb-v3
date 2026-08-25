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
    llama_cpp_url: str = field(default_factory=lambda: os.getenv("LLAMA_CPP_URL", "http://127.0.0.1:8081"))
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
    # Evidence Spine: decision-level, causally linked provenance records
    # cross-referencing the audit chain. General
    # (not Vesta-specific) so it lives under data/evidence/.
    decision_spine_db_path: str = field(default_factory=lambda: os.getenv("MSB_DECISION_SPINE_DB_PATH", "data/evidence/decision_spine.db"))
    # Bearer key for the OpenAI-compatible /v1 adapter (Open WebUI etc.).
    # Empty = adapter closed (503) until configured — fail-closed.
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    # Frontier seam for the hybrid model router: the /v1 adapter URL + the
    # model id used for long-horizon plan/verify-synth work. The
    # seam is "closed" (router degrades to local) until OPENAI_API_KEY is set.
    openai_frontier_url: str = field(default_factory=lambda: os.getenv("OPENAI_FRONTIER_URL", "http://127.0.0.1:8766/v1"))
    openai_frontier_model: str = field(default_factory=lambda: os.getenv("OPENAI_FRONTIER_MODEL", "frontier"))
    # DeepSeek native API (OpenAI-compatible) — the first frontier provider
    # behind the AgentProvider ABC. DEEPSEEK_API_KEY falls back to
    # OPENAI_API_KEY so the /v1 seam and this provider can share one key.
    deepseek_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""))
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
    deepseek_model: str = field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"))
    # Anthropic native Messages API (the api.anthropic provider). Strictly
    # ANTHROPIC_API_KEY — no fallback to OPENAI_API_KEY: unlike DeepSeek,
    # Anthropic's wire protocol is not OpenAI-compatible, so sharing the key
    # would fail confusingly at the HTTP layer, not degrade gracefully.
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_base_url: str = field(default_factory=lambda: os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"))
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"))
    # DeepSeek Harness (dsh) — the plugin-based agent harness from DeepSeek AI,
    # governed as a bounded subprocess behind DshAgentProvider (kind "dsh").
    # DSH_BINARY may be a single executable ("dsh") or a space-separated prefix
    # ("npx @deepseek-ai/dsh"); it is resolved with shlex, so quoting works.
    # Unset or unresolvable = provider unavailable (fail-closed).
    dsh_binary: str = field(default_factory=lambda: os.getenv("DSH_BINARY", "dsh"))
    dsh_profile: str = field(default_factory=lambda: os.getenv("DSH_PROFILE", "headless"))
    dsh_timeout_s: float = field(default_factory=lambda: float(os.getenv("DSH_TIMEOUT_S", "600")))
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
    # --- Cron scheduler (the heartbeat) ---
    # In-process scheduler: the FastAPI lifespan starts a background loop
    # when enabled (MSB_CRON_ENABLED=0 disables; the CLI still runs jobs on
    # demand). Tests disable it via the suite-wide autouse fixture.
    cron_enabled: bool = field(default_factory=lambda: os.getenv("MSB_CRON_ENABLED", "1") == "1")
    # Durable job definitions + run history, following the runtime-store
    # convention (beside runtime.db / tasks.db under data/runtime/). Empty =
    # derive from settings.db_path at use-site (MSB_DB_PATH moves them
    # together); MSB_CRON_DB_PATH still overrides.
    cron_db_path: str = field(default_factory=lambda: os.getenv("MSB_CRON_DB_PATH", ""))
    # How often the scheduler loop wakes to check for due jobs.
    cron_tick_s: int = field(default_factory=lambda: int(os.getenv("MSB_CRON_TICK_S", "15")))
    # Run history retained per job (older rows pruned on each run).
    cron_history_keep: int = field(default_factory=lambda: int(os.getenv("MSB_CRON_HISTORY_KEEP", "100")))
    # Host allowlist for the http_call action — fail-closed: a URL whose host
    # is not on this list is refused. Defaults to loopback only; widen by
    # adding hosts (comma-separated, no scheme/port).
    cron_http_hosts: str = field(default_factory=lambda: os.getenv("MSB_CRON_HTTP_HOSTS", "127.0.0.1,localhost,::1"))
    # --- Wake loop (the 5-minute resident agent) ---
    # A cron job (wake-agent, schedule below) wakes the resident agent to
    # process messages left in the wake inbox from any session; responses
    # land in the outbox (see api/wake.py + docs/wake-loop.md). Enabled by
    # default; the job is seeded on server start (app.py lifespan) when both
    # this and cron_enabled are true.
    wake_enabled: bool = field(default_factory=lambda: os.getenv("MSB_WAKE_ENABLED", "1") == "1")
    # Durable inbox/outbox store, following the runtime-store convention
    # (beside cron.db under data/runtime/). Empty = derive from db_path.
    wake_db_path: str = field(default_factory=lambda: os.getenv("MSB_WAKE_DB_PATH", ""))
    # How many pending messages one wake cycle processes (bounded — the
    # cycle is a governed cron action with a timeout).
    wake_max_per_run: int = field(default_factory=lambda: int(os.getenv("MSB_WAKE_MAX_PER_RUN", "5")))
    # The resident cadence.
    wake_schedule: str = field(default_factory=lambda: os.getenv("MSB_WAKE_SCHEDULE", "*/5 * * * *"))
    # --- Automation brain (n8n / Make / Zapier / GoHighLevel) ---
    # The brain (DeepSeek-driven) turns a request into a structured plan and
    # executes it via the provider clients. Budget cap in USD on the LLM
    # brain spend (the $10 key); platform per-run costs are the provider's
    # own billing and are recorded in the manifest when known.
    automation_budget_usd: float = field(default_factory=lambda: float(os.getenv("MSB_AUTOMATION_BUDGET_USD", "10.0")))
    # Fail-closed: dry-run by default. Creation with side effects requires
    # approve=true on the request (operator token = the approval) or this
    # flipped to 0.
    automation_dry_run: bool = field(default_factory=lambda: os.getenv("MSB_AUTOMATION_DRY_RUN", "1") == "1")
    automation_manifest_path: str = field(default_factory=lambda: os.getenv("MSB_AUTOMATION_MANIFEST_PATH", ""))
    # The /hook sense (Stage 3): optional shared secret for inbound webhook
    # payloads (x-hook-secret header, constant-time compare). Empty = open
    # to bounded payloads only — the brain judges, the edge is small.
    automation_hook_secret: str = field(default_factory=lambda: os.getenv("MSB_AUTOMATION_HOOK_SECRET", ""))
    # Dispatcher outbound allowlist (Stage 1): hosts living automations may
    # POST to beyond loopback + the configured providers' own hosts
    # (comma-separated; empty = loopback + providers only).
    automation_webhook_hosts: str = field(default_factory=lambda: os.getenv("MSB_AUTOMATION_WEBHOOK_HOSTS", ""))
    # n8n is the first-class target: self-hosted, free to run, real REST API.
    # N8N_API_KEY is created in the n8n UI (Settings → API).
    n8n_api_key: str = field(default_factory=lambda: os.getenv("N8N_API_KEY", ""))
    n8n_base_url: str = field(default_factory=lambda: os.getenv("N8N_BASE_URL", "http://127.0.0.1:5678"))
    # Make: the practical integration is a webhook trigger (scenario creation
    # needs account-level API access). Zapier / GoHighLevel: REST API keys.
    make_webhook_url: str = field(default_factory=lambda: os.getenv("MSB_MAKE_WEBHOOK_URL", ""))
    zapier_api_key: str = field(default_factory=lambda: os.getenv("MSB_ZAPIER_API_KEY", ""))
    ghl_api_key: str = field(default_factory=lambda: os.getenv("MSB_GHL_API_KEY", ""))
    # GoHighLevel location id — PIT (private integration token) access is
    # location-scoped; sent as locationId on workflow creation when set.
    ghl_location_id: str = field(default_factory=lambda: os.getenv("MSB_GHL_LOCATION_ID", ""))
    ghl_base_url: str = field(default_factory=lambda: os.getenv("MSB_GHL_BASE_URL", "https://services.leadconnectorhq.com"))
    # --- Autonomous repair (Phase 4) ---
    # The bounded self-repair loop (launchd: com.blackswanlabz.msb-v3.auto-repair,
    # every 10 min): scan → diagnose → propose → execute AUTO plans. Disabled =
    # the loop exits without proposing or executing (the launchd script and the
    # loop itself both check this — belt and braces).
    auto_repair_enabled: bool = field(default_factory=lambda: os.getenv("MSB_AUTO_REPAIR_ENABLED", "1") == "1")
    # Per-cycle cap on AUTO executions — bounded by construction even when
    # many plans are open (deferred plans retry on later cycles).
    auto_repair_max_execute: int = field(default_factory=lambda: int(os.getenv("MSB_AUTO_REPAIR_MAX_EXECUTE", "3")))
    _active_backend: str = field(default_factory=lambda: os.getenv("MSB_ACTIVE_BACKEND", "ollama"))


settings = Settings()
