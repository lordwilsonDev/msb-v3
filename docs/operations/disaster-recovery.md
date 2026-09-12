# Disaster Recovery — MSB v3 Audit Chain & Signing Identity

**Date written:** 2026-09-11
**Status:** Honest as of this date, verified against the live machine, not aspirational. **Read the "What this means right now" section before anything else — it's not good news.**

This answers four questions directly. Nothing here is guessed; every claim was verified against the actual running system tonight (keychain contents, `.env`, `data/uac/`, `~/msb-backups/`, the launchd job configs).

---

## 1. Where are the Ed25519 keys?

**One place: the macOS login keychain on this Mac.** Service `msb-chain-anchor-key`, account `msb-v3` (confirmed present via `security find-generic-password -a msb-v3 -s msb-chain-anchor-key`). `chain_anchor.py`'s resolution order is: `MSB_CHAIN_ANCHOR_KEY` env var → `data/uac/chain_anchor_key` file → keychain (configured service) → keychain (default service name) → **raise** (it never silently generates a fresh key — good, fail-closed design). Right now, only the keychain entry exists; there's no env var and no keyfile fallback on disk.

**There is no off-machine copy of this key anywhere.** Not in the vault, not in the notary log, not in any backup. `store-anchor-key.sh` (the script that moved it into the keychain) only moves it *into* the keychain and deletes the plaintext `.env` copy — it never creates a second, off-box copy.

There's a real path to hardware-backed key custody (Secure Enclave, documented in `docs/operations/secure-enclave-anchor.md`) but it's not enrolled yet — needs an Apple ID signed into Xcode once. That would make the key *unexportable* (stronger security) but doesn't change the recovery story: an unexportable key that only exists on one machine is exactly as vulnerable to that machine dying as an exportable one with no backup.

## 2. What's the restore procedure?

`make backup` (daily, 03:00, confirmed running — 5 most recent backups present in `~/msb-backups/msb-v3/`, most recent today) creates a full snapshot: every SQLite DB (backed up via the SQLite online-backup API, not a raw file copy — safe under concurrent writes), the rest of `data/`, `storage/` (Qdrant), and the chain-anchor notary log if present. Restore is `python -m msb_v3.ops restore <timestamp>` (or `make restore TS=<timestamp>`) — it verifies the backup's checksums first, builds the new copy alongside the old one (non-destructive — the old data isn't deleted until the new copy is confirmed in place), then swaps.

**This procedure restores data. It does not restore signing capability.** The backup captures `data/uac/chain_anchor.json` (the anchor *state* — a public, signed artifact) and `data/uac/audit_chain.db` (the chain itself), but the **private key that signs new anchors lives only in the keychain**, which `make backup` never touches. Restore this backup onto a fresh machine and you get: a complete, verifiable historical chain, and the ability to confirm nothing in it was tampered with — but no ability to sign a *new* anchor under the same identity, because that identity's private key didn't travel with the backup.

## 3. How do you rebuild the ledger from the vault + anchor?

**Today, you can't — not off-machine.** "Rebuild from vault + anchor" presumes an off-machine copy of the vault and the anchor exists to rebuild *from*. Checked every backup/replication mechanism in the repo tonight:

| Mechanism | Scope | Off-machine? |
|---|---|---|
| `make backup` (daily 03:00) | Full `data/` + `storage/` + notary log | **No — local disk only** (`~/msb-backups/msb-v3/`) |
| Chain-anchor notary (`notarize_chain_anchor.sh`, daily 07:10) | Signed chain-tip snapshots | **No — `MSB_NOTARY_REMOTE=none`, explicitly disabled.** The script supports an off-box rclone push; it's just not configured. |
| `replicate-to-secondary.sh` (weekly, Sun 07:05) | Whole repo incl. `data/` | **No — `MSB_REPLICATION_TARGET` is unset.** Designed and fail-loud-if-broken, but never configured. |
| `heartbeat.sh` (daily 12:00, confirmed working again tonight) | `$REPO/audit/` only (evidence reports, not the chain DB, not the anchor, not the key) | **Yes — the only thing actually leaving this machine.** Pushed to `gdrive:msb-v3/heartbeat` via rclone. |

So: if this Mac's disk fails today, the one artifact that survives off-machine is a folder of audit *reports* — not the audit chain database, not the anchor state, not the notary log, and not the signing key. There is currently no "vault + anchor" anywhere else to rebuild from.

## 4. How long until you're back online?

Two very different answers depending on what "back online" means:

- **The application running again, no continuity with history required:** `make setup` (idempotent host rebuild from a fresh clone: deps, launchd agents, Qdrant, models, health smoke) plus re-provisioning models (`make provision-models`, pulls qwen3:8b + nomic-embed-text — the larger of the two downloads). Realistically **30–90 minutes** depending on network speed for the model pull, most of it unattended. A fresh chain-anchor key would be generated on first use — a genuinely new identity, with no cryptographic link to the old chain's history.
- **The same audit chain, provably continuous with everything before the failure:** **Not currently possible**, per the table above. There is no off-machine copy to restore from. The local daily `make backup` snapshots would survive a *logical* failure (corrupted DB, bad migration, accidental deletion) but not a *physical* one (disk death, machine loss, theft) — they live on the same disk as the thing they're backing up.

---

## What this means right now

You are, today, one disk failure away from permanently losing the ability to prove continuity of this audit chain's history — not the history itself if you have some other recovery (Time Machine, etc. — not checked here, out of scope for what MSB v3's own tooling does), but the *signing identity* that makes the chain's integrity provable going forward. The system's own fail-closed design (never silently forge a new key) is exactly correct — it just means recovery requires a deliberate, done-in-advance step that hasn't happened yet.

**Three independent gaps, any one of which would close this if fixed:**
1. Export the keychain seed and store it somewhere off-machine you control (a password manager, a written backup in a safe — anywhere that isn't this disk). This is the single highest-leverage fix and takes minutes. `security find-generic-password -a msb-v3 -s msb-chain-anchor-key -w` prints the raw seed — deliberately not run here, since a private key's plaintext value shouldn't pass through a session transcript if it doesn't have to. Run it yourself, in a terminal you control, and store the output somewhere durable.
2. Set `MSB_NOTARY_REMOTE` to a real rclone remote (the notary script already supports this — it's a config value, not new code) so the daily signed chain-tip snapshots start leaving the machine.
3. Set `MSB_REPLICATION_TARGET` to a real second machine or off-box path so the weekly full-repo replication (already built, already fail-loud, just unconfigured) starts actually running.

None of these are code changes. All three are configuration you can set tonight.
