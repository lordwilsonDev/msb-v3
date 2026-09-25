# Blueprint citation index

> **Generated** by `scripts/blueprint_index.py` — do not edit by hand.
> Regenerate with `python3 scripts/blueprint_index.py --write`;
> `--check` fails if this file has drifted from the tree.

## How to read this

A `§N` citation here names a section of *some* design document, and the number
alone does not say which. Ranges overlap across the documents in `docs/`, and
the owners of the most-cited labels are not in this repository. Cite as
`document §N` — e.g. `docs/project-map.md §2` — never a bare `§2`.

- Numbered documents under `docs/`: **47**
- External or unfound documents cited by number: **11**, of which **5** could not be located at all
- `§` citations in the tree: **763**
  — anchored 211 · same-line 17 · file-default 67 · own-section 3 · single-candidate 0 · ambiguous 228 · ambiguous-label 235 · unresolved 2
- Of those, **214** resolve only to a document outside this repository

Statuses — how each citation was resolved, in order of how much the citation
itself says:

- **anchored** — it names its document inline, in its own clause
  (`unified-architecture §27`).
- **same-line** — the line names the document, in an earlier clause. A table
  cell like *"... (Steward blueprint Layer 02) ... enforces §53"* reads this
  way. Better than a header, weaker than the citation's own words.
- **file-default** — it is bare, and the file's header names exactly one
  document. Inherited context, not the citation's own words.
- **own-section** — bare, and the number is a heading of the file's own
  document (`§0.5` inside the document that defines §0.5).
- **single-candidate** — bare, and exactly one document owns that number.
- **ambiguous** — bare, and several in-repo documents own it.
- **ambiguous-label** — the label (`blueprint`, `spec`) names nothing specific.
- **unresolved** — no owner found anywhere.

### What `single-candidate` does not mean

It is not proof. Five of the cited documents are outside this repo with
unattested numbering, so a number can have exactly one in-repo owner and still
mean something else — and this tree contained a provable case of it:

- `docs/releases/HARDENING-AUDIT.md` cited `§24` of a document it names on the
  previous line: `docs/desktop-architecture.md`. The index resolved it to
  `docs/audits/forensic-grill-2026-09-02.md` §24 (`DEPENDENCY FAILURE`) — a
  forensic audit about dependency failure, for a claim about memory authority.
- The named document has **no numbered sections at all** (its headings are
  titles: `## Authority`), so the citation could not be followed anywhere.

The citation was wrong and the resolution was wrong, and nothing in the number
revealed either. That citation names its section now. Treat `single-candidate`
as a lead to check, not an answer: every site that landed here needed the
document named, not the number trusted.

## Citation vocabulary

Every label that appears immediately before a `§` in this tree, and the
document it names. **Readable from a checkout?** is the column that decides
whether a citation is something a reader can follow. Anything whose owner is
*not found* or *external* is a citation no reader of this repository can follow.

| Label | Owner | Where it lives | Sections | Cites | Readable from a checkout? |
| --- | --- | --- | --- | ---: | --- |
| `ail–moie steward blueprint` | Steward blueprint (AIL-MoIE Project Steward) | vault | 91 numbered sections | 1 | no — external |
| `ail-moie steward blueprint` | Steward blueprint (AIL-MoIE Project Steward) | vault | 91 numbered sections | 0 | no — external |
| `steward blueprint` | Steward blueprint (AIL-MoIE Project Steward) | vault | 91 numbered sections | 3 | no — external |
| `sovereign architecture v4.0` | Sovereign Architecture v4.0 — cited as *spec* | unfound | unattested — the document could not be located | 17 | **no — not found** |
| `sovereign-architecture` | Sovereign Architecture v4.0 — cited as *spec* | unfound | unattested — the document could not be located | 46 | **no — not found** |
| `sovereign architecture` | Sovereign Architecture v4.0 — cited as *spec* | unfound | unattested — the document could not be located | 0 | **no — not found** |
| `meta-system blueprint` | Meta-System blueprint — cited as *Meta-System blueprint* | unfound | unattested — cited for §5, §6, §7, §13, §14, §15 | 4 | **no — not found** |
| `wrongness-engine blueprint` | Wrongness Engine blueprint AIB-001 | vault | 25 numbered sections | 0 | no — external |
| `aib-001` | Wrongness Engine blueprint AIB-001 | vault | 25 numbered sections | 2 | no — external |
| `unified-architecture` | Unified Architecture — cited as *unified-architecture* | unfound | unattested — no located document has the cited §5-§31 | 50 | **no — not found** |
| `unified architecture` | Unified Architecture — cited as *unified-architecture* | unfound | unattested — no located document has the cited §5-§31 | 0 | **no — not found** |
| `deliverable 02` | Deliverable 02 (identity shadow) | vault | no numbered headings — §10 cannot be resolved | 8 | no — external |
| `deliverable-2` | Deliverable 02 (identity shadow) | vault | no numbered headings — §10 cannot be resolved | 0 | no — external |
| `del02` | Deliverable 02 (identity shadow) | vault | no numbered headings — §10 cannot be resolved | 0 | no — external |
| `doctoral research blueprint` | MSB-v3 Doctoral Research Blueprint | vault | 27 numbered sections | 0 | no — external |
| `research blueprint` | MSB-v3 Doctoral Research Blueprint | vault | 27 numbered sections | 9 | no — external |
| `sovereign-agentic-runtime-build-spec` | Sovereign-Agentic-Runtime-Build-Spec v1 | vault | 18 numbered headings (§0-§9, with §3.1-§3.5) | 1 | no — external |
| `build-spec` | Sovereign-Agentic-Runtime-Build-Spec v1 | vault | 18 numbered headings (§0-§9, with §3.1-§3.5) | 1 | no — external |
| `north star, blueprint` | North Star / Dream Big Blue blueprint | unfound | unattested — cited for §20, §25, §27 | 1 | **no — not found** |
| `north-star blueprint` | North Star / Dream Big Blue blueprint | unfound | unattested — cited for §20, §25, §27 | 0 | **no — not found** |
| `dream big blue` | North Star / Dream Big Blue blueprint | unfound | unattested — cited for §20, §25, §27 | 0 | **no — not found** |
| `doc 1` | S-AOS Guardian — cited as *doc 1* | vault | unverified — the file was located by name only | 4 | no — external |
| `doc 4` | S-AOS Guardian — cited as *doc 4* | unfound | unattested | 5 | **no — not found** |
| `convergence blueprint` | `docs/blueprints/convergence-to-12/blueprint.md` | repo | 8 sections (8 top-level, 1–8) | 13 | yes |
| `convergence-to-12` | `docs/blueprints/convergence-to-12/blueprint.md` | repo | 8 sections (8 top-level, 1–8) | 0 | yes |
| `2026-08-11-adaptive-build-environment` | `docs/blueprints/2026-08-11-adaptive-build-environment.md` | repo | 9 sections (7 top-level, 0–6) | 2 | yes |
| `2026-09-09-production-hardening` | `docs/blueprints/2026-09-09-production-hardening.md` | repo | 14 sections (8 top-level, 1–8) | 1 | yes |
| `production-hardening blueprint` | `docs/blueprints/2026-09-09-production-hardening.md` | repo | 14 sections (8 top-level, 1–8) | 5 | yes |
| `production hardening blueprint` | `docs/blueprints/2026-09-09-production-hardening.md` | repo | 14 sections (8 top-level, 1–8) | 0 | yes |
| `governance-hardening` | `docs/blueprints/governance-hardening.md` | repo | 14 sections (14 top-level, 0–13) | 0 | yes |
| `adaptive-build-environment` | `docs/blueprints/2026-08-11-adaptive-build-environment.md` | repo | 9 sections (7 top-level, 0–6) | 0 | yes |
| `m1-governance-node-architecture` | `docs/blueprints/plans/m1-governance-node-architecture.md` | repo | 5 sections (5 top-level, 1–5) | 3 | yes |
| `m1-core-loop` | `docs/blueprints/convergence-to-12/M1-core-loop.md` | repo | 6 sections (6 top-level, 1–6) | 0 | yes |
| `live-loop-composition-plan` | `docs/blueprints/convergence-to-12/live-loop-composition-plan.md` | repo | 7 sections (7 top-level, 1–7) | 0 | yes |
| `forensic-grill` | `docs/audits/forensic-grill-2026-09-02.md` | repo | 37 sections (33 top-level, 1–33) | 24 | yes |
| `forensic grill` | `docs/audits/forensic-grill-2026-09-02.md` | repo | 37 sections (33 top-level, 1–33) | 0 | yes |
| `forensic-build-audit` | `docs/audits/forensic-build-audit-2026-08-15.md` | repo | 20 sections (20 top-level, 1–20) | 0 | yes |
| `project-map` | `docs/project-map.md` | repo | 22 sections (22 top-level, 1–22) | 3 | yes |
| `task-contract-v1.md` | `docs/task-contract-v1.md` | repo | 12 sections (12 top-level, 1–12) | 7 | yes |
| `task-contract-v1` | `docs/task-contract-v1.md` | repo | 12 sections (12 top-level, 1–12) | 0 | yes |
| `task-contract` | `docs/task-contract-v1.md` | repo | 12 sections (12 top-level, 1–12) | 1 | yes |
| *any ambiguous label* | **cannot be resolved** — see the two notes below | — | — | 235 | no |

