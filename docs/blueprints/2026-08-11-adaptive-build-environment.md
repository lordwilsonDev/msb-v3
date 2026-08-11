# MSB v3 — Adaptive Build Environment
### Production Blueprint · 2026-08-11

> **Status:** proposed design, awaiting owner review.
> **Scope decided:** rock-solid for **one sovereign user** (Wilson). No multi-user, no cloud, no SaaS.
> **Grounded, not aspirational:** every capability below names a real existing endpoint/module or a specific net-new piece. Written from a live audit of the repo, not memory.

---

## 0. The spine — what this environment is *for*

This is not a generic IDE. It is built around **one person's actual build method**:

> Read research papers and run deep research *while coding* → dive through repos and papers for the part you need → pull it in → build like nothing is impossible. And **everything you harvest compounds** — what you pull today is searchable, and building on, tomorrow.

So the environment has one job: **keep research, repos, code, and the running system in one adaptive space, and make everything you reach for stack into MSB's own knowledge.** It bends around the method instead of forcing the method into a tool.

### Guiding principles

1. **Serve the method, not a generic IDE.** Research + repos + code + live system, one space.
2. **Everything harvested compounds.** Papers/repos/snippets pulled in get captured into MSB's knowledge (RAG + evidence store) so they're searchable next time.
3. **Adaptive = reshapes to context.** The view foregrounds what you're doing now (a research run, an error, a build) and reflects the system's own self-watching state — not fixed panels.
4. **Rock-solid for one.** Reliability, backup, sovereignty of your own box. Never lose the work.
5. **Build on what exists.** ~80% of the backend primitives already ship. The real net-new is the *window*, the *harvest loop*, *backups*, and *security hardening*. Don't rebuild the commodity.
6. **Chat stays in Open WebUI.** Already wired via `/v1`. The Cockpit is the window into MSB's own internals, not a second chat app.

### Non-goals (YAGNI — explicit)

- No multi-user, no auth beyond the operator token, no cloud deploy.
- No rebuilding chat/RAG UI — Open WebUI keeps that.
- No new grand-named subsystems. This blueprint fills and surfaces what exists.
- No ML-heavy "adaptivity" in v1 — adaptive starts rule-based and grows.

---

## 0.5 The engine — the Research→Build Flywheel (the heart)

The environment exists to run **one loop** — the owner's cognitive flywheel. Everything else (Cockpit, harvest, panels) is in service of turning this wheel faster and recording each turn.

```
        ┌──────────────────────────────────────────────────────────┐
        ▼                                                          │
 1. VERIFY NOVELTY ──► does it already exist?  (gate: build only if not)
 2. DRAFT BLUEPRINT
 3. CHARGE ── AIL + MoIE run the research ──► UIM
 4. UPDATE BLUEPRINT (from the UIM)
 5. SCAN NEW PAPERS ──► do they solve a standing problem?
 6. SURFACE NEXT PROBLEMS
 7. RUN THE SKILL ──► BUILD THE THING
 8. COMBINE with something new (cross-domain)
 9. RECORD to the vault ──────────────────────────────────────────┘
        (Ouroboros = Yin governor throttles runaway expansion)
```

**The owner's frameworks, used precisely:**

- **AIL — Axiom Inversion Logic:** excavate the hidden axioms behind a claim, invert them systematically to reveal alternative possible worlds. *The reasoning method.*
- **MoIE — Mixture of Inversion Experts:** three synthetic experts — **Inversion Critic**, **Positive Deviant Scout**, **Mechanism Synthesizer** — in iterative dialectic. *The brain.*
- **UIM — Unified Inversion Model:** their output — core inverted axiom, boundary conditions, causal architecture, measurable constructs, falsifiable predictions, and a meta-inversion that critiques its own reasoning.
- **Ouroboros (Yin governor):** the deterministic/subtractive counterweight to MoIE's (Yang) generative expansion — keeps the loop convergent instead of runaway.
- **Vault doc-trail:** the durable record of every turn.

**Why this stays current:** you don't chase the frontier — you run a machine that ingests it. New papers enter at step 5, get tested against open problems, and either close one or reveal the next.

**It's already ~half-built — the job is to connect it, not invent it:**

