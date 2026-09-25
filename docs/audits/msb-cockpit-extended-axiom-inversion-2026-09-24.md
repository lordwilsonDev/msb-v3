# MSB Cockpit Extended Axiom Inversion

**Ten-Year Integrity, Recoverability, and Independent Verification**  
**Storage-Semantics Enforcement of Append-Only Audit Guarantees**

**Mac mini (M1, 2020) Specialist Operational Constraints**  
**Version 1.1 — 24 September 2026**

BlackSwanLabz / Systems Architecture & Epistemic Assurance

---

### Abstract

This document consolidates and extends the complete axiom inversion of the MSB Cockpit and its surrounding control-plane surfaces. Every dominant assumption is inverted, the resulting threat model is made explicit, and each critical property is required to be enforced by storage semantics, cryptographic structure, or independent mechanical isolation rather than by developer discipline or configuration convention.

Version 1.1 incorporates every material hardware, firmware, thermal, memory-architecture, storage, power, and ecosystem detail that a Mac mini (M1, 2020) specialist would extract from Apple documentation, teardowns, and long-term operational experience. These details are treated as hard constraints on the ten-year recoverability claims.

---

### 1. Purpose and Scope

The original symptom that triggered inversion was event-loop starvation caused by synchronous audit verification inside the Cockpit request path. Axiom inversion expands that single failure mode into a full causal graph covering resource contention, cache coherence, audit authenticity, recovery, operator authority, and physical-machine failure.

**Governing requirement:**

> Every critical guarantee must be enforced by storage semantics, cryptographic structure, or independent mechanical isolation — never by developer discipline or configuration convention alone.

All subsequent sections treat the Mac mini (M1, 2020) as the sole production runtime. Its non-upgradeable unified memory, soldered SSD, single-fan thermal design, and Apple Silicon page-size and Docker behaviour are therefore first-class constraints.

---

### 2. Mac mini (M1, 2020) Specialist Hardware Baseline

The following facts are taken as authoritative and immutable for the life of the machine.

| Characteristic | Specification |
|---|---|
| SoC | Apple M1, 5 nm (TSMC N5), 16 billion transistors |
| CPU | 8 cores — 4 performance (Firestorm) up to 3.2 GHz + 4 efficiency (Icestorm) up to ≈2.06 GHz |
| GPU | 8-core Apple GPU (no discrete eGPU support on M1) |
| Neural Engine | 16-core, 11 TOPS (FP16) |
| Unified memory | LPDDR4X-4266, 128-bit bus, 68.25 GB/s theoretical bandwidth. Configurable only at purchase: 8 GB or 16 GB. Not user-upgradeable. Soldered to the package. |
| Storage | Soldered PCIe 4.0 NVMe SSD (Apple AP0256 family or equivalent). Capacities 256 GB / 512 GB / 1 TB / 2 TB, also non-upgradeable after purchase. Sequential reads typically ≈2.9–3.3 GB/s on the internal controller. |
| Cooling | Single axial fan (max ≈4500 RPM) + aluminium heat-spreader. Chassis remains cool to the touch under sustained load; fan is essentially inaudible in normal operation. |
| Power | Idle ≈5–7 W at the wall; full sustained load ≈25–40 W. Internal PSU rated 150 W continuous. Dramatically lower thermal output than the 2018 Intel Mac mini (max ≈122 W). |
| I/O | 2× Thunderbolt / USB 4 (40 Gb/s), 2× USB-A (5 Gb/s), HDMI 2.0, Gigabit Ethernet (10 GbE optional at purchase), 3.5 mm headphone. Maximum two simultaneous displays (one 6K@60 Hz via Thunderbolt + one 4K@60 Hz via HDMI). |
| Networking | Wi-Fi 6 (802.11ax), Bluetooth 5.0. |
| Dimensions / weight | 197 × 197 × 36 mm, 1.2 kg. |
| Model identifiers | Macmini9,1 / A2348. |

**Critical long-term implications for MSB:**

- **Memory pressure is irreversible.** An 8 GB unit will swap aggressively under concurrent SQLite + Qdrant + model + Cockpit load; a 16 GB unit is the practical minimum for production.
- **SSD capacity is fixed.** Ten-year audit growth must be managed by archival to external media or the system will hit `AUDIT_WRITE_BLOCKED`.
- **There is no path to more Thunderbolt ports, more RAM, or a larger internal SSD** without replacing the entire machine.
- **Unified memory means CPU, GPU and Neural Engine contend for the same physical pool.** Large model weights, vector indexes and SQLite page cache all compete directly.

---

### 3. Storage-Semantics Enforcement of Append-Only

#### VI.8 — "Append-Only Means Nobody Deletes"

**Axiom:** The application never deletes audit records.

**Inverted:** Application behaviour is only one layer of authority. An adversary or operational error may still delete the SQLite file, truncate the JSONL file, modify permissions, replace the directory, restore an older backup, mount a different filesystem, or rewrite records outside the application process.

