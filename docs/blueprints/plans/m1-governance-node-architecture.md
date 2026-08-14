# M1 Mac mini — Governance-Node Architecture

**Source:** pasted advisory memo (2026-08-13). Saved verbatim below; mapping
to existing msb-v3 modules is appended at the bottom.

---

## The M1 is a governance node, not the biological-compute engine

Your machine gives you:

- 8-core CPU
- 8-core GPU
- 16-core Neural Engine
- 8/16 GB unified memory
- Thunderbolt/USB 4 up to 40 Gb/s
- Gigabit or optional 10Gb Ethernet
- local persistent storage
- relatively low power consumption

That's actually a **very good edge-control substrate**.

But 8–16 GB unified memory is the constraint that matters most for your
MSB/MoIE architecture.

So I would split the system into **five planes**.

### 1. Sovereign Control Plane — M1

The M1 owns:

```text
MSB Runtime
        │
        ├── Policy Engine
        ├── Identity / Capability Registry
        ├── Audit Ledger
        ├── Provenance
        ├── Experiment Registry
        ├── Model Registry
        ├── Safety Gates
        ├── Scheduler
        └── Recovery
```

This is where your existing MSB architecture fits extremely well.

The M1 doesn't need to be the smartest computer in the system.

It needs to be the computer that **decides what is allowed to happen**.

### 2. Sensor Plane

The Thunderbolt/USB4 ports become the expansion boundary.

Conceptually:

```text
                    M1
                     │
              ┌──────┴──────┐
              │ Sensor Bus  │
              └──────┬──────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
     Cameras      Wearables    Instruments
        │            │            │
        └────────────┼────────────┘
                     ▼
              Sensor Gateway
                     │
                     ▼
              Timestamp + Hash
                     │
                     ▼
              Evidence Store
```

The important part isn't merely collecting measurements.

Every observation becomes:

```
O_i = (
  timestamp,
  sensor,
  calibration,
  measurement,
  uncertainty,
  provenance,
  hash
)
```

Now an AI agent cannot conveniently rewrite history.

### 3. Compute Plane

This is where I'd avoid making the M1 do everything.

Your architecture should support:

```text
M1
 │
 ├── Local lightweight models
 │
 ├── Classical signal processing
 │
 ├── orchestration
 │
 └── safety decisions
        │
        ▼
External accelerator / workstation / cloud
        │
        ▼
Large models / expensive simulation
```

That gives you **compute independence**.

If tomorrow you replace the M1 with an M4, Linux workstation, GPU server,
or another machine, the sovereign layer doesn't fundamentally change.

That's exactly aligned with your principle:

> **Composable. Observable. Recoverable. Replaceable.**

### 4. Identity Plane

This is where your previous architecture becomes much more interesting.

Do **not** store "consciousness" as one giant object.

Create a structured identity state:

```text
IDENTITY_STATE
│
├── autobiographical memory
├── semantic knowledge
├── preferences
├── behavioral patterns
├── values
├── relationships
├── goals
├── linguistic signatures
├── decision patterns
├── uncertainty
└── provenance
```

Then create cryptographic snapshots:

```
I_t = Hash(
  Memory_t,
  Behavior_t,
  Preferences_t,
  Values_t,
  Provenance_t
)
```

The M1 can protect and attest the **representation**.

It cannot prove that the representation *is consciousness*.

That distinction stays explicit.

### 5. Experimental Plane

This is where I'd make the biggest change to your earlier blueprint.

The M1 should **never autonomously perform biological intervention simply
because an AI model predicts that it would work.**

Instead:

```
Prediction
  → Safety Gate
  → Human/Institutional Authorization
  → Controlled Experiment
  → Measurement
  → Verification
```

The M1 becomes the **flight computer**, not the scientist with unrestricted
access to the engine.

That's a much more defensible architecture.

### Your M1's real superpower

It's not raw compute.

It's **locality**.

You can build:

```
Local Evidence + Local Governance + Local Identity + Local Audit
```

