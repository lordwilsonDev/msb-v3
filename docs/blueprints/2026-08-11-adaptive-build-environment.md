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

**Done when:** you can wipe and rebuild from clone + backups, and prove the restore works.

### Phase 1 — The Cockpit: one screen, read-only (the "look at it" win)

*A single owned page served by MSB (e.g. `/cockpit`) that finally lets you SEE the whole living system.*

- **Live panels** over existing endpoints: health of all 4 services, models loaded, memory browser, RAG/vault index freshness, mission anchor, audit chain / Argus findings / claims status, hygiene gate status.
- **Adaptive surfacing v1 (rule-based):** foreground what's active — the current research run, recent errors — and tuck the rest away.
- **Find-box:** one semantic search across vault + memory + audit chain — *"where did I decide X / what do I know about Y"* — the findability you already love, built in.

**Mostly a good front-end over real data.** Keep it simple — server-rendered or a lightweight page, **not** a heavy SPA rabbit hole.

**Done when:** you open one screen and see everything, and can find anything by asking.

### Phase 2 — The build loop: research + repos + capture (your method, made real)

*This is where the dashboard becomes a **build cockpit**.*

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

> You open one screen, see your whole system, research + harvest + build without leaving it, everything you pull compounds into your own knowledge, you can trust it, and you can never lose it.

---

## 6. Next step after this doc is approved

Turn **Phase 0 + Phase 1** into a concrete, step-by-step implementation plan (actual next-actions and file targets), then build — Phase 0 first (protect the work), Phase 1 second (the window). Later phases get their own plans when we reach them.