**Enforcement by storage semantics (mandatory on Mac mini M1):**

1. The authoritative audit store is an append-only, write-once log. Physical deletion or truncation of any committed record is impossible without breaking cryptographic chain continuity that is independently verifiable.
2. Each record carries a monotonic sequence number, a cryptographic hash of its canonical serialisation, and the hash of its immediate predecessor. Any gap, reordering, or substitution is detectable by a verifier that does not share the producer's code base.
3. The live store is periodically checkpointed to an external, independently controlled trust anchor (offline medium attached via Thunderbolt, remote WORM volume, or hardware-rooted log). The checkpoint contains the current head hash and sequence. Replacement of the live store is rejected unless the external anchor still validates.
4. File-system permissions and mount options are set so that the process that writes the audit log has no delete or truncate capability on the committed segments. Rotation occurs only by renaming completed segments into an archive directory that is itself append-only from the application's perspective.
5. Backups are themselves append-only and carry independent integrity proofs. A restore operation must re-verify the entire chain against the external anchor before the restored store is accepted as authoritative.

Because the internal SSD is soldered and non-replaceable, **the external trust-anchor medium must itself be treated as a first-class failure domain** and must be rotated on a documented schedule.

#### VI.9 — "SQLite Is the Audit Store"

**Axiom:** The database file represents the authoritative audit history.

**Inverted:** The file is only one representation of history. Power loss, WAL recovery, disk-full conditions, page corruption, incomplete backups, concurrent copies, schema migration failures, and SQLite version upgrades all constitute distinct failure modes.

**Additional M1 constraints:**

- Unified memory means the SQLite page cache competes directly with Qdrant indexes and any local model weights. Aggressive cache sizing can induce swap and destroy latency guarantees.
- The internal SSD, while fast (≈3 GB/s sequential), has finite write endurance. Continuous high-rate audit writing must be rate-limited or off-loaded.
- Power-loss testing must include the actual behaviour of the Apple NVMe controller under abrupt wall-power removal; the single-fan design does not change the need for fsync and WAL durability settings.

Required storage contracts remain: forced power-loss simulation, disk-full transition to `AUDIT_WRITE_BLOCKED`/`SAFE_MODE`, independent detection of corrupted pages or WAL, and versioned migrations that preserve both pre- and post-migration chain heads.

#### VI.10 — "Backup Means Recovery"

**Axiom:** A backup protects the audit history.

**Inverted:** A backup that has never been restored is an untested hypothesis.

On the Mac mini the only practical restore path for a failed internal SSD is a full machine replacement followed by restore from the external trust anchor. Therefore **every backup/restore procedure must be exercised on a second Mac mini (or equivalent Apple Silicon host) before being declared production-ready.**

---

### 4. Cache, Executor, and Resource Containment

#### VI.11 — "The Cache Only Needs Expiration"

State generation is stored in the same append-only log. TTL remains an emergency fallback only.

#### VI.12–VI.15 — Executor Capacity, Cancellation, Self-Probes, Observability

The M1's 4 performance + 4 efficiency core design, combined with a single shared memory pool of at most 16 GB, makes executor saturation a first-order risk. Explicit concurrency limits, per-panel budgets, queue-depth metrics, and admission control are mandatory. Self-probes and observability must operate under a hard resource budget; the Cockpit must never be the sole observer of the Cockpit.

---

### 5. Test Isolation and Epistemic Separation

Production paths, credentials, sockets and filesystem mounts must be physically inaccessible to test processes. On a single-machine deployment this requires either a separate volume, a second physical machine, or a tightly controlled virtualisation boundary that itself respects the M1's 16 kB page size and Docker behaviour.

---

### 6. Replay, Audit Schema, and Canonicalisation

All original requirements retained. Model identity must also record **whether the model ran natively on Apple Silicon or under Rosetta translation.**

---

### 7. UI, Operator, and Emergency Control

All original requirements retained. "localhost" remains insufficient authorisation.

---

### 8. Supervisor, Restart, Memory, and Dependencies

#### VI.30–VI.36

`launchd` is the native supervisor on macOS. Crash-loop detection, restart backoff, health-gated restart and safe-mode entry must be implemented inside the launchd job definitions or a thin wrapper that launchd supervises.

**Qdrant / Docker on Apple Silicon notes (specialist experience):**

- Apple Silicon uses 16 kB pages. Certain jemalloc configurations inside older Qdrant Docker images abort with **"Unsupported system page size"**. Prefer native `arm64` images or images known to handle 16 kB pages.
- Bind mounts of Qdrant storage volumes have exhibited **vector-zeroing bugs** on macOS (vectors become all-zero after container restart). Named Docker volumes are safer; independent integrity checks of the vector store after every restart are required.
- Docker Desktop on recent macOS versions has experienced intermittent daemon-start failures; an external watchdog that can restart Docker itself is part of the recovery contract.