### `blueprint` — a label, not a document

At least five documents in this tree are called a blueprint — Steward,
Meta-System, North Star, the convergence blueprint at
`docs/blueprints/convergence-to-12/blueprint.md`, and the plan documents
under `docs/blueprints/` — and their ranges overlap completely. Every
citation written as `blueprint §N` has to be judged on its context.

### `spec` — the same problem, five times over

`spec` is the most-cited label in the tree and refers to at least five
different documents:

| Reading | Evidence | Verdict |
| --- | --- | --- |
| Sovereign Architecture v4.0 | §4.2.1/§4.2.2/§4.2.3 in `agent/handle.py` and `docs/SURFACE.md`; the build audit names the title | the only reading with a §4.2.x scheme — but the document is not locatable |
| the Paseo MCP protocol spec | `docs/paseo-adapter-v1.md` cites `spec §7` and `spec §32`, and its own numbering stops at §11 | external third party |
| the conversation producer spec | `scripts/probe_conversation_e2e.py`: `producer spec §11` | probably `docs/conversation-ledger-producer-v1.md` |
| the conversation E2E harness spec | `tests/test_probe_self_test.py`: `spec §7` | probably `docs/conversation-e2e-harness-v1.md` |
| the kernel spec | a job output under the gitignored `ai-workspace/` | not in this tree |

Because a bare `spec` cannot choose between these, it is reported as an 
ambiguous label. Write the document name instead: the qualified forms 
`sovereign-architecture` and `sovereign architecture v4.0` do resolve.

## Numbered documents in this repository

Discovered by structure: a document qualifies when it has at least 3 numbered headings and lives under `docs/`. The count columns
separate citations that *name* the document from ones that merely land in its
range:

- **Named** — the citation, or its line, writes this document's name. Its,
  to the same standard as the `anchored` and `same-line` statuses.
- **File default** — the citation is bare, but the file it sits in names this
  document in its header. Probably its, and checkable when the document is here.
- **Own section** — bare, and the number is a heading of the document the
  citation sits in. The one bare form that can only mean itself.
- **Sole candidate** — a bare `§N` that only this document owns. Probably its,
  subject to the caveat above.
- **Contested** — a bare `§N` this document shares with others. Unknown which.