without requiring every decision to leave the machine.

And that fits your sovereign architecture extremely well.

The external cloud becomes an **untrusted computational resource**.

The M1 remains the authority.

### Physical layout

```text
                 ┌──────────────────────────┐
                 │       REAL WORLD         │
                 │                          │
                 │ Sensors / Instruments    │
                 │ Human interfaces         │
                 │ Experimental systems     │
                 └────────────┬─────────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ SENSOR GATEWAY   │
                    │ timestamp/hash   │
                    └────────┬─────────┘
                             │
                             ▼
┌────────────────────────────────────────────────────┐
│                    M1 MAC MINI                     │
│                                                    │
│ ┌──────────────┐   ┌────────────────────────────┐ │
│ │ Evidence     │   │      MSB / MoIE Runtime    │ │
│ │ Store        │◄─►│                            │ │
│ └──────────────┘   │ Planner                    │ │
│                    │ Executor                   │ │
│ ┌──────────────┐   │ Policy                     │ │
│ │ Provenance   │◄─►│ Safety                     │ │
│ │ Graph        │   │ Experiment Manager         │ │
│ └──────────────┘   │ Identity Manager           │ │
│                    │ Recovery                   │ │
│ ┌──────────────┐   └────────────────────────────┘ │
│ │ Identity     │                                  │
│ │ Vault        │                                  │
│ └──────────────┘                                  │
└───────────────────────┬────────────────────────────┘
                        │
                 Capability Gateway
                        │
             ┌──────────┴──────────┐
             ▼                     ▼
       Local Compute          Remote Compute
       / accelerator          / cloud / GPU
             │                     │
             └──────────┬──────────┘
                        ▼
                 RESULTS ONLY
                        │
                        ▼
                M1 VERIFICATION
```

### What that gives you

- The cloud can compute.
- The agents can hypothesize.
- The sensors can observe.
- The sovereign node decides what becomes trusted state.

That's the architecture I'd build around the M1.

**Hardware correction:** the M1 Mac mini's unified memory and internal SSD
are integrated/soldered, so the practical expansion strategy should be
external storage and external compute—not planning around internal RAM/SSD
upgrades. That makes your Thunderbolt/USB4 architecture particularly
important.

The M1 therefore isn't obsolete for this design. It's actually a pretty
elegant **edge governance computer** — provided you don't make it
pretend to be a datacenter, a biomedical instrument, or proof that
consciousness can be transferred.

---

## Mapping to existing msb-v3 modules

This blueprint's five planes line up unevenly with what's already in the
codebase. Status column: `✓` ship-ready, `~` partial, `—` not present.

