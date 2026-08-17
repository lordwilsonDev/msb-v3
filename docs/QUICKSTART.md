# MSB v3 — Quick Start (for a technically capable outsider)

This is the "demo is reproducible" artifact of the v0.3.0 release: with the
prerequisites below, you can bring MSB up from a clean checkout, run the
canonical governed path end-to-end, and verify the three verdict cases
(SAFE / DENY / BLOCK) with the evidence they produce — without the original
builder's setup or mental model.

MSB v3 is a **narrow, local-first, governed agent runtime**: it takes a real
task from request to a verified, evidence-backed result, and refuses,
records, and recovers when a model, tool, or permission fails. Everything
below is local; nothing leaves your machine.

## 1. Prerequisites

| Thing | Why | Check |
|---|---|---|
| macOS or Linux | runtime (launchd is macOS-only; Linux uses the standby supervisor) | `uname -s` |
| Python 3.11+ (miniforge/venv recommended) | app + tests | `python3 --version` |
| Ollama running on `:11434` with `qwen3:8b` + `nomic-embed-text` | local inference + embeddings (no cloud required) | `ollama list` |
| `MSB_OPERATOR_TOKEN` + `MCP_BRIDGE_SECRET` in `.env` | operator gate + MCP bridge (start refuses to boot without the bridge secret) | see step 2 |
| ~2–4 GB free disk | models + SQLite stores | `df -h` |

Optional but recommended: the [msb-v3 CI gate set](.github/workflows/) runs
the full battery (ruff + bare mypy + tests + hygiene + portability +
claims audit) on every commit — see the release declaration
([docs/releases/MSB-v3-RELEASE.md](releases/MSB-v3-RELEASE.md)) for what
"green" means.

## 2. Bring it up

```bash
git clone <your-origin> msb-v3 && cd msb-v3

# Secrets (required):
cp .env.example .env
#   MSB_OPERATOR_TOKEN=<long random>        # operator gate for /agent/handle
#   MCP_BRIDGE_SECRET=$(openssl rand -hex 32)
#   OLLAMA_MODEL=qwen3:8b                   # already the default

# Install + boot:
make deps            # pip install -e ".[dev]" (or the exact gate deps)
bash scripts/start.sh start
curl -s :8766/health
```

If you don't have Ollama models yet:

```bash
ollama pull qwen3:8b && ollama pull nomic-embed-text
```

## 3. Run the canonical governed path

The canonical path is `/agent/handle`: intent → task DAG → ActionGate →
governed tools → verification → evidence spine → audit chain → replay.
It is operator-gated (fail-closed 503 until the token is set, 401 on
mismatch) because it executes tools against the vault tenant.

```bash
TOKEN="$MSB_OPERATOR_TOKEN"   # from .env
curl -s -X POST :8766/agent/handle \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"request": "Research how the vault is organized and write a short summary note", "approve": true}' | python3 -m json.tool
```

Expect: a run record with a task graph, a deterministic hash, tool events,
and a replay-consistent evidence chain. A read-only request with
`"approve": false` returns PASS with **no file written**.

## 4. Verify the three verdict cases

The golden fixtures ([artifacts/core-loop/README.md](../artifacts/core-loop/README.md))
are the release evidence; re-running them on your checkout proves the
behavior is reproducible, not just claimed:

```bash
bash scripts/capture-verdict-fixtures.sh        # SAFE + tainted-DENY
# kill-BLOCK (arm the switch, attempt a write, disarm):
curl -s -X POST :8766/governance/killswitch/arm -H "Authorization: Bearer $TOKEN"
curl -s -X POST :8766/agent/handle -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"request": "write a note", "approve": true}'
curl -s -X POST :8766/governance/killswitch/disarm -H "Authorization: Bearer $TOKEN"
```

Each case lands in `artifacts/core-loop/` with `response`, `replay.json`
(the event-sourced reconstruction) and `audit.json` (the hash chain). The
tainted and kill cases must show **no file written** — that is the
governance invariant, not a side effect.

## 5. Verify a Merkle receipt (new in this release)

```bash
python3 -m msb_ledger.chain_anchor --receipt data/uac/audit_chain.db --seq 1 > /tmp/receipt.json
python3 -m msb_ledger.chain_anchor --verify-receipt data/uac/audit_chain.db --receipt-file /tmp/receipt.json
# exit 0 = the record is provably in the chain; see docs/operations/merkle-receipts.md
```

## 6. Run the full gate battery

```bash
make test          # full pytest suite (1468+ tests, hermetic)
make lint          # ruff + bare mypy + lock check + CLAIMS AUDIT
make hygiene       # 12/12 offline experiments
make portability   # full suite from a foreign checkout path (the pre-push gate)
```

`make lint` includes `scripts/verify-claims.py` — every capability claim in
the release declaration must link to evidence that exists and matches live
test counts, or the gate fails. That is how "the docs match the runtime"
is enforced mechanically rather than promised.

## What is intentionally NOT here

- No multi-user SaaS, dashboard product, or remote deployment.
- No sandboxed executor (the CLI provider is best-effort isolation —
  see the release declaration's Limitations).
- No multimodal, no distributed mesh (parked in v4 —
  [docs/blueprints/convergence-to-12/v4-parking-lot.md](blueprints/convergence-to-12/v4-parking-lot.md)).

For the full contract, evidence, and honest limitations, read
[docs/releases/MSB-v3-RELEASE.md](releases/MSB-v3-RELEASE.md) first — it
states what v3 is, what it is NOT, and what is frozen.
