# MSB Cockpit Build-Verification Interrogation Set

**Build-verification interrogation set — 104 gates + final open-ended trace.**  
**Version 1.0 — 24 September 2026**  
BlackSwanLabz / Systems Architecture & Epistemic Assurance

---

**Usage:** Treat each question as a gate. A "yes" without an artifact — code, config, test result, runbook, or independent verifier output — is not an answer. This set is the verification harness for the MSB Cockpit Extended Axiom Inversion (v1.1, 2026-09-24).

---

## 1. Production Runtime and Hardware Baseline

1. Is the Mac mini (M1, 2020) the sole production runtime, with model identifiers `Macmini9,1` / `A2348`?
2. Is the SoC confirmed as Apple M1, 5 nm (TSMC N5), 16 billion transistors?
3. Is the CPU confirmed as 8 cores: 4 performance (Firestorm) up to 3.2 GHz + 4 efficiency (Icestorm) up to ≈2.06 GHz?
4. Is the GPU confirmed as 8-core Apple GPU with no discrete eGPU support?
5. Is the Neural Engine confirmed as 16-core, 11 TOPS FP16?
6. Is unified memory confirmed as LPDDR4X-4266, 128-bit bus, 68.25 GB/s theoretical bandwidth, configurable only at purchase as 8 GB or 16 GB, soldered, and not user-upgradeable?
7. Is production minimum 16 GB, and is an 8 GB unit explicitly treated as swap-prone under concurrent SQLite + Qdrant + model + Cockpit load?
8. Is storage confirmed as soldered PCIe 4.0 NVMe SSD, Apple AP0256 family or equivalent, 256 GB / 512 GB / 1 TB / 2 TB, non-upgradeable, with sequential reads ≈2.9–3.3 GB/s?
9. Is cooling confirmed as single axial fan max ≈4500 RPM + aluminium heat-spreader, with chassis cool and fan essentially inaudible under normal operation?
10. Is power confirmed as idle ≈5–7 W, full sustained load ≈25–40 W, internal PSU 150 W?
11. Is I/O confirmed as 2× Thunderbolt / USB 4 (40 Gb/s), 2× USB-A (5 Gb/s), HDMI 2.0, Gigabit Ethernet with 10 GbE optional at purchase, 3.5 mm headphone, maximum two simultaneous displays?
12. Is networking confirmed as Wi-Fi 6 (802.11ax) and Bluetooth 5.0?
13. Are dimensions/weight confirmed as 197 × 197 × 36 mm, 1.2 kg?
14. Are these long-term implications accepted as hard constraints: memory pressure irreversible; 8 GB swaps aggressively; SSD capacity fixed; no path to more Thunderbolt ports, more RAM, or larger internal SSD without replacing the entire machine; unified memory means CPU, GPU, and Neural Engine contend for the same physical pool; large model weights, vector indexes, and SQLite page cache compete directly?

## 2. Purpose, Scope, and Governing Requirement

15. Was the original event-loop starvation caused by synchronous audit verification inside the Cockpit request path identified and addressed?
16. Has axiom inversion expanded that single failure mode into a full causal graph covering resource contention, cache coherence, audit authenticity, recovery, operator authority, and physical-machine failure?
17. Is every critical guarantee enforced by storage semantics, cryptographic structure, or independent mechanical isolation — never by developer discipline or configuration convention alone?
18. Are the Mac mini M1's non-upgradeable unified memory, soldered SSD, single-fan thermal design, and Apple Silicon page-size/Docker behaviour treated as first-class constraints?

## 3. Append-Only Storage Semantics — VI.8

19. Is the axiom "the application never deletes audit records" treated as only one layer of authority?
20. Is the inverted threat model explicit for: adversary or operational error deleting the SQLite file, truncating the JSONL file, modifying permissions, replacing the directory, restoring an older backup, mounting a different filesystem, or rewriting records outside the application process?
21. Is the authoritative audit store an append-only, write-once log?
22. Is physical deletion or truncation of any committed record impossible without breaking cryptographic chain continuity that is independently verifiable?
23. Does each record carry a monotonic sequence number, a cryptographic hash of its canonical serialisation, and the hash of its immediate predecessor?
24. Can a verifier that does not share the producer's code base detect any gap, reordering, or substitution?
25. Is the live store periodically checkpointed to an external, independently controlled trust anchor — offline Thunderbolt medium, remote WORM volume, or hardware-rooted log?
26. Does the checkpoint contain the current head hash and sequence?
27. Is replacement of the live store rejected unless the external anchor still validates?
28. Are filesystem permissions and mount options set so that the process writing the audit log has no delete or truncate capability on committed segments?
29. Does rotation occur only by renaming completed segments into an archive directory that is itself append-only from the application's perspective?
30. Are backups themselves append-only and do they carry independent integrity proofs?
31. Does a restore operation re-verify the entire chain against the external anchor before the restored store is accepted as authoritative?
32. Because the internal SSD is soldered and non-replaceable, is the external trust-anchor medium treated as a first-class failure domain and rotated on a documented schedule?

