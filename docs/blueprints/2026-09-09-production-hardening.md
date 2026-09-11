# MSB-v3 Production Hardening Blueprint
### Production Blueprint · 2026-09-09

> **Status:** authoritative next-step plan (owner-approved), awaiting execution.
> **Scope:** single-operator, local-first, governed agent runtime.

---

**Version:** 1.0  
**Date:** 2026-09-09  
**Scope:** Single-operator, local-first, governed agent runtime  
**Goal:** Move msb-v3 from “high-quality but not yet production-clean” to a fully production-ready state while preserving its core strengths (governance, sovereignty, auditability).

---

### 1. Executive Summary

msb-v3 is a mature, single-operator governed agent runtime. Its primary value is the auditable request → evidence loop, not model serving. Current liabilities are degraded optional paths, dormant code, external critical behaviour, elevated operational surface area, and insufficient packaging/observability of the governance path.

This blueprint defines the exact sequence of work required to eliminate those liabilities. New features and capability expansion are explicitly deferred until the foundation is clean.

**Guiding principle**  
Eliminate degraded and dormant paths → pull critical behaviour inside the trust boundary → reduce moving parts → harden observability. Only then consider expansion.

---

### 2. Current State Snapshot

| Area                        | Status                                      |
|----------------------------|---------------------------------------------|
| Active inference           | `qwen3:8b` + `nomic-embed-text` only        |
| Frontier path              | Circuit-broken (DeepSeek 402)               |
| Dormant providers          | Anthropic, llama.cpp, Paseo                 |
| Hot-reload                 | External Trinity daemon                     |
| Supervision                | Multiple independent launchd jobs           |
| Packaging                  | None (launchd-only)                         |
| Governed-loop observability| Partial                                     |
| Inventory alignment        | Clean after recent prune                    |

---

### 3. Production Priorities (Ordered)

#### 3.1 Resolve or Formally Retire the Frontier Path
- Decide: restore a working frontier provider **or** permanently remove the DeepSeek seam and all related routing.
- Remove residual configuration, circuit-breaker logic, and task-kind defaults that still reference the frontier.
- **Exit criterion:** No live or residual code path references a non-functional frontier provider. All task kinds route cleanly to local.

#### 3.2 Prune or Quarantine All Dormant Providers and Adapters
- Hard-remove or explicitly quarantine Anthropic, llama.cpp/Gemma, and Paseo.
- Eliminate residual environment variables, imports, and status reporting.
- **Exit criterion:** Active configuration and startup surface contain only live, intentional components.

#### 3.3 Bring Hot-Reload Inside the Sovereignty Boundary
- Absorb Trinity hot-reload into the main repository and supervision model, **or** replace it with an in-process / launchd-native keep-alive that is versioned with the core system.
- Retire the external `~/.trinity` dependency for keep-alive.
- **Exit criterion:** Model residency is controlled by code and configuration that live inside the governed boundary.

#### 3.4 Reduce Operational Surface Area
- Inventory every independent supervised process.
- Consolidate or more tightly couple chain-notary, auto-repair, and Qdrant under fewer failure domains while preserving required durability.
- **Exit criterion:** Measurable reduction in the number of independently failing units; remaining supervision still provides KeepAlive and recovery semantics.

#### 3.5 Add Minimal but Robust Packaging
- Introduce a Dockerfile (or equivalent) that captures runtime dependencies, entrypoint, and configuration surface.
- Keep packaging minimal; do not expand into multi-tenant or cloud-native scope.
- **Exit criterion:** A clean build from the packaging definition produces a functional instance capable of serving the governed agent loop.

#### 3.6 Strengthen Observability of the Governed Loop
- Promote ActionGate decisions, evidence-spine writes, audit-chain appends, and degradation events to first-class metrics and structured logs.
- Ensure full Goal → Evidence chain can be reconstructed from observability data.
- Define basic alerting on unexpected BLOCK/FAIL rates and prolonged local-inference degradation.
- **Exit criterion:** Critical governance events are alertable and the request-to-evidence path is fully reconstructible from telemetry.

---

### 4. Explicit Non-Goals (Immediate Term)

- Adding new model providers or experimental features while the inventory and daemon surface remain unclean.
- Expanding multi-user or collaboration capabilities (contradicts single-operator design).
- Heavy investment in the static Wrongness Engine until the live runtime is hardened.

---

### 5. Implementation Sequence

1. Frontier decision & cleanup  
2. Dormant provider prune  
3. Hot-reload sovereignty  
4. Surface-area reduction  
5. Packaging  
6. Governed-loop observability  

Each step must produce durable evidence (configuration diff, health verification, test results) before the next step begins.

---

### 6. Validation Gates

A priority is complete only when:

- The stated exit criterion is met.
- Residual references have been searched and cleared.
- Gateway health, `ollama list` / `ollama ps`, and relevant launchd jobs confirm the expected state.
- Changes are recorded in the evidence / audit trail.

---

### 7. Success Definition

msb-v3 is considered production-clean when:

- Only intentionally live components remain in the runtime surface.
- Critical behaviour lives inside the governed boundary.
- Operational surface area is minimised.
- The system is reproducibly packageable.
- The core governance loop is highly observable and alertable.

At that point the system’s existing strengths (governance, sovereignty, auditability) are no longer undermined by residual liabilities, and controlled capability expansion may resume.

---

This blueprint is the authoritative next-step plan for msb-v3 production hardening.