Memory carries full provenance. Vector similarity is never treated as truth authority. Unbounded memory growth is forbidden; retention, decay and quarantine policies are mandatory.

---

### 9. Storage Growth, Archival, and Physical Machine

#### VI.37–VI.41 — Disk, Archival, Machine Failure

Disk-full behaviour remains:

```
NORMAL → LOW_SPACE → CRITICAL_SPACE → AUDIT_WRITE_BLOCKED → SAFE_MODE
```

Because the internal SSD cannot be enlarged, **archival to external Thunderbolt media (or network) is obligatory long before the internal volume approaches capacity.** Rotation and archival must preserve cryptographic chain continuity via checkpoint semantics.

**The single physical Mac mini is a complete failure domain:**

- SSD failure → machine replacement + restore from external trust anchor.
- Logic-board / SoC failure → same.
- Accidental deletion, FileVault key loss, or macOS update that bricks the volume → same.
- Thermal or power-supply failure is rare given the 25–40 W envelope, but still requires a tested recovery path.

Environment manifests (exact macOS version, Xcode/CLT version, Python version, SQLite version, Qdrant version, model hashes, schema version) must be recorded with every audit checkpoint so that a future investigator can reconstruct the precise runtime that produced any historical evidence.

---

### 10. Verification Independence and Evidence Durability

All original requirements retained. Independent verifier should preferably run on a second machine or at least a second user/process with no write access to the production store.

---

### 11. Privacy, Retention, and the 2036 Guarantee

All original ten guarantees retained. Given the non-upgradeable 16 GB ceiling and fixed SSD, **the strongest practical guarantee by 2036 is that a failed Mac mini can be replaced by another Apple Silicon machine and the external trust-anchor chain can still be verified and restored.**

---

### 12. Formal Invariants (Executable Where Possible)

All original audit, runtime, memory, operational and replay invariants remain in force. **Additional M1-specific invariants:**

1. Unified-memory pressure is monitored; swap activity above a defined threshold forces degraded mode.
2. Internal SSD free space is monitored with the same urgency as cryptographic chain integrity.
3. Any Docker/Qdrant restart is followed by an independent vector-store integrity check before the service is declared healthy.

---

### 13. Adversarial Test Matrix

All original rows retained. Additional Mac mini M1 rows:

| Scenario | Expected Property |
|---|---|
| Sustained 4P+4E core load for >30 min | Fan remains near-silent; no thermal throttling of critical path |
| 8 GB configuration under concurrent SQLite + Qdrant + model | Explicit degraded mode or admission control, never silent swap thrashing |
| Internal SSD free space <5 % | Transition to `AUDIT_WRITE_BLOCKED` |
| Qdrant container restart on 16 kB-page host | Vectors remain non-zero; integrity check passes |
| Sudden wall-power removal | WAL recovery yields continuous, verifiable chain or explicit break |
| macOS major version upgrade | Environment manifest records the change; historical verification still succeeds |

---

### 14. Priority Ordering After Inversion

P0 remains unchanged (external trust anchor, canonical schema, independent verification, physical isolation of test from production, etc.).

**Additional P0 item for the Mac mini M1:**

Documented and tested restore procedure from external trust anchor onto a second Apple Silicon machine of equal or greater capability.

---

### 15. What Must Not Be Built Yet

All original guidance retained. **Do not add complexity that assumes upgradeable RAM, replaceable SSDs, or multi-socket memory architectures that the M1 simply does not possess.**

---

### 16. Recursive Controller Question and Final Synthesis

Authority separation remains:

```
ACTION PRODUCER
  → AUTHORITY
  → EXECUTOR
  → INDEPENDENT VERIFIER
  → EVIDENCE
  → AUDIT
  → EXTERNAL CHECKPOINT
```

**Final synthesis (Mac mini M1 edition):**

> Observation is execution; verification is execution; internal consistency is not external authenticity; cached truth expires when its underlying state changes; tests with production access are actors in the threat model; the Mac mini M1's non-upgradeable unified memory and soldered SSD are hard physical constraints; and the ten-year property is not that MSB never breaks, but that when it does, its critical guarantees remain detectable, bounded, recoverable from an external trust anchor, and independently verifiable on a replacement Apple Silicon host.

---

### Deliverable Status

This document (v1.1) constitutes the complete extended axiom inversion pass together with the storage-semantics enforcement contracts and the full set of Mac mini (M1, 2020) specialist constraints required to turn the inversions into a deliverable product. All subsequent implementation work is gated by the P0–P3 priorities and the adversarial test matrix. No claim of strong audit integrity or ten-year recoverability is permitted until the corresponding mechanical controls, external trust-anchor procedures, and M1-specific integrity checks exist and have been independently verified.