## 4. SQLite and the Audit Store — VI.9

33. Is SQLite treated as only one representation of history, not the authoritative audit store?
34. Are these distinct failure modes explicitly covered: power loss, WAL recovery, disk-full conditions, page corruption, incomplete backups, concurrent copies, schema migration failures, and SQLite version upgrades?
35. Is SQLite page cache sizing constrained because unified memory makes it compete directly with Qdrant indexes and local model weights?
36. Is aggressive cache sizing prohibited where it can induce swap and destroy latency guarantees?
37. Is continuous high-rate audit writing rate-limited or off-loaded because the internal SSD has finite write endurance?
38. Has power-loss testing included the actual behaviour of the Apple NVMe controller under abrupt wall-power removal?
39. Are fsync and WAL durability settings verified, independent of the single-fan design?
40. Are the required storage contracts met: forced power-loss simulation; disk-full transition to `AUDIT_WRITE_BLOCKED` / `SAFE_MODE`; independent detection of corrupted pages or WAL; versioned migrations that preserve both pre- and post-migration chain heads?

## 5. Backup and Recovery — VI.10

41. Is a backup treated as an untested hypothesis until it has been restored?
42. Is it accepted that on the Mac mini, the only practical restore path for a failed internal SSD is full machine replacement followed by restore from the external trust anchor?
43. Has every backup/restore procedure been exercised on a second Mac mini or equivalent Apple Silicon host before being declared production-ready?

## 6. Cache, Executor, and Resource Containment

44. Is state generation stored in the same append-only log, with TTL remaining only an emergency fallback?
45. Given the M1's 4P + 4E cores and at most 16 GB shared memory, is executor saturation treated as a first-order risk?
46. Are explicit concurrency limits, per-panel budgets, queue-depth metrics, and admission control mandatory and implemented?
47. Do self-probes and observability operate under a hard resource budget?
48. Is the Cockpit never the sole observer of the Cockpit?

## 7. Test Isolation and Epistemic Separation

49. Are production paths, credentials, sockets, and filesystem mounts physically inaccessible to test processes?
50. On a single-machine deployment, is isolation provided by a separate volume, a second physical machine, or a tightly controlled virtualisation boundary that respects the M1's 16 kB page size and Docker behaviour?

## 8. Replay, Audit Schema, and Canonicalisation

51. Are all original replay, audit schema, and canonicalisation requirements retained?
52. Does model identity record whether the model ran natively on Apple Silicon or under Rosetta translation?

## 9. UI, Operator, and Emergency Control

53. Are all original UI, operator, and emergency control requirements retained?
54. Is `localhost` explicitly insufficient for authorisation?
55. Is operator authority separated from action production, execution, and verification?

## 10. Supervisor, Restart, Memory, and Dependencies

56. Is `launchd` the native supervisor on macOS?
57. Are crash-loop detection, restart backoff, health-gated restart, and safe-mode entry implemented inside the `launchd` job definitions or a thin wrapper that `launchd` supervises?
58. For Qdrant/Docker on Apple Silicon, are 16 kB pages accounted for?
59. Are jemalloc aborts with "Unsupported system page size" avoided?
60. Are native arm64 images or images known to handle 16 kB pages preferred?
61. Are named Docker volumes used instead of bind mounts for Qdrant storage, given observed vector-zeroing bugs on macOS bind mounts?
62. Is an independent integrity check of the vector store required after every restart before the service is declared healthy?
63. Is there an external watchdog that can restart Docker itself, given intermittent Docker Desktop daemon-start failures?
64. Does memory carry full provenance?
65. Is vector similarity never treated as truth authority?
66. Is unbounded memory growth forbidden, with mandatory retention, decay, and quarantine policies?

## 11. Storage Growth, Archival, and Physical Machine