| Flywheel stage | Existing asset to wire in |
|---|---|
| 1 Verify novelty | SRSE `synthesizing-cross-domain` + `validating-adversarially` skills |
| 3 Charge (AIL+MoIE) | `moie-os` server (scanner_chain / verify / telemetry), MoIE task-scoped runtime |
| 3→4 UIM output | research runs already emit `*_UIM.json` (`runtime/research/`) |
| 5 Scan papers | UAC `stage_0_knowledge_acquisition`, Tavily, NotebookLM, `Papers-Index-2026` |
| 7 Run skill / build | skills system + `agent_factory`, MSB runtime |
| 8 Combine | SRSE cross-domain synthesis |
| 9 Record | vault doc-trail (LM-Wiki `raw/`+`wiki/` pattern), UAC `axiom_library` |
| Governor | Ouroboros Yin/subtractive layer |

The Cockpit (Phases 1–2) becomes the **control surface for this flywheel** — see each stage, drive it, and watch a turn complete.

---

## 0.6 Autonomous operation (fully-auto) — Yang engine, Yin governor

**Decision:** the flywheel runs **fully autonomously**. It turns itself; the owner supervises and approves. This is the Yang (generative) engine — and it is only safe because the Yin (governing) gates are load-bearing, not decorative. This matches the repo's own principle: *never trust unverified output.*

**What auto-runs (no lever needed):** stage 5 scan new papers → stage 1 verify novelty → stage 3 charge (AIL+MoIE → UIM) → stage 4 propose an updated blueprint → stage 6 surface next problems. The loop generates and reasons continuously.

**What passes through an approval gate (irreversible / side-effecting):** stage 7 build, stage 8 combine, stage 9 promote-to-permanent knowledge, and any git commit or vault write. These land in an **approval queue** in the Cockpit — one-click approve/reject, with the UIM and evidence attached so the owner decides fast.

**The brakes that MUST exist before auto-run is enabled (moved into Phase 0):**

1. **Ouroboros governor** — the deterministic/subtractive throttle on MoIE expansion; convergence enforced, not requested.
2. **Budget + rate limits** — hard caps on research calls, tokens, and loop iterations per period; the loop halts when a cap is hit (fail-closed).
3. **Approval queue** — nothing irreversible executes without an explicit owner approval; queue survives restarts.
4. **Kill switch + audit** — one control to pause the whole loop; every autonomous action written to the UAC audit chain so it's never a black box.

> **Sequencing consequence:** these four guardrails are Phase 0 foundation work, alongside backups. The engine does not run itself until the brakes are proven.

---

## 1. What already exists (so we build, not rebuild)

**Runtime:** FastAPI on `:8766`, launchd-supervised (`com.lordwilson.msb-v3`), 516 tests green, ruff clean.

**Endpoints we can surface immediately:** `/system/health|config|routes`, `/status`, `/metrics` (+`/metrics/prometheus`), `/memory/{session}`, `/rag/index|search`, `/research/assistant/*`, `/safety/status`, `/evolution/scan`, `/conversation/ask`, `/workflow/advance`, `/v1` (OpenAI-compat).

**Self-watching internals to expose:** `triumvirate/` (mission_anchor, guardian_scanner, argus_auditor, meta_cognitive_planner), `uac/` (audit_chain, axiom_library evidence store, observer_log, stage_0_knowledge_acquisition, research_backend), `guardrails/`, hygiene gates, `verify_claims.py` fabrication gate.

**Research infra already present:** `runtime/research/` runs, Tavily, NotebookLM, SRSE skills, the vault Research-Output-System, Qdrant `tenant_wilson-vault` index.

**Data stores to protect:** `data/msb_v3.db`, `runtime/triumvirate/mulch_learnings.db`, `data/uac/axiom_library.db`, Qdrant `storage/`, `~/Documents/Vault`.

> **Takeaway:** the backend for "see the system" and "run research" mostly exists. The gap is a window over it, a capture loop, and the reliability/security last-mile.

---

## 2. The phases (ordered by what protects and serves you most)

Each phase is independently usable — you are never stuck in a half-built state.

### Phase 0 — Protect & stabilize (small, first, non-negotiable)

*Never lose two years to a disk hiccup or the Qdrant "data-loss trap."*

- **Automated backup + tested restore** for all data stores above. Restore must be *verified*, not assumed.
- **`.gitignore`** the churning hygiene artifacts (`artifacts/hygiene/*.json`, `webcheck-*`); stop noise commits.
- **Finish path/config portability** (`MSB_HOME`/`MSB_REPO`/`MSB_VAULT_PATH` — mostly done today).
- **Provisioning script** — pull `qwen3:8b` + `nomic-embed-text` on a fresh box.
- **Reproducible rebuild** — a `Dockerfile` *or* `setup.sh` that stands the whole stack up from `MANIFEST.md`.
- **The brakes (required before fully-auto — see §0.6):** Ouroboros governor, budget/rate/iteration caps (fail-closed), a restart-surviving **approval queue**, and a **kill switch** with every autonomous action logged to the UAC audit chain.

