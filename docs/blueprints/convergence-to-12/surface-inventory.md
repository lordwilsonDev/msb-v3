# MSB v3 — Surface Inventory (M0: wire / cut / park)

**Owner:** Wilson · **Inventoried:** 2026-08-16 · **Method:** AST import-graph scan of `src/` (199 modules) + grep for stub/placeholder/deferred markers + wiring verification against `api/app.py` router mounts and `scripts/`.

Every item below has exactly one destination (Blueprint Rule 1):

- **WIRE** — implemented but not on the live path; mount/connect it.
- **CUT** — remove from the shipping surface (or fold into a live equivalent).
- **PARK** — kept with a dated decision, owner, and explicit reason (allowed under Rule 1); live stubs parked behind a fail-closed gate.

## Shipping-surface items

| # | Item | Location | Evidence | Decision | Note |
|---|---|---|---|---|---|
| S1 | `api/status.py` — complete `/status` router | `src/msb_v3/api/status.py` | Zero importers; not in `app.py` router mounts (only `/system/info` is mounted) | **CUT or WIRE** | Complete code, dead route. Either mount it or fold `/status` into `/system/info` and delete. Decide in M3. |
| S2 | `api/tenant_chat.py` — placeholder | `src/msb_v3/api/tenant_chat.py` | Gate test (`tests/api/test_tenant_chat_gate.py`) asserts it is NOT mounted; docstring dated 2026-08-15 | **PARK** | Dated decision + explicit reason (Phase 1 hardening). Revisit in M3: implement narrow contract or cut. |
| S3 | `core/health.py` — health subsystem | `src/msb_v3/core/health.py` | Only `tests/test_runtime_boot.py` references it; live health is `api/health.py` | **CUT or PARK** | Complete, tested, zero runtime callers — likely a pre-dating duplicate of `api/health`. Decide in M3. |
| S4 | `core/runtime_config.py` + `core/runtime.yaml` | `src/msb_v3/core/` | Only tests reference it (`runtime_boot`, `chaos/phase1/phase2`); `config.py` (the real config) does not use it | **CUT or PARK** | Test-only config loader. If tests need it, park with a note; if the YAML is aspirational, cut. |
| S5 | Multimodal interfaces (`VisionClaw` / `HapticHeartbeat` / `SpeechFunctions`) | `src/msb_v3/triumvirate/multimodal_interfaces.py` | All methods return `status: "stub"`; `/triumvirate/multimodal/*` routes 503 unless `MSB_MULTIMODAL_ENABLED=1`; both sides tested | **PARK (flag-gated)** | This is the blueprint's flagship stub. Already outside the default release path and fail-closed. M3 decision: implement a narrow real contract (screen capture / transcription) or move to `experiments/` + architecture note. |
| S6 | `personal_intelligence/` — skill-discovery surface | `src/personal_intelligence/` | Module docstring: deferred, not adopted by the live runtime; has its own test dir | **PARK** | Dated decision documented in the module. Keep out of the release path. |
| S7 | `uac/stage_0_knowledge_acquisition.py` + `uac/transcript_requirements_extractor.py` | `src/msb_v3/uac/` | Only their own tests import them; `uac/models.py` references only a default `author` string | **PARK** | Implemented + tested but unwired ("stage 0" research pipeline). Needs a wiring decision or explicit park note in M3. |
| S8 | Flywheel `StubCharger` default | `src/msb_v3/flywheel/` | Wired (router + container); default charger is `stub` (offline deterministic), `sovereign` is the real-LLM alternative | **WIRE (already) — documented degraded default** | Honest fallback, not a lie: `StubScanner` reports `papers_scanned: 0`. Keep; document the default in the README claims pass (M3). |
| S9 | `device/client.py` — node.v1 device CLI | `src/msb_v3/device/client.py` | Wired via `scripts/device-client.py` (imports `msb_v3.device.client.main`) | **WIRE (verified live)** | Real signed-session client; not an orphan — CLI entry point. |
| S10 | Conversation `StubModel` | `src/msb_v3/conversation/envelope.py` | Deliberate deterministic stub MODEL (`MSB_CONVERSATION_MODEL=stub`), resolved through the container | **WIRE (verified live)** | Documented design (deterministic fallback), not an unfinished stub. |
| S11 | Governance `capability_catalog` "deferred" phase | `src/msb_v3/vesta/policy.py` | Honest catalog: `known` + `enabled` + `phase` (`0-2`/`G`/`deferred`) | **VERIFIED-live** | "Deferred" labels known-but-disabled capabilities — not an implied future; ensure README does not advertise them (M3). |

## Verified-live (no action)

Evidence spine (`evidence/`), replay (`replay/`), governed tool loop (`agent/handle.py`), audit chain + anchor + notary + RFC 3161 (`uac/`), signing backends (`uac/signing.py`), factory + diverse-LLM reviewer panel (`factory/`, `moie/`), MCP bridge + stdio adapter (`api/mcp_bridge.py`, root `mcp_adapter.py`), codegraph, RAG/vector store, Vesta services, sovereign node, n8n/Make bridge workflows (external).

## Repo hygiene items

| # | Item | Evidence | Decision |
|---|---|---|---|
| H1 | Two abandoned-looking worktrees | `.claude/worktrees/smi-018-implementation` (7c8430d) and `.paseo/worktrees/2truu8kn/...` (b699e2d) | M4 criterion "no abandoned worktrees": merge or clean; record the decision. |
| H2 | `experiments/` (39 files) | Sanctioned parking area (harness_*, gov_corpus, reports/results/runs) | **KEEP** — this is the blueprint's designated park location. |
| H3 | `make-scenarios/` JSON fixtures | Scenario definitions (daily-note, vault-search, memory-append, ...) | Verify they are referenced (likely harness scenarios); if unused, classify in the M3 second pass. |
| H4 | Root-level `verify_multi_tenant.py` | Top-level script | Verify it is referenced or move under `scripts/`/`experiments/` in M3. |

## Known claims-vs-reality checks to run in M3

1. README/MANIFEST must not advertise multimodal, stage-0, personal_intelligence, or `core/health` as live capabilities.
2. Metrics must not count stub calls (already enforced for multimodal — extend the audit discipline to any other stub path).
3. `docs/task-contract-v1.md`, `docs/triumvirate*.md`, `docs/conversation-*-v1.md` — confirm each describes a live or explicitly parked contract; park docs for parked items.

## Freeze

Per Rule 3, no new subsystem enters during M0–M3 unless a core-path failure demands it. New ideas → parking-lot record (dated, proposed value, dependency, trigger).