67. Is disk-full behaviour exactly `NORMAL → LOW_SPACE → CRITICAL_SPACE → AUDIT_WRITE_BLOCKED → SAFE_MODE`?
68. Because the internal SSD cannot be enlarged, is archival to external Thunderbolt media or network obligatory long before the internal volume approaches capacity?
69. Do rotation and archival preserve cryptographic chain continuity via checkpoint semantics?
70. Is the single physical Mac mini treated as a complete failure domain?
71. Are SSD failure, logic-board/SoC failure, accidental deletion, FileVault key loss, and a macOS update that bricks the volume all mapped to: machine replacement + restore from external trust anchor?
72. Is thermal or power-supply failure treated as rare but still covered by a tested recovery path?
73. Are environment manifests recorded with every audit checkpoint: exact macOS version, Xcode/CLT version, Python version, SQLite version, Qdrant version, model hashes, and schema version?
74. Can a future investigator reconstruct the precise runtime that produced any historical evidence from those manifests?

## 12. Verification Independence and Evidence Durability

75. Are all original verification independence and evidence durability requirements retained?
76. Does the independent verifier preferably run on a second machine, or at least as a second user/process with no write access to the production store?

## 13. Privacy, Retention, and the 2036 Guarantee

77. Are all original ten guarantees retained?
78. Given the non-upgradeable 16 GB ceiling and fixed SSD, is the strongest practical guarantee by 2036 that a failed Mac mini can be replaced by another Apple Silicon machine and the external trust-anchor chain can still be verified and restored?

## 14. Formal Invariants

79. Are all original audit, runtime, memory, operational, and replay invariants still in force?
80. Is unified-memory pressure monitored, with swap activity above a defined threshold forcing degraded mode?
81. Is internal SSD free space monitored with the same urgency as cryptographic chain integrity?
82. Is any Docker/Qdrant restart followed by an independent vector-store integrity check before the service is declared healthy?

## 15. Adversarial Test Matrix

83. Are all original adversarial test rows retained?
84. Sustained 4P+4E core load for >30 minutes: does the fan remain near-silent and is there no thermal throttling of the critical path?
85. 8 GB configuration under concurrent SQLite + Qdrant + model: is there explicit degraded mode or admission control, never silent swap thrashing?
86. Internal SSD free space <5%: does the system transition to `AUDIT_WRITE_BLOCKED`?
87. Qdrant container restart on a 16 kB-page host: do vectors remain non-zero and does the integrity check pass?
88. Sudden wall-power removal: does WAL recovery yield a continuous, verifiable chain or an explicit break?
89. macOS major version upgrade: does the environment manifest record the change, and does historical verification still succeed?

## 16. Priority Ordering After Inversion

90. Is P0 unchanged: external trust anchor, canonical schema, independent verification, physical isolation of test from production, etc.?
91. Is there an additional P0 item: documented and tested restore procedure from external trust anchor onto a second Apple Silicon machine of equal or greater capability?

## 17. What Must Not Be Built Yet

92. Is all original guidance retained?
93. Are you avoiding complexity that assumes upgradeable RAM, replaceable SSDs, or multi-socket memory architectures that the M1 does not possess?

## 18. Recursive Controller and Final Synthesis

94. Is authority separation exactly: `ACTION PRODUCER → AUTHORITY → EXECUTOR → INDEPENDENT VERIFIER → EVIDENCE → AUDIT → EXTERNAL CHECKPOINT`?
95. Is observation treated as execution?
96. Is verification treated as execution?
97. Is internal consistency explicitly not external authenticity?
98. Does cached truth expire when its underlying state changes?
99. Are tests with production access treated as actors in the threat model?
100. Are the Mac mini M1's non-upgradeable unified memory and soldered SSD treated as hard physical constraints?
101. Is the ten-year property defined as: when MSB breaks, its critical guarantees remain detectable, bounded, recoverable from an external trust anchor, and independently verifiable on a replacement Apple Silicon host?

## 19. Deliverable Status Gate

102. Is this document v1.1 confirmed as the complete extended axiom inversion pass, together with the storage-semantics enforcement contracts and the full set of Mac mini (M1, 2020) specialist constraints required to turn the inversions into a deliverable product?
103. Is all subsequent implementation work gated by the P0–P3 priorities and the adversarial test matrix?
104. Is any claim of strong audit integrity or ten-year recoverability prohibited until the corresponding mechanical controls, external trust-anchor procedures, and M1-specific integrity checks exist and have been independently verified?

---

## Final Open-Ended Question

Given every answer above, walk me through — end to end — how one committed audit record is created, canonicalised, sequenced, hash-chained, written, rotated, checkpointed to the external trust anchor, recovered after power loss, restored after internal SSD failure, migrated across a macOS major upgrade, independently verified on a replacement Apple Silicon machine, and still proven authentic in 2036. For each step, name the exact mechanical enforcement — storage semantics, cryptographic structure, or independent isolation — and demonstrate that no developer discipline, configuration convention, or unexercised restore procedure is load-bearing. If any step depends on assumption, habit, or an untested path, say so explicitly.