**Done when:** you can wipe and rebuild from clone + backups and prove the restore works — *and* the loop's brakes are proven (caps halt it, kill switch stops it, nothing irreversible runs without approval).

### Phase 1 — The Cockpit: one screen, read-only (the "look at it" win)

*A single owned page served by MSB (e.g. `/cockpit`) that finally lets you SEE the whole living system.*

- **Live panels** over existing endpoints: health of all 4 services, models loaded, memory browser, RAG/vault index freshness, mission anchor, audit chain / Argus findings / claims status, hygiene gate status.
- **Adaptive surfacing v1 (rule-based):** foreground what's active — the current research run, recent errors — and tuck the rest away.
- **Find-box:** one semantic search across vault + memory + audit chain — *"where did I decide X / what do I know about Y"* — the findability you already love, built in.

**Mostly a good front-end over real data.** Keep it simple — server-rendered or a lightweight page, **not** a heavy SPA rabbit hole.

**Done when:** you open one screen and see everything, and can find anything by asking.

### Phase 2 — Stand up the Flywheel (§0.5), one turn end-to-end

*This is where the dashboard becomes a **build cockpit** — the control surface for the loop. Target: drive one full turn (verify → charge → build → combine → record) from the cockpit.*

- **Research panel:** launch deep research (Tavily / NotebookLM / SRSE) from the cockpit while you code; results shown alongside, not in another window.
- **Harvest action:** paste a paper URL / repo link / snippet → it's chunked and ingested into MSB knowledge (Qdrant RAG + `axiom_library` evidence store) → immediately searchable. **This is the "everything compounds" loop.**
- **Capture-to-Inbox (recommended default — flip if you want):** harvested items land in a knowledge **Inbox** you can *promote-to-permanent* or *discard*. Adaptive capture, but you stay sovereign over what becomes permanent — and it keeps junk out of the vault index.
- **Hands-on controls:** switch active model, run chat/RAG, trigger a reindex, kick a research run, and watch the audit chain update live.

**Done when:** you can research, harvest, and build from one place, and everything you pull is there next time.

### Phase 3 — Harden for daily reliance

*What lets you depend on it without babysitting.*

- **Observability you can see:** surface metrics + logs in the cockpit; structured logging; **loud** error surfacing (no silent failures).
- **Security of your box:** lock down `CORS` (currently `*`), keep secrets out of git (`.env` / keychain), require the operator token on control actions, keep binding to loopback.
- **Visible self-heal:** show what launchd / `evolution` / Argus are doing, so "self-healing" is observable, not a black box you have to trust blind.

**Done when:** when something degrades, you *know* — before it fails.

### Phase 4 — Fill the thin spots & honest cleanup

- **Real-or-rename** the grand-named stubs (`provenance`, `memory_graph` are ~45-line `__init__`s). Either make them real or rename to match what they do.
- **Harvest → knowledge graph (optional):** connect captured research into `memory_graph` so relationships surface and the "find" gets smarter over time.
- Docs, optional llama.cpp fallback (weights currently missing), vault backup.

---

## 3. The adaptive thread (how "adaptive" grows, without ballooning)

- **v1 (Phase 1):** rule-based surfacing — foreground the active run/error.
- **v2 (Phase 2–3):** the environment reacts to the system's self-watching signals (Argus findings, mission drift, stale index) by *changing what it shows you*.
- **v3 (Phase 4+):** it learns from what you search and keep (tie into `mulch_learnings` / `evolution`) to surface better over time.

Keep it light early. Adaptive is a direction, not a v1 feature to over-engineer.

---

## 4. Honest risks

1. **The cockpit front-end is the biggest net-new build.** Guard against SPA scope-creep — start minimal, server-rendered, grow only where it earns it.
2. **The harvest loop can pollute the index with junk** — the Inbox/promote gate is the mitigation; don't skip it.
3. **"Adaptive" can balloon into ML complexity** — resist; rules first.
4. **Breadth vs finishing** (the standing pattern): each phase ships usable so you get wins without needing the whole thing done.

---

## 5. Success (one sentence)

> You open one screen, turn the flywheel (§0.5) — verify → charge with AIL+MoIE → build → combine → record — without leaving it, everything you pull compounds into your own knowledge, you can trust it, and you can never lose it.

---

## 6. Next step after this doc is approved

Turn **Phase 0 + Phase 1** into a concrete, step-by-step implementation plan (actual next-actions and file targets), then build — Phase 0 first (protect the work), Phase 1 second (the window). Later phases get their own plans when we reach them.
