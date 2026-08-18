# Provider Harness & Give-Away Readiness

Date: 2026-08-18
Status: proposed (Phase 0 pending explicit push approval)
Baseline: `v0.3.1` (verified: 1642 passed / 8 skipped, `make lint` green)

The north star: **a harness-agnostic provider layer — any agent, plugged into
this, becomes governed and provable.** The moat becomes the give-away product.

---

## Ground truth (corrects the "implementations aren't written" premise)

The seam already exists and is further along than assumed:

- `src/msb_v3/agent/providers.py` — `AgentProvider` ABC + `ProviderRegistry`
  (deterministic `select`), with three working implementations:
  - `LocalAgentProvider` → delegates to `agent.handle()` (the *pattern to copy*:
    it passes a `client` through the full MoIE → ActionGate → evidence → ledger
    path).
  - `CliAgentProvider` — Claude Code / Codex / OpenCode as a bounded subprocess.
  - `PaseoAgentProvider` — Paseo-managed Claude/Codex/OpenCode.
- `src/msb_v3/fabric/model_router.py` — `FrontierClient` (OpenAI-compatible
  `/v1` client, `generate` + `agenerate`) + `ModelRouter` (R-score
  local/frontier routing).
- `src/msb_v3/gateway/` — the Capability Gateway, the auditable dispatcher
  between runtime and (local|remote) compute.

**What is genuinely missing:** a *native API provider* for DeepSeek/Anthropic
behind the ABC, plus the client-side `chat(messages)` method the tool-loop
needs (`FrontierClient` only has `generate(prompt)`).

---

## Phase 0 — Ship the closed loop

- [ ] `git push origin main --tags` (requires explicit operator approval —
  never pushed by an agent unprompted).

---

## Phase 1 — One provable harness: DeepSeek

*One closed harness beats three half-wired.* Do exactly one, end to end.

1. **Config** — add DeepSeek `base_url` / `api_key` / `model` to
   `src/msb_v3/core/config.py` and `.env.example` (DeepSeek is
   OpenAI-compatible, so `FrontierClient`'s payload builder already works).
2. **Client** — `DeepSeekClient` extending `FrontierClient`, adding
   `chat(messages)` so the tool-loop's message-array path works (map messages
   → OpenAI chat-completions shape).
3. **Provider** — `DeepSeekAgentProvider(AgentProvider)` whose `execute()`
   calls `handle(goal, client=deepseek_client)` — the exact
   `LocalAgentProvider` pattern, so MoIE gate → ActionGate → evidence spine →
   ledger → receipt all fire with zero new governance code.
4. **Register** in `default_providers()` + `ProviderRegistry`.
5. **Hermetic tests** (`httpx.MockTransport`): a BLOCKed request makes 0
   DeepSeek calls; a PASS run emits exactly one receipt.

**Done =** a real task runs through it and a receipt appears in the Evidence
Stream with a **verified audit hash**. Provable, end-to-end, one harness.

---

## Phase 2 — Repeat the pattern: Anthropic, then Codex

- **Anthropic** — not OpenAI-compatible: its own Messages-API client behind the
  same `AgentProvider` contract (the real stress test of the seam).
- **Codex** — OpenAI-compatible: thin, after DeepSeek establishes the pattern.

---

## Phase 3 — Verifiable by a stranger

- [ ] README one-paragraph outsider framing (mostly done in the attribution
  pass; tighten if needed).
- [ ] `make setup` verified from a **clean clone** — prove a stranger can run
  it (this re-validates the `config/risk_templates.json` move from `v0.3.1`).
- [ ] One **independent review** of the trust-critical `msb_ledger` code before
  it is declared safe to rely on.

---

## Anti-direction (explicitly do NOT)

- Do not chase the 31 Phase-2 refs, the 8 `NotImplementedError`, or a new
  subsystem. The multimodal stub stays parked. Converging on the harness layer
  + give-away readiness is the move; widening is not.