| Document | Numbering | Named | File default | Own section | Sole candidate | Contested |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `docs/PRODUCTION-READINESS.md` | 3 sections (3 top-level, 1–3) | 0 | 0 | 0 | 0 | 20 |
| `docs/QUICKSTART.md` | 6 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |
| `docs/audits/forensic-build-audit-2026-08-15.md` | 20 sections (20 top-level, 1–20) | 0 | 0 | 0 | 0 | 222 |
| `docs/audits/forensic-grill-2026-09-02.md` | 37 sections (33 top-level, 1–33) | 24 | 0 | 0 | 0 | 224 |
| `docs/audits/msb-cockpit-audit-schema-v1.md` | 16 sections (13 top-level, 1–13) | 0 | 0 | 0 | 0 | 191 |
| `docs/audits/msb-cockpit-build-plan-v1-2026-09-24.md` | 11 sections (11 top-level, 1–11) | 0 | 0 | 0 | 0 | 168 |
| `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md` | 19 sections (19 top-level, 1–19) | 0 | 0 | 0 | 0 | 222 |
| `docs/audits/msb-cockpit-execution-log-v1-2026-09-24.md` | 4 sections (4 top-level, 1–4) | 0 | 0 | 0 | 0 | 47 |
| `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md` | 35 sections (12 top-level, 1–12) | 0 | 0 | 0 | 0 | 187 |
| `docs/audits/msb-cockpit-extended-axiom-inversion-2026-09-24.md` | 16 sections (16 top-level, 1–16) | 0 | 0 | 0 | 0 | 201 |
| `docs/audits/msb-cockpit-plan-adversarial-review-v1-2026-09-24.md` | 6 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |
| `docs/audits/smi-017-forensic-review/current_architecture.md` | 5 sections (5 top-level, 1–5) | 0 | 0 | 0 | 0 | 64 |
| `docs/audits/smi-017-forensic-review/scale_failure_analysis.md` | 7 sections (7 top-level, 1–7) | 0 | 0 | 0 | 0 | 104 |
| `docs/audits/smi-017-forensic-review/sovereign_agent_factory_phase2.md` | 9 sections (9 top-level, 1–9) | 0 | 0 | 0 | 0 | 151 |
| `docs/blueprints/2026-08-11-adaptive-build-environment.md` | 9 sections (7 top-level, 0–6) | 2 | 1 | 3 | 0 | 88 |
| `docs/blueprints/2026-09-09-production-hardening.md` | 14 sections (8 top-level, 1–8) | 8 | 3 | 0 | 0 | 142 |
| `docs/blueprints/2026-09-25-agent-control-plane.md` | 18 sections (14 top-level, 1–14) | 0 | 0 | 0 | 0 | 200 |
| `docs/blueprints/convergence-to-12/M1-core-loop.md` | 6 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |
| `docs/blueprints/convergence-to-12/blueprint.md` | 8 sections (8 top-level, 1–8) | 14 | 0 | 0 | 0 | 142 |
| `docs/blueprints/convergence-to-12/live-loop-composition-plan.md` | 7 sections (7 top-level, 1–7) | 0 | 0 | 0 | 0 | 104 |
| `docs/blueprints/governance-hardening.md` | 14 sections (14 top-level, 0–13) | 0 | 0 | 0 | 0 | 191 |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md` | 5 sections (5 top-level, 1–5) | 0 | 0 | 0 | 0 | 64 |
| `docs/blueprints/plans/2026-08-13-sovereign-node-build-plan.md` | 18 sections (11 top-level, 1–11) | 0 | 0 | 0 | 0 | 168 |
| `docs/blueprints/plans/2026-08-13-vesta-msb-integration-spec.md` | 8 sections (8 top-level, 1–8) | 0 | 0 | 0 | 0 | 142 |
| `docs/blueprints/plans/2026-08-13-vesta-node-full-build-plan.md` | 8 sections (8 top-level, 1–8) | 0 | 0 | 0 | 0 | 142 |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md` | 8 sections (8 top-level, 1–8) | 0 | 0 | 0 | 0 | 142 |
| `docs/blueprints/plans/2026-08-14-wireguard-preflight-adr.md` | 6 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |
| `docs/blueprints/plans/2026-09-20-phone-contact-notify-channel.md` | 15 sections (9 top-level, 1–9) | 0 | 0 | 0 | 0 | 151 |
| `docs/blueprints/plans/2026-09-23-audit-remediation-four-findings.md` | 9 sections (9 top-level, 1–9) | 0 | 0 | 0 | 0 | 151 |
| `docs/blueprints/plans/m1-governance-node-architecture.md` | 5 sections (5 top-level, 1–5) | 4 | 6 | 0 | 0 | 64 |
| `docs/conversation-e2e-harness-v1.md` | 10 sections (10 top-level, 1–10) | 0 | 0 | 0 | 0 | 161 |
| `docs/conversation-envelope-v1.md` | 12 sections (12 top-level, 1–12) | 0 | 0 | 0 | 0 | 183 |
| `docs/conversation-ledger-producer-v1.md` | 12 sections (12 top-level, 1–12) | 0 | 0 | 0 | 0 | 183 |
| `docs/deep-pass-2026-08-08.md` | 9 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |
| `docs/forensic-audit-2026-09-11.md` | 7 sections (7 top-level, 1–7) | 0 | 0 | 0 | 0 | 104 |
| `docs/meta/routing-thesis-assessment.md` | 3 sections (3 top-level, 1–3) | 0 | 0 | 0 | 0 | 20 |
| `docs/operations/disaster-recovery.md` | 4 sections (4 top-level, 1–4) | 0 | 0 | 0 | 0 | 47 |
| `docs/operations/vesta-security-review.md` | 3 sections (3 top-level, 1–3) | 0 | 0 | 0 | 0 | 20 |
| `docs/operations/yubikey-piv-anchor.md` | 9 sections (9 top-level, 1–9) | 0 | 0 | 0 | 0 | 151 |
| `docs/paseo-adapter-v1.md` | 11 sections (11 top-level, 1–11) | 0 | 0 | 0 | 0 | 168 |
| `docs/project-map.md` | 22 sections (22 top-level, 1–22) | 3 | 0 | 0 | 0 | 224 |
| `docs/pull-signature-and-access.md` | 4 sections (4 top-level, 1–4) | 0 | 0 | 0 | 0 | 47 |
| `docs/releases/NEXT.md` | 10 sections (10 top-level, 1–10) | 0 | 0 | 0 | 0 | 161 |
| `docs/superpowers/specs/2026-09-05-production-gate.md` | 9 sections (9 top-level, 1–9) | 0 | 0 | 0 | 0 | 151 |
| `docs/task-contract-v1.md` | 12 sections (12 top-level, 1–12) | 8 | 8 | 0 | 0 | 183 |
| `docs/v3.2-plan.md` | 10 sections (10 top-level, 1–10) | 0 | 0 | 0 | 0 | 161 |
| `docs/v3.3-plan.md` | 6 sections (6 top-level, 1–6) | 0 | 0 | 0 | 0 | 88 |

## Number collisions among documents in this repository

A bare `§N` in this repository is a coin flip for any number below §21, because
nearly every document here numbers its sections from 1. This is the list to
consult before writing a citation. It covers the numbers this tree actually
cites, not every number in existence.

Of the 51 distinct numbers this tree cites:

- **24** are owned by more than one in-repo document
- **18** by exactly one
- **9** by none — those citations can only point outside the repo

### Numbers no bare citation can resolve

The 24 shared numbers. Those with more than 6 owners are
summarised; the rest are listed with their owners.

