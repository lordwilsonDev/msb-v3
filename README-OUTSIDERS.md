# MSB v3 — for people who just met it

> **One screen. No subsystems. Read this before anything else.**

## What it is

MSB v3 is a small **governed agent runtime** that runs on your own machine. You
give it a task; it decides whether the task is safe, runs it under a
permission boundary, and then hands you **proof of what happened** — not just
a "done" message.

It is not a chatbot, not a SaaS, and not a dashboard product. It is a loop:

```
request → is this safe? → run it (bounded) → prove what happened → keep the proof
```

## Why provable autonomy matters

Most AI agents today are a black box: you ask, it does *something*, and you
hope. MSB v3 is built on the opposite bet — **if an agent is going to act, it
should be able to show its work, and the system should be able to prove that
the record of its work wasn't rewritten afterward.**

Three properties make that real, and they're the whole point of the project:

1. **Fail-closed.** If the safety layer can't decide, the action is refused —
   never silently allowed. A dangerous request makes **zero** model calls and
   still leaves an evidence receipt saying it was denied.
2. **Tamper-evident history.** Every action is appended to a hash chain. Edit
   any past record and every record after it breaks — and an external signed
   anchor plus an off-box notary make even *replacing the whole history* with
   an older copy detectable.
3. **Receipts, not vibes.** Every run leaves one JSON receipt: the request,
   the safety verdict, what was authorized, what ran, how it was verified,
   and the audit-chain hash. A receipt says honestly whether the result was
   *directly re-run* or *inferred from logs* — it never overclaims.

You can verify the ledger yourself with one command and no trust in the
author:

```bash
python -m msb_ledger.chain_anchor --verify data/uac/audit_chain.db
python -m msb_ledger.notary --verify data/uac/audit_chain.db   # needs the notary remote
```

## The honest trust model (30 seconds)

The guarantee is: **tampering is detectable** — not impossible. The chain
catches edits; the signed anchor catches whole-history replacement; the
notary catches rollback. The one thing that defeats all of it is compromise
of the anchor signing key, which by default lives on the machine (software
key). The code supports moving it into Apple's Secure Enclave or a YubiKey
(`MSB_CHAIN_ANCHOR_BACKEND=secure-enclave|yubikey`); until you do, treat the
default as *detectable unless the key is compromised* — which is exactly what
the documentation says.

## Run it

```bash
make doctor    # check prerequisites (one command, tells you what's missing)
make setup     # install deps + launchd agents + models + health smoke
bash scripts/start.sh   # start the server (launchd keeps it alive)
```

`make setup` installs into the Python it finds at the default path; on a
machine without that exact Python, point it at yours:
`make setup PY=$(which python3)`.

**What `make setup` does** (all idempotent): installs Python deps, installs
three launchd agents (msb-v3 server, qdrant, backup) rendered for *this*
checkout — an existing install is never clobbered — pulls the two models, and
smoke-checks `/health`.

Five-minute demo — no model, no network, no vault (canned tool outputs):

```bash
python scripts/demo_governed_loop.py   # blocks a dangerous action, allows a safe one
```

Then see [docs/canonical-journey.md](docs/canonical-journey.md) — the five
stages of one request and what each leaves behind.

## Honest limitations

- The safety pre-filter is keyword-based, not an LLM judge — it's a first
  gate, and the real boundary is the governed tool registry.
- The default anchor key is on-box (see above).
- The full engineering reference (every subsystem, endpoint, and contract)
  is [README.md](README.md). This file is the door; that file is the house.

---

Built by [Lord Wilson](https://github.com/lordwilsonDev). MIT licensed.
Cite via [`CITATION.cff`](CITATION.cff).
