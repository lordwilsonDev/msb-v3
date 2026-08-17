# Secure Enclave chain-anchor key — operator guide

**Security-hardening #1:** the chain-anchor signing key moves OFF the box into
Apple's Secure Enclave, so a box compromise can no longer forge a fresh anchor
over a rewritten audit chain. This closes the documented trust boundary of the
external anchor ("detectable unless the signing key is compromised" → the key
cannot be copied because it never leaves the enclave).

Status on the sovereign node (Mac mini M4, macOS 26.2):
**backend implemented + fail-closed; enrollment pending an Apple ID in Xcode**
(see "The macOS entitlement wall" below — this is a real OS constraint, not a
stubbed feature).

## Architecture

```
ChainAnchor (uac/chain_anchor.py)
   └── SigningBackend (uac/signing.py)
         └── SecureEnclaveBackend ──JSON CLI──> secenclave-tool (Swift)
                                                  └── Security.framework
                                                        └── Secure Enclave (P-256)
```

- `scripts/secenclave/secenclave.swift` — the helper: `create` / `public` /
  `sign` / `delete`. The private key is generated inside the enclave and never
  exported; the keychain stores only a reference. Signing uses
  `kSecKeyAlgorithmECDSASignatureMessageX962SHA256` (raw X9.62 r||s output;
  the Python backend converts to DER).
- Access control: `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` +
  `kSecAccessControlPrivateKeyUsage` — usable without a prompt after the
  operator has unlocked the Mac once following boot, so the unattended launchd
  notary/verify jobs can sign.
- Build: `scripts/secenclave/build.sh` → `~/.local/bin/secenclave-tool`
  (resolved via `MSB_SECURE_ENCLAVE_TOOL`, then `~/.local/bin`, then
  `<repo>/scripts/`).
- Wire format is identical to the software P-256 backend (uncompressed point +
  DER ECDSA), so the anchor/notary code never changes; the anchor record's
  `key_algorithm` field makes the two eras coexist.

## The macOS entitlement wall (why enrollment needs Xcode)

Persisting a Secure Enclave key requires the `keychain-access-groups`
entitlement, which macOS validates against an **embedded provisioning
profile**, not a bare signature. Empirically verified on macOS 26.2 (2026-08):

| Attempt | Result |
| --- | --- |
| unsigned binary | `-34018 errSecMissingEntitlement` |
| ad-hoc signature (`codesign -s -`) | `-34018` |
| ad-hoc + entitlement | killed at launch (AMFI: restricted entitlement on ad-hoc) |
| real Apple Development sig + entitlement, bare binary | killed at launch (no profile) |
| **Xcode target w/ automatic signing + Keychain Sharing capability** | **works** (validated pattern; generates + embeds the profile) |

The only free path is an Xcode build with the Keychain Sharing capability,
which requires an Apple ID signed into Xcode once (free Personal Team is
sufficient — validated). Until the tool is profile-signed, `available()` is
False and `sign()` raises with these steps — fail-closed by design.

## Enrollment (one-time, on the Mac)

1. **Sign into Xcode** with an Apple ID: Xcode → Settings → Accounts
   (free account is enough). This provisions an Apple Development identity.
2. **Create a tiny macOS command-line-tool target** (bundle id, e.g.
   `com.blackswanlabz.msb.secenclave`) with automatic signing under your team
   and the **Keychain Sharing** capability, and build it once — Xcode
   generates + embeds a development provisioning profile that authorizes
   `keychain-access-groups` on this device. (A ready-to-use project generator
   is a follow-up; the requirement is the profile, not the project contents.)
3. **Wrap `secenclave-tool` in that profile**: copy the generated
   `embedded.provisionprofile` into a minimal `.app` bundle around the binary
   (or codesign the standalone binary with the entitlement + embedded
   profile), then `codesign --entitlements <KeychainSharing.entitlements> -s
   "<Apple Development: …>" secenclave-tool`.
4. **Enroll the key**:
   ```bash
   ~/.local/bin/secenclave-tool create --label msb-chain-anchor
   ```
   → prints the 65-byte uncompressed public key. `public` / `sign` work from
   any process as the same user.
5. **Point the runtime at it** in `.env`:
   ```bash
   MSB_CHAIN_ANCHOR_BACKEND=secure-enclave
   MSB_SECURE_ENCLAVE_TOOL=/Users/<you>/.local/bin/secenclave-tool
   # fresh notary era for the new key (see Rotation):
   MSB_NOTARY_LOG=/Users/<you>/msb-backups/chain-anchor-notary-se.jsonl
   MSB_NOTARY_REMOTE=gdrive:msb-v3/chain-anchor-notary-se
   # REMOVE MSB_CHAIN_ANCHOR_KEY — the seed leaves the box
   ```
6. **Rotate the anchor + notary** (see below), then `bash scripts/start.sh
   stop && bash scripts/start.sh start` so the app anchors with the enclave
   key, and confirm `scripts/verify_chain_anchor.sh` is healthy.

## Rotation (Ed25519 seed → Secure Enclave key)

The audit chain's record hashes are independent of the anchor key, but the
anchor file and every notary entry are signed by the current key — and
`verify_notary_entry` requires the entry's key to match the current anchor
key. Rotation is therefore an explicit operator action:

1. **Stop the app** first (`start.sh stop`) — the running app re-anchors on
   every append and would silently write the OLD key back over the new anchor.
2. **Re-anchor** with the new key:
   `python -m msb_v3.uac.chain_anchor --anchor <audit.db>` (with the new env).
3. **Start a fresh notary era**: new `MSB_NOTARY_LOG` + `MSB_NOTARY_REMOTE`
   (e.g. `...-se.jsonl` / `gdrive:msb-v3/chain-anchor-notary-se`). The v1 log
   and `gdrive:msb-v3/chain-anchor-notary` are preserved as historical
   evidence; entries there are still independently verifiable by anyone with
   the public key embedded in each entry.
4. `notarize_chain_anchor.sh` once → first SE-era entry on the remote.
5. Restart the app; `verify_chain_anchor.sh` must report the remote head
   verified and the anchor fresh.
6. Archive the legacy seed **off-box** (password manager) if you want to
   re-verify the v1 era later; delete it from `.env` regardless — an on-box
   copy is exactly the weakness being closed.

## Re-provisioning a new machine

New Mac → steps 1–4 of Enrollment with a **new** key → rotation (re-anchor the
same audit DB copy with the new key, fresh notary era). The old machine's key
is hardware-bound and cannot be copied, so migration is enroll-and-rotate, not
key transfer.

## Verification

- `scripts/secenclave/build.sh` — builds + smoke-tests the tool (fail-closed
  probe on a missing key).
- Hermetic: `tests/uac/test_signing.py` runs the full backend glue against a
  fake tool (raw X9.62 contract) and asserts unprovisioned fail-closed.
- Live: `~/.local/bin/secenclave-tool public --label msb-chain-anchor` returns
  the enrolled public key; `verify_chain_anchor.sh` exit 0.