| § | In-repo owners | Owners |
| --- | ---: | --- |
| §1 | 47 | too many to list — see the registry |
| §2 | 47 | too many to list — see the registry |
| §3 | 47 | too many to list — see the registry |
| §4 | 44 | too many to list — see the registry |
| §5 | 41 | too many to list — see the registry |
| §6 | 38 | too many to list — see the registry |
| §7 | 31 | too many to list — see the registry |
| §8 | 28 | too many to list — see the registry |
| §9 | 23 | too many to list — see the registry |
| §10 | 18 | too many to list — see the registry |
| §11 | 15 | too many to list — see the registry |
| §12 | 12 | too many to list — see the registry |
| §13 | 8 | too many to list — see the registry |
| §2.2 | 2 | `docs/blueprints/plans/2026-09-20-phone-contact-notify-channel.md`, `docs/deep-pass-2026-08-08.md` |
| §5.4 | 2 | `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md` |
| §14 | 6 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/audits/msb-cockpit-extended-axiom-inversion-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md`, `docs/project-map.md` |
| §15 | 5 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/audits/msb-cockpit-extended-axiom-inversion-2026-09-24.md`, `docs/project-map.md` |
| §16 | 5 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/audits/msb-cockpit-extended-axiom-inversion-2026-09-24.md`, `docs/project-map.md` |
| §17 | 4 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/project-map.md` |
| §18 | 4 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/project-map.md` |
| §19 | 4 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, `docs/project-map.md` |
| §20 | 3 | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/project-map.md` |
| §21 | 2 | `docs/audits/forensic-grill-2026-09-02.md`, `docs/project-map.md` |
| §22 | 2 | `docs/audits/forensic-grill-2026-09-02.md`, `docs/project-map.md` |

### Numbers a bare citation resolves on its own

Owned by exactly one in-repo document: §0.5, §0.6, §1.4, §3.2, §3.3, §3.4, §3.6, §7.3, §23, §24, §25, §26, §27, §28, §29, §30, §31, §32.

Still not safe to write bare — an external document may own the same
number, and five of them could not be checked. See `single-candidate` above.

## Citations that do not resolve from a checkout

### Unresolved — 2 site(s)

No document owning the number was found in the repo or the vault, so the citation points at nothing that can be read from here. Check the context before treating these as defects: `§` is also how this tree cites external standards, and most of these are RFC sections — `src/msb_ledger/timestamping.py:33` is `RFC 5652 §5.4`, and `:54` is `RFC 3161 §2.4.2`. Those are correct citations to documents that were never expected to be in this repository.

| Where | § | Label | Resolved by | Resolves to |
| --- | --- | --- | --- | --- |
| `docs/audits/forensic-build-audit-2026-08-15.md:620` | §4.2.1 | `(bare)` | unresolved | — |
| `src/msb_ledger/timestamping.py:54` | §2.4.2 | `(bare)` | unresolved | — |

### Ambiguous — 228 site(s)

Bare `§N` where several in-repo documents own that number.