| Plane | Plane component | msb-v3 module | Status |
|---|---|---|---|
| 1. Sovereign Control | Policy Engine | `msb_v3/guardrails/` (`fold.py`) + `msb_v3/core/config.py` (`Settings`) | ~ |
| 1. Sovereign Control | Identity / Capability Registry | `msb_v3/core/identity.py` (`AgentIdentity`) + `triumvirate/guardian_scanner.py:SBOMRegistry` | ~ |
| 1. Sovereign Control | Audit Ledger | `msb_v3/uac/audit_chain.py` (hash-linked Merkle) | ✓ |
| 1. Sovereign Control | Provenance | `msb_v3/observability/audit.py:_MULCH_DB` + `uac/audit_chain.py` | ~ |
| 1. Sovereign Control | Safety Gates | `triumvirate/guardian_scanner.py` (`GuardianScanner`, `PoisonPill`) | ✓ |
| 1. Sovereign Control | Scheduler / Planner | `triumvirate/meta_cognitive_planner.py` (`MetaCognitivePlanner`) | ✓ |
| 1. Sovereign Control | Recovery | `agent/execution_loop.py` (was `ralph_loop.py`) — `IntegrityLocks`, hash chain | ✓ |
| 1. Sovereign Control | Experiment Registry / Model Registry | (no standalone module; tracked under `uac/stage_0_knowledge_acquisition.py`) | ~ |
| 2. Sensor | Sensor Bus / Sensor Gateway | — | — |
| 2. Sensor | Timestamp + Hash | `uac/audit_chain.py` hash primitives are reusable | ~ |
| 2. Sensor | Evidence Store | `vesta/` (planned, not yet adopted) | — |
| 3. Compute | Local lightweight models | `local_ai/` (`ollama.py`, `llama_client.py`) | ✓ |
| 3. Compute | External accelerator | `core/config.py:OPENAI_FRONTIER_URL` (router seam) | ~ |
| 3. Compute | Capability Gateway routing decision | — (no single dispatcher; routing is per-callsite) | — |
| 4. Identity | Structured identity state (memory + behavior + preferences + values) | `core/identity.py` only carries id/version/host/environment | — |
| 4. Identity | Cryptographic identity snapshots I_t = Hash(...) | `uac/audit_chain.py` hash chain can do this for any JSON payload | ~ |
| 5. Experimental | Safety Gate | `triumvirate/guardian_scanner.py:GuardianScanner.scan` | ✓ |
| 5. Experimental | Human/Institutional Authorization gate | (no human-in-the-loop handler; `api/auth.py:operator_token` is the closest primitive) | — |
| 5. Experimental | Controlled Experiment wrapper | `uac/stage_0_knowledge_acquisition.py` (`stage_0`, knowledge ingest) | ~ |
| 5. Experimental | Measurement + Verification | `msb_v3/conversation/producer.py:_verify_claims` (the research claims verification pattern) | ✓ |

### Concrete gaps worth closing for M1 deployment

Ordered by ROI on the M1 substrate:

1. **Capability Gateway** — a single dispatcher that decides "this call
   fits in 8 GB / `LocalAIClient` route" vs "this call goes to the
   external frontier seam". Today the decision is per-callsite, with
   no central envelope that records *why* the route was picked. ~half
   the value of Compute-Plane independence is latent until this lives.

2. **EXPERIMENT_GATE policy** — a one-line capability token on any
   mission flagged `requires_authorization`. Guardian/PoisonPill path
   should refuse to dispatch if the token isn't present; the planner
   is responsible for parking the mission at the "Human/Institutional
   Authorization" stage until a human signs off. The "M1 should never
   autonomously perform biological intervention" rule becomes a
   *codified capability check* rather than a moral principle.

3. **Sensor Plane skeleton** — `src/msb_v3/sensors/` with
   `{bus,gateway,evidence_store,hashing}.py`. The Observation
   record shape `O_i = (timestamp, sensor, calibration, measurement,
   uncertainty, provenance, hash)` lives there as the contract.
   Reuses `uac/audit_chain.py` hash primitives and `vesta/` evidence
   storage when it lands.

4. **Identity Vault for structured-state snapshots** — extend
   `core/identity.py` from "host/version" to a real
   `IDENTITY_STATE` object with the categorised fields from the
   blueprint, and a `snapshot()` method that emits a hash-chained
   record. The personal-intelligence work (currently dormant per the
   `2026-08-13-dormant-satellites-disposition.md` document) was
   heading in this direction; this plan re-anchors it with a clear
   non-claim ("the M1 can attest the representation, not declare the
   representation *is* consciousness") that prevents the project
   from overreaching into claims it can't prove.

5. **Storage correction** — document this in `CLAUDE.md` so a future
   contributor doesn't waste a sprint planning for soldered-RAM
   upgrades. The Thunderbolt/USB4 subtree is the actual expansion
   surface.

### What we are NOT building from this blueprint

- No claim that the M1 is the biological-compute engine. The 8–16 GB
  constraint makes that wishful.
- No claim that identity snapshots are proof of consciousness.
  Crypto attestations prove *integrity of the representation*, not
  the truth of what's represented.
- No autonomous experimental intervention without human authorization.
  Code, not just principle.