| Where | § | Label | Resolved by | Resolves to |
| --- | --- | --- | --- | --- |
| `CHANGELOG.md:74` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `CHANGELOG.md:144` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `CHANGELOG.md:233` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `MANIFEST.md:146` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/audits/forensic-build-audit-2026-08-15.md:141` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/audits/forensic-build-audit-2026-08-15.md:255` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `docs/audits/forensic-build-audit-2026-08-15.md:257` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/audits/forensic-build-audit-2026-08-15.md:261` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/audits/forensic-build-audit-2026-08-15.md:262` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/audits/forensic-build-audit-2026-08-15.md:263` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/audits/forensic-build-audit-2026-08-15.md:266` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/audits/forensic-build-audit-2026-08-15.md:485` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `docs/audits/forensic-build-audit-2026-08-15.md:538` | §14 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+3) |
| `docs/audits/forensic-grill-2026-09-02.md:34` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/audits/forensic-grill-2026-09-02.md:35` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/audits/forensic-grill-2026-09-02.md:1173` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/audits/forensic-grill-2026-09-02.md:1181` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `docs/audits/forensic-grill-2026-09-02.md:1182` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/audits/forensic-grill-2026-09-02.md:1338` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `docs/audits/smi-017-forensic-review/sovereign_agent_factory_phase2.md:26` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/audits/smi-017-forensic-review/technical_debt.md:37` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/audits/smi-017-forensic-review/technical_debt.md:98` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:5` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:5` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:9` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:78` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:99` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:101` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:130` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:159` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:366` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:374` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:376` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:392` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `docs/blueprints/2026-09-25-agent-control-plane.md:394` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/convergence-to-12/M1-core-loop.md:97` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:36` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:36` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:64` | §1 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:74` | §1 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:81` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md:93` | §1 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-13-sovereign-node-build-plan.md:237` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md:39` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md:138` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md:138` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md:144` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/blueprints/plans/2026-08-14-close-out-msb-v3.md:219` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/blueprints/plans/2026-08-14-wireguard-preflight-adr.md:7` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/blueprints/plans/2026-08-14-wireguard-preflight-adr.md:54` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-14-wireguard-preflight-adr.md:59` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-14-wireguard-preflight-adr.md:79` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/blueprints/plans/2026-08-16-msb-v3-completion-blueprint.md:145` | §5.4 | `(bare)` | ambiguous | `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md` |
| `docs/conversation-e2e-harness-v1.md:28` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-e2e-harness-v1.md:29` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `docs/conversation-e2e-harness-v1.md:86` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-e2e-harness-v1.md:133` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-e2e-harness-v1.md:180` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/conversation-e2e-harness-v1.md:230` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/conversation-envelope-v1.md:43` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:58` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:83` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `docs/conversation-envelope-v1.md:116` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/conversation-envelope-v1.md:157` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:175` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:218` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:278` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:331` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-envelope-v1.md:332` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-envelope-v1.md:335` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/conversation-envelope-v1.md:336` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/conversation-envelope-v1.md:337` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:10` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:20` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:61` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-ledger-producer-v1.md:86` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/conversation-ledger-producer-v1.md:114` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:178` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:202` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-ledger-producer-v1.md:212` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/conversation-ledger-producer-v1.md:215` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-ledger-producer-v1.md:263` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:263` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/conversation-ledger-producer-v1.md:266` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/conversation-ledger-producer-v1.md:285` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/conversation-ledger-producer-v1.md:287` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/operations/secure-enclave-anchor.md:126` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/operations/vesta-security-review.md:5` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/operations/vesta-security-review.md:121` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/paseo-adapter-v1.md:51` | §15 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+2) |
| `docs/paseo-adapter-v1.md:106` | §1 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/paseo-adapter-v1.md:128` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `docs/project-map.md:8` | §15 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+2) |
| `docs/releases/v0.2.3-baseline.md:53` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/releases/v0.3.0-rc1-baseline.md:40` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/releases/v0.3.0-rc1-baseline.md:52` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:5` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:5` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:222` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:415` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:858` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:858` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:1571` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:1571` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `docs/task-contract-v1.md:32` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/task-contract-v1.md:103` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/task-contract-v1.md:106` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `docs/task-contract-v1.md:108` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/task-contract-v1.md:109` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `docs/task-contract-v1.md:201` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/task-contract-v1.md:205` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/task-contract-v1.md:267` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/task-contract-v1.md:276` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/task-contract-v1.md:303` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `docs/task-contract-v1.md:311` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `docs/task-contract-v1.md:312` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/task-contract-v1.md:319` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/task-contract-v1.md:368` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `docs/task-contract-v1.md:376` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `docs/task-contract-v1.md:387` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `experiments/gov_corpus.py:6` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `experiments/gov_corpus.py:6` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/gov_corpus.py:42` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `experiments/harness_baseline_comparison.py:8` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/harness_baseline_comparison.py:161` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/harness_baseline_comparison.py:217` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/harness_baseline_comparison.py:235` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/harness_cascading_failure.py:372` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `experiments/harness_governance_effectiveness.py:14` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/harness_performance.py:17` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `experiments/harness_performance.py:24` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/harness_performance.py:26` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/harness_performance.py:150` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/harness_performance.py:174` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/harness_sovereignty.py:197` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:4` | §22 | `(bare)` | ambiguous | `docs/audits/forensic-grill-2026-09-02.md`, `docs/project-map.md` |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:7` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:17` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:38` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:86` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:90` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `experiments/reports/MSB-GOV-EVAL-001.md:4` | §22 | `(bare)` | ambiguous | `docs/audits/forensic-grill-2026-09-02.md`, `docs/project-map.md` |
| `experiments/reports/MSB-GOV-EVAL-001.md:22` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `experiments/reports/MSB-GOV-EVAL-001.md:23` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001.md:27` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `experiments/reports/MSB-GOV-EVAL-001.md:27` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `experiments/reports/MSB-GOV-EVAL-001.md:27` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `experiments/reports/MSB-GOV-EVAL-001.md:50` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `experiments/reports/MSB-GOV-EVAL-001.md:50` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `experiments/reports/MSB-GOV-EVAL-001.md:55` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `experiments/reports/MSB-GOV-EVAL-001.md:101` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `experiments/reports/MSB-GOV-EVAL-001.md:101` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `experiments/reports/MSB-GOV-EVAL-001.md:132` | §11 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+12) |
| `experiments/reports/MSB-GOV-EVAL-001.md:132` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/reports/MSB-GOV-EVAL-001.md:148` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `experiments/reports/MSB-GOV-EVAL-001.md:162` | §15 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+2) |
| `experiments/reports/MSB-GOV-EVAL-001.md:162` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001.md:193` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `experiments/reports/MSB-GOV-EVAL-001.md:235` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001.md:235` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001.md:258` | §19 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `experiments/reports/MSB-GOV-EVAL-001.md:306` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `experiments/reports/MSB-GOV-EVAL-001.md:306` | §18 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `research/PLAN.md:27` | §15 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+2) |
| `research/PLAN.md:27` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `research/PLAN.md:50` | §15 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+2) |
| `scripts/probe_conversation_e2e.py:7` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `scripts/probe_conversation_e2e.py:10` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `scripts/probe_conversation_e2e.py:152` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `scripts/probe_conversation_e2e.py:161` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `scripts/probe_conversation_e2e.py:182` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `scripts/probe_conversation_e2e.py:188` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `scripts/probe_conversation_e2e.py:197` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `scripts/probe_conversation_e2e.py:217` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `scripts/probe_conversation_e2e.py:482` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `src/msb_ledger/chain_anchor.py:4` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `src/msb_ledger/timestamping.py:33` | §5.4 | `(bare)` | ambiguous | `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md` |
| `src/msb_ledger/timestamping.py:347` | §5.4 | `(bare)` | ambiguous | `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md` |
| `src/msb_v3/agent/safety.py:144` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `src/msb_v3/agent/safety.py:150` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `src/msb_v3/agent/safety.py:230` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `src/msb_v3/agent/verify.py:128` | §14 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+3) |
| `src/msb_v3/agent/verify.py:149` | §14 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+3) |
| `src/msb_v3/api/agent.py:151` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `src/msb_v3/api/conversation.py:5` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `src/msb_v3/api/system.py:64` | §14 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+3) |
| `src/msb_v3/api/system.py:135` | §14 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+3) |
| `src/msb_v3/conversation/envelope.py:7` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `src/msb_v3/conversation/envelope.py:118` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `src/msb_v3/conversation/envelope.py:128` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `src/msb_v3/conversation/envelope.py:198` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `src/msb_v3/conversation/producer.py:5` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `src/msb_v3/conversation/producer.py:74` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `src/msb_v3/conversation/producer.py:258` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `src/msb_v3/conversation/producer.py:286` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `src/msb_v3/conversation/producer.py:534` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `src/msb_v3/conversation/producer.py:563` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `src/msb_v3/governance/killswitch.py:4` | §13 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+5) |
| `src/msb_v3/harnesses/base.py:31` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `src/msb_v3/meta/translation/__init__.py:4` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `src/msb_v3/mission/__init__.py:3` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `src/msb_v3/mission/__init__.py:3` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `src/msb_v3/mission/models.py:3` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `src/msb_v3/mission/states.py:3` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `src/msb_v3/mission/store.py:3` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `src/msb_v3/mission/store.py:3` | §12 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+9) |
| `src/msb_v3/moie/meta_critic.py:5` | §9 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+20) |
| `src/msb_v3/vesta/approval_watchdog.py:4` | §10 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+15) |
| `tests/agent/paseo/test_paseo_adapter.py:2` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `tests/contracts/test_layered_boundary.py:101` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `tests/gateway/test_gateway_routing.py:95` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `tests/gateway/test_gateway_routing.py:138` | §3 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `tests/gateway/test_route.py:114` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `tests/governance/test_bypass.py:161` | §17 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-build-verification-interrogation-2026-09-24.md`, … (+1) |
| `tests/test_conversation.py:3` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `tests/test_conversation.py:4` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `tests/test_conversation.py:63` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `tests/test_conversation.py:112` | §5 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+38) |
| `tests/test_conversation.py:112` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `tests/test_conversation.py:146` | §6 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+35) |
| `tests/test_conversation.py:207` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `tests/test_cross_producer_regress.py:6` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `tests/test_cross_producer_regress.py:15` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `tests/test_cross_producer_regress.py:130` | §8 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+25) |
| `tests/test_task_contract.py:90` | §2 | `(bare)` | ambiguous | `docs/PRODUCTION-READINESS.md`, `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, … (+44) |
| `tests/test_task_contract.py:163` | §7 | `(bare)` | ambiguous | `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, `docs/audits/msb-cockpit-audit-schema-v1.md`, … (+28) |
| `tests/test_task_contract.py:217` | §4 | `(bare)` | ambiguous | `docs/QUICKSTART.md`, `docs/audits/forensic-build-audit-2026-08-15.md`, `docs/audits/forensic-grill-2026-09-02.md`, … (+41) |
| `tests/uac/test_timestamping.py:6` | §5.4 | `(bare)` | ambiguous | `docs/audits/msb-cockpit-execution-slice-2-plan-v1-2026-09-24.md`, `docs/blueprints/2026-09-25-agent-control-plane.md` |

### Ambiguous label — 235 site(s)

The label (`blueprint`, `spec`) names no specific document, so the number cannot be resolved even in principle.

| Where | § | Label | Resolved by | Resolves to |
| --- | --- | --- | --- | --- |
| `CLAUDE.archive.md:65` | §0.6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `PLAN.md:704` | §53 | `blueprint` | ambiguous-label | `?ambiguous` |
| `PLAN.md:704` | §53 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:465` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:469` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:499` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:503` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:663` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:704` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:736` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:765` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:795` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:834` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:864` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:906` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:911` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:911` | §23 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:911` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:916` | §25 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/audits/forensic-build-audit-2026-08-15.md:963` | §25 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/convergence-to-12/v3-contract.md:62` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/convergence-to-12/v3-contract.md:68` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/convergence-to-12/v4-parking-lot.md:77` | §7.3 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/governance-hardening.md:18` | §53 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/governance-hardening.md:335` | §53 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/plans/2026-08-11-phase0b-the-brakes.md:55` | §0.6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/plans/2026-08-11-phase1-cockpit.md:9` | §1 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/blueprints/plans/2026-08-11-phase1-cockpit.md:65` | §3 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/conversation-e2e-harness-v1.md:122` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/conversation-e2e-harness-v1.md:132` | §11 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/conversation-e2e-harness-v1.md:140` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/conversation-envelope-v1.md:276` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/glossary.md:133` | §3.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/glossary.md:149` | §3.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/meta/routing-thesis-assessment.md:108` | §18 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:5` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:5` | §14 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:55` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:80` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:137` | §32 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:159` | §21 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/paseo-adapter-v1.md:159` | §22 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/phase0-substrate-hardening.md:3` | §6 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/phase0-substrate-hardening.md:74` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `docs/releases/HARDENING-AUDIT.md:325` | §24 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/releases/NEXT.md:74` | §24 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/releases/PRODUCTION-READINESS-ROADMAP.md:152` | §28 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:94` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:262` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:280` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:363` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:549` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:1406` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:1406` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/superpowers/plans/2026-09-25-mission-spine-phase1.md:1664` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/task-contract-v1.md:211` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `docs/task-contract-v1.md:286` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `experiments/gov_corpus.py:2` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/gov_corpus.py:2` | §18 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_audit_tampering.py:2` | §13 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_audit_tampering.py:12` | §21 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_baseline_comparison.py:2` | §18 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_baseline_comparison.py:2` | §19 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_cascading_failure.py:2` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_cascading_failure.py:13` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_cascading_failure.py:219` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_fail_closed.py:2` | §5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_fail_closed.py:2` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_fail_closed.py:2` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_fail_closed.py:168` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_governance_effectiveness.py:2` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_governance_effectiveness.py:2` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_performance.py:2` | §11 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_performance.py:2` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_sovereignty.py:2` | §15 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_sovereignty.py:2` | §17 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_sovereignty.py:16` | §16 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/harness_sovereignty.py:86` | §16 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/reports/MSB-GOV-EVAL-001.md:74` | §13 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/reports/MSB-GOV-EVAL-001.md:74` | §25 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/reports/MSB-GOV-EVAL-001.md:170` | §16 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/reports/MSB-GOV-EVAL-001.md:191` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `experiments/reports/MSB-GOV-EVAL-001.md:280` | §26 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/R0-ADDENDUM.md:19` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/R0-ADDENDUM.md:19` | §24 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/R0-ADDENDUM.md:25` | §14.1 | `spec` | ambiguous-label | `?ambiguous` |
| `research/baseline/R0-ADDENDUM.md:33` | §5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/original/KNOWN_LIMITATIONS.md:3` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/original/KNOWN_LIMITATIONS.md:22` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `research/baseline/original/TEST_RESULTS.md:9` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `scripts/execute_task_contract.py:112` | §9 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:7` | §11 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:134` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:152` | §11 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:204` | §3.6 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:277` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `scripts/probe_conversation_e2e.py:334` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/handle.py:155` | §25 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/paseo/adapter.py:99` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/safety.py:36` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/trace.py:1` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/verify.py:21` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/agent/verify.py:37` | §3.4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/conversation.py:53` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/conversation.py:123` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/conversation.py:137` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/conversation.py:137` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/conversation.py:137` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/mcp_bridge.py:364` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/mcp_bridge.py:364` | §23 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/moie.py:1` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/moie.py:1` | §23 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/workflow.py:7` | §9 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/workflow.py:105` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/api/workflow.py:117` | §9 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:35` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:109` | §9.2 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:132` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:161` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:240` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:273` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:273` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:465` | §9 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:492` | §10 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/executor.py:520` | §10 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/producer.py:147` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_contract.py:20` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_contract.py:74` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_contract.py:180` | §2 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_contract.py:324` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_contract.py:405` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_producer.py:18` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_producer.py:71` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_producer.py:310` | §8 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/conversation/task_producer.py:336` | §10 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/__init__.py:1` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/context.py:1` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/context.py:1` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/retrieval_router.py:1` | §3 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/retrieval_router.py:1` | §6 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/fabric/retrieval_router.py:1` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/flywheel/engine.py:3` | §0.5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/flywheel/models.py:1` | §0.5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/flywheel/models.py:35` | §0.6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/approval.py:27` | §0.6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/cli.py:134` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:8` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:14` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:72` | §5 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:542` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:592` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/identity_shadow.py:858` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/governance/tool_registry_view.py:7` | §4 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/memory_fabric/fabric.py:12` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/memory_fabric/fabric.py:316` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/memory_fabric/models.py:8` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/memory_fabric/models.py:82` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/adaptive/optimizer.py:3` | §11 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/benchmark/__init__.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/benchmark/__init__.py:3` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:37` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:54` | §5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:75` | §15 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:111` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:147` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:151` | §15 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:217` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:237` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/contracts.py:243` | §13 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/__init__.py:3` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/__init__.py:3` | §13 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/classifier.py:3` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/classifier.py:33` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/escalation.py:3` | §17 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/repair.py:3` | §13 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/failure/repair.py:3` | §15 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/outcome/ledger.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/probability/__init__.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/probability/__init__.py:3` | §11 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/probability/historical_performance.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/probability/routing_matrix.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/capability_matcher.py:3` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/capability_matcher.py:60` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/router.py:3` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/router.py:3` | §10 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_bridge.py:3` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_bridge.py:3` | §11 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_bridge.py:3` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_registry.py:3` | §5 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_registry.py:3` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/skill_registry.py:3` | §11 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/worker_registry.py:3` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/worker_registry.py:3` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/routing/worker_registry.py:3` | §17 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/scheduler.py:5` | §6 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/__init__.py:3` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/__init__.py:10` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/context_compiler.py:3` | §7 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/context_compiler.py:3` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/model_task.py:9` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/model_task.py:83` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/meta/translation/task_translator.py:3` | §8 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/mission/states.py:43` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/mission/states.py:61` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/merger.py:1` | §24 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/merger.py:1` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/meta_critic.py:1` | §24 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/meta_critic.py:1` | §25 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/meta_critic.py:1` | §23 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/models.py:98` | §23 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/pipeline.py:1` | §2 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/pipeline.py:1` | §19 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/router.py:1` | §24 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/moie/router.py:1` | §31 | `spec` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/retrieval/planner.py:8` | §2.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/retrieval/planner.py:36` | §2.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `src/msb_v3/wrongness/README.md:54` | §10 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/agent/test_phase1_acceptance_live.py:1` | §6 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/agent/test_phase1_acceptance_live.py:60` | §3.4 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/agent/test_verify.py:94` | §3.4 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/api/test_system_health.py:202` | §3.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/api/test_system_health.py:228` | §3.2 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/codegraph/test_g1_gate.py:1` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/governance/test_identity_shadow_status.py:11` | §14 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/harnesses/test_chat_actor.py:9` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/memory_fabric/test_fabric.py:2` | §17 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/meta/test_contracts.py:5` | §12 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/mission/test_mission_models.py:1` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/mission/test_mission_property.py:5` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/mission/test_mission_property.py:5` | §9 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/mission/test_mission_states.py:1` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/mission/test_mission_store.py:2` | §4 | `blueprint` | ambiguous-label | `?ambiguous` |
| `tests/test_execute_cli.py:51` | §9 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/test_probe_self_test.py:1` | §7 | `spec` | ambiguous-label | `?ambiguous` |
| `tests/wrongness/test_heldout_fleet.py:6` | §10 | `spec` | ambiguous-label | `?ambiguous` |

### Resolves only outside the repository — 214 site(s)

These resolve — to a document not in this repository. They are grouped by where the citation points, not by how it was resolved, so an inline `spec §4.2.3` and a bare `§53` that only the Steward blueprint owns both land here.

| Where | § | Label | Resolved by | Resolves to |
| --- | --- | --- | --- | --- |
| `docs/SURFACE.md:29` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `docs/SURFACE.md:44` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `docs/SURFACE.md:74` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `docs/SURFACE.md:95` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `docs/SURFACE.md:97` | §4 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `docs/SURFACE.md:116` | §53 | `(bare)` | same-line | `steward-blueprint` |
| `docs/SURFACE.md:116` | §54 | `(bare)` | same-line | `steward-blueprint` |
| `docs/audits/forensic-build-audit-2026-08-15.md:406` | §31 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:435` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:441` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:442` | §28 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:456` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:587` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/audits/forensic-build-audit-2026-08-15.md:658` | §4.2.2 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `docs/audits/forensic-build-audit-2026-08-15.md:682` | §4.2.3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `docs/audits/forensic-build-audit-2026-08-15.md:845` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/blueprints/governance-hardening.md:320` | §53 | `(bare)` | same-line | `steward-blueprint` |
| `docs/blueprints/governance-hardening.md:320` | §54 | `(bare)` | same-line | `steward-blueprint` |
| `docs/blueprints/governance-hardening.md:322` | §5 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/blueprints/governance-hardening.md:322` | §6 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/blueprints/governance-hardening.md:325` | §7 | `meta-system blueprint` | anchored | `meta-system-blueprint` |
| `docs/blueprints/governance-hardening.md:325` | §31 | `meta-system blueprint` | anchored | `meta-system-blueprint` |
| `docs/blueprints/governance-hardening.md:335` | §53 | `steward blueprint` | anchored | `steward-blueprint` |
| `docs/blueprints/governance-hardening.md:336` | §54 | `steward blueprint` | anchored | `steward-blueprint` |
| `docs/blueprints/governance-hardening.md:352` | §53 | `steward blueprint` | anchored | `steward-blueprint` |
| `docs/glossary.md:140` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/provider-plugins.md:20` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `docs/provider-plugins.md:21` | §31 | `unified-architecture` | anchored | `unified-architecture` |
| `experiments/reports/MSB-GOV-EVAL-001-SUMMARY.md:99` | §26 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `experiments/reports/MSB-GOV-EVAL-001.md:292` | §23 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:8` | §3 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:21` | §2 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:21` | §25 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:31` | §24 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:31` | §27 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:40` | §24 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `research/PLAN.md:45` | §2 | `(bare)` | file-default | `doctoral-research-blueprint` |
| `research/PLAN.md:45` | §2 | `research blueprint` | anchored | `doctoral-research-blueprint` |
| `scripts/scan-secrets.py:2` | §5 | `aib-001` | anchored | `wrongness-blueprint-aib-001` |
| `src/msb_v3/agent/handle.py:1` | §20 | `north star, blueprint` | anchored | `north-star-blueprint` |
| `src/msb_v3/agent/handle.py:117` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/handle.py:379` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:386` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:479` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:515` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/handle.py:535` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:624` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:657` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/agent/handle.py:723` | §28 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/handle.py:807` | §17 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/handle.py:986` | §17 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/identity.py:1` | §31 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/identity.py:7` | §17 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/agent/identity.py:10` | §21 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/agent/identity.py:87` | §21 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/agent/paseo/__init__.py:1` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/providers.py:1` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/providers.py:1` | §31 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/safety.py:192` | §13 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/agent/verify.py:15` | §3.4 | `sovereign-agentic-runtime-build-spec` | anchored | `sovereign-agentic-runtime-build-spec` |
| `src/msb_v3/agent/verify.py:119` | §3.4 | `build-spec` | anchored | `sovereign-agentic-runtime-build-spec` |
| `src/msb_v3/api/agent.py:141` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/agent.py:206` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/agent.py:411` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/agent.py:420` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/codegraph.py:1` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/context.py:1` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/factory.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/governance.py:99` | §13 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/mcp_bridge.py:55` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `src/msb_v3/api/mcp_bridge.py:69` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `src/msb_v3/api/mcp_bridge.py:201` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/mcp_bridge.py:339` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/mcp_bridge.py:387` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/mcp_bridge.py:707` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/mcp_bridge.py:743` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/memory_fabric.py:1` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/api/system.py:132` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/api/system.py:201` | §14 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/codegraph/__init__.py:1` | §4.2.1 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/codegraph/queries.py:1` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/codegraph/schema.py:3` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/core/config.py:137` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/core/config.py:143` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/core/config.py:155` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/core/config.py:159` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/fabric/context_engine.py:1` | §4.2.3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/__init__.py:1` | §4.2.6 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/__init__.py:1` | §8 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/__init__.py:1` | §31 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/__init__.py:9` | §9 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/factory/builders.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/classifier.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/models.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/models.py:1` | §8 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/pipeline.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/pipeline.py:1` | §8 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/pipeline.py:1` | §31 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/planner.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/planner.py:1` | §8 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/reviewer.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/reviewer.py:1` | §9 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/factory/test_runner.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/verifier.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/factory/verifier.py:1` | §9 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/governance/identity_shadow.py:1` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `src/msb_v3/governance/killswitch.py:37` | §13 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/governance/killswitch.py:135` | §13 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/guardian/config.py:4` | §10 | `doc 1` | anchored | `guardian-doc-1` |
| `src/msb_v3/guardian/forensics.py:1` | §5 | `doc 4` | anchored | `guardian-doc-4` |
| `src/msb_v3/guardian/forensics.py:354` | §4 | `doc 1` | anchored | `guardian-doc-1` |
| `src/msb_v3/guardian/kpi.py:1` | §8 | `doc 4` | anchored | `guardian-doc-4` |
| `src/msb_v3/guardian/ledger.py:1` | §7 | `doc 4` | anchored | `guardian-doc-4` |
| `src/msb_v3/guardian/reasoning.py:1` | §6 | `doc 4` | anchored | `guardian-doc-4` |
| `src/msb_v3/guardian/run.py:1` | §7 | `doc 1` | anchored | `guardian-doc-1` |
| `src/msb_v3/guardian/run.py:1` | §4 | `doc 4` | anchored | `guardian-doc-4` |
| `src/msb_v3/guardian/run.py:96` | §12 | `doc 1` | anchored | `guardian-doc-1` |
| `src/msb_v3/harnesses/base.py:153` | §5 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/harnesses/base.py:165` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `src/msb_v3/memory_fabric/__init__.py:1` | §4.2.2 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/memory_fabric/__init__.py:7` | §17 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/memory_fabric/fabric.py:1` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/memory_fabric/models.py:3` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/meta/contracts.py:6` | §5 | `meta-system blueprint` | anchored | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:6` | §6 | `meta-system blueprint` | anchored | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:7` | §7 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:7` | §13 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:7` | §14 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:8` | §15 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:114` | §14 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:126` | §9 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:221` | §17 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/meta/contracts.py:247` | §16 | `(bare)` | file-default | `meta-system-blueprint` |
| `src/msb_v3/moie/__init__.py:1` | §3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/__init__.py:2` | §23 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/moie/__init__.py:2` | §31 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/moie/__init__.py:13` | §23 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/moie/engine.py:1` | §3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/engine.py:1` | §24 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/engine.py:1` | §25 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/engine.py:1` | §31 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `src/msb_v3/moie/experts.py:1` | §3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/experts.py:1` | §31 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/experts.py:69` | §25 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/moie/models.py:1` | §3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/models.py:1` | §23 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/moie/models.py:127` | §25 | `(bare)` | file-default | `sovereign-architecture-v4` |
| `src/msb_v3/replay/engine.py:58` | §28 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/steward/__init__.py:6` | §7 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__init__.py:8` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__init__.py:9` | §54 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__main__.py:8` | §7 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__main__.py:8` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__main__.py:8` | §54 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__main__.py:12` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/__main__.py:62` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:3` | §7 | `ail–moie steward blueprint` | anchored | `steward-blueprint` |
| `src/msb_v3/steward/state.py:4` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:4` | §54 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:27` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:28` | §54 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:93` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/steward/state.py:175` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `src/msb_v3/tasks/__init__.py:1` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tasks/__init__.py:3` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/__init__.py:8` | §28 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/events.py:1` | §28 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tasks/events.py:11` | §28 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/events.py:68` | §28 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:1` | §28 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:13` | §28 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:191` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:222` | §28 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:231` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/lifecycle.py:245` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/models.py:1` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tasks/models.py:40` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/models.py:100` | §27 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/models.py:112` | §32 | `(bare)` | file-default | `unified-architecture` |
| `src/msb_v3/tasks/observations.py:4` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/__init__.py:3` | §5 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/__init__.py:3` | §6 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/executors.py:355` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/executors.py:473` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:1` | §6 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/registry.py:18` | §6 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/registry.py:196` | §4.2.1 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:274` | §4.2.2 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:325` | §4.2.3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:351` | §3 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:351` | §23 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/registry.py:380` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `src/msb_v3/tools/runtime.py:1` | §5 | `unified-architecture` | anchored | `unified-architecture` |
| `src/msb_v3/tools/runtime.py:128` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `src/msb_v3/tools/runtime.py:156` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `tests/agent/paseo/test_paseo_adapter.py:250` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `tests/agent/test_identity.py:1` | §17 | `unified-architecture` | anchored | `unified-architecture` |
| `tests/agent/test_inversion_gate.py:2` | §25 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `tests/agent/test_providers.py:1` | §7 | `unified-architecture` | anchored | `unified-architecture` |
| `tests/factory/test_factory_pipeline.py:1` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `tests/factory/test_factory_pipeline.py:1` | §8 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `tests/factory/test_factory_pipeline.py:186` | §4.2.6 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `tests/factory/test_factory_pipeline.py:186` | §9 | `sovereign-architecture` | anchored | `sovereign-architecture-v4` |
| `tests/governance/test_identity_shadow.py:1` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `tests/governance/test_killswitch_scoped.py:1` | §13 | `unified-architecture` | anchored | `unified-architecture` |
| `tests/harnesses/test_chat_actor.py:1` | §10 | `deliverable 02` | anchored | `deliverable-02` |
| `tests/moie/test_moie_engine.py:1` | §3 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `tests/moie/test_moie_engine.py:1` | §23 | `sovereign architecture v4.0` | anchored | `sovereign-architecture-v4` |
| `tests/moie/test_moie_engine.py:1` | §31 | `(bare)` | same-line | `sovereign-architecture-v4` |
| `tests/steward/test_project_state.py:5` | §53 | `(bare)` | file-default | `steward-blueprint` |
| `tests/tasks/test_lifecycle.py:1` | §27 | `unified-architecture` | anchored | `unified-architecture` |
| `tests/tasks/test_lifecycle.py:276` | §28 | `(bare)` | file-default | `unified-architecture` |
| `tests/tasks/test_lifecycle.py:287` | §27 | `(bare)` | file-default | `unified-architecture` |
| `tests/test_secret_scan.py:1` | §5 | `aib-001` | anchored | `wrongness-blueprint-aib-001` |
