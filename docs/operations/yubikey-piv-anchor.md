# YubiKey PIV anchor — what to buy and how to enroll

The chain anchor's proof is only as strong as the key that signs it. The
interim setup keeps the Ed25519 seed in the macOS login keychain (encrypted at
rest, `0600` file, keychain-protected) — but an attacker in your unlocked
session could still read it. A YubiKey closes that fully: **the private key is
generated inside the YubiKey's secure element and never leaves it.** Signing
happens on the device, so a box compromise cannot extract the key or forge
fresh anchors.

This document covers (1) what to buy and (2) the one-time enrollment.

---

## 1. What to buy

Any YubiKey with **PIV support** works. PIV is available on most of the line:

| Model | PIV? | Approx. price | Notes |
|---|---|---|---|
| **YubiKey 5 NFC** | ✅ | ~$55 | The standard pick. USB-A, NFC. P-256 in PIV. |
| **YubiKey 5C NFC** | ✅ | ~$60 | USB-C version — matches a modern Mac directly. |
| **YubiKey 5 Nano** | ✅ | ~$55 | Tiny, stays in the port. |
| **YubiKey Security Key C NFC** | ✅ | ~$29 | Cheaper; PIV supported since the 5.7 firmware. |
| **YubiKey 5 FIPS** | ✅ | ~$80 | Overkill for this purpose. |

**Recommendation: a YubiKey 5C NFC (or 5 NFC with a USB-C adapter).** The
USB-C form factor is the least friction on a Mac, and NFC means the same key
can later serve phone/mobile use. The Security Key C NFC at ~$29 is fine if
budget matters — PIV is all we need, and it supports P-256.

> **Avoid:** YubiKey 5Ci (discontinued, Lightning connector), and the classic
> Security Key (non-NFC, older firmware without PIV). Any *current* YubiKey
> with "PIV" in its feature list works.

### What you get

- P-256 (secp256r1) key generated **on-device** — the private key can never be
  exported (the YubiKey has no export path for generated keys).
- Signing via PKCS#11 (`libykcs11`), PIN-gated. The MSB backend uses
  `python-pkcs11` + the `ECDSA_SHA256` mechanism.
- No Apple ID needed — unlike the Secure Enclave path, no provisioning
  profile / Xcode signing is required. The YubiKey is the lower-friction
  hardware option.

---

## 2. Prerequisites (one-time install)

```bash
brew install ykman yubico-piv-tool   # ykman CLI + libykcs11 PKCS#11 module
pip install python-pkcs11            # already pinned in requirements-runtime.lock
```

Then plug in the YubiKey and confirm it's seen:

```bash
ykman list
# e.g. "YubiKey 5C NFC [OTP+FIDO+CCID] Serial: 12345678"
```

---

## 3. Enroll (one command)

```bash
bash scripts/yubikey-enroll.sh            # slot 9a, prompts for a PIN
# or, to script it / use the Signature slot:
bash scripts/yubikey-enroll.sh 9c 123456  # WARNING: use a real PIN, not 123456
```

What it does (all documented, idempotent-ish, safe to re-run):

1. Verifies `ykman` exists and a YubiKey is plugged in.
2. Changes the PIV PIN from the factory default `123456` to yours (skipped
   silently if already changed).
3. **Generates a P-256 key on-device** in the slot — the private key never
   leaves the YubiKey.
4. Writes a self-signed certificate to the slot. PKCS#11 `Sign` refuses to
   sign without a certificate present; the cert is only a carrier for the
   public key.
5. Stores the PIN at `~/.yubikey-pin` (`chmod 600`) for the unattended
   launchd notary/verify jobs.
6. Exports and prints the public key so you can record the fingerprint.

### Slot choice

| Slot | Name | Purpose |
|---|---|---|
| `9a` | PIV Authentication | Default; fine for the anchor. |
| `9c` | Digital Signature | Also fine; slightly more "signature-appropriate". |
| `9d` | Key Management | Avoid unless you know why. |

The anchor only needs one slot. `9a` is the default.

---

## 4. Point MSB at the hardware backend

Add to `.env` (the app reads these at startup):

```bash
MSB_CHAIN_ANCHOR_BACKEND=yubikey
MSB_YUBIKEY_PIV_SLOT=9a
# PIN: read automatically from ~/.yubikey-pin. Overrides:
# MSB_YUBIKEY_PIN=<pin>
# MSB_YUBIKEY_PKCS11_LIB=/opt/homebrew/lib/libykcs11.dylib  (auto-detected otherwise)
```

Verify the backend is seen and can sign:

```bash
python3 - <<'EOF'
from msb_v3.uac.signing import YubiKeyPivBackend, verify_signature, SECP256R1
b = YubiKeyPivBackend()
print("available:", b.available())
print("reason:", b.unavailable_reason())
if b.available():
    msg = b"canonical-check"
    sig = b.sign(msg)
    print("signature ok:", verify_signature(msg, sig, b.public_key_bytes(), SECP256R1))
EOF
```

Expected: `available: True`, `signature ok: True`.

---

## 5. Migrate the anchor to the YubiKey key (rotation ceremony)

Rotation is now a **cross-signed ceremony**, not a bare re-sign: the OLD key
signs a successor endorsement, the `chain_key_registry.json` advances, and the
chain is re-anchored with the new key. The old key's historical notary
entries stay verifiable (a hardware move must not invalidate history), and an
unregistered key can never claim the anchor slot.

1. **Prepare a recovery key FIRST** (one-time, before any migration — if the
   primary key dies before this step, recovery is impossible):
   ```bash
   python3 - <<'EOF'
   from msb_v3.uac.chain_anchor import ChainAnchor, generate_seed
   from msb_v3.uac.signing import SoftwareEd25519Backend
   seed = generate_seed()
   print("RECOVERY SEED (store OFFLINE, e.g. paper/password manager):", seed.hex())
   print("RECOVERY PUBLIC KEY (register with --register-recovery):", SoftwareEd25519Backend(seed).public_key_hex())
   EOF
   ```
   Then register the public half (the seed never touches the box again):
   ```bash
   python3 -m msb_v3.uac.chain_anchor --register-recovery <audit.db> \
     --recovery-public-key <recovery-public-hex> --reason "offline recovery key"
   ```
2. **Stop the app** (so the running process can't re-anchor mid-rotation):
   ```bash
   bash scripts/start.sh stop
   ```
3. **Rotate** — the old key cross-signs the YubiKey key as successor:
   ```bash
   python3 -m msb_v3.uac.chain_anchor --rotate <audit.db> \
     --backend yubikey --reason "migrate to YubiKey PIV"
   ```
   (For a software successor: `--backend software --seed <new-seed-hex>`.)
4. **Restart the app** and confirm it re-anchors with the YubiKey key:
   ```bash
   bash scripts/start.sh start
   bash scripts/verify_chain_anchor.sh
   # expect: "local and remote notary histories agree; remote head verified"
   ```
5. **Remove the old key material**: delete the keychain item holding the old
   Ed25519 seed (`security delete-generic-password -s <service>` — see
   `scripts/store-anchor-key.sh` for the service name) and the
   `MSB_CHAIN_ANCHOR_KEY` / keyfile references from `.env`.
6. **Revoke the old key** so it can never sign a NEW anchor again (its
   historical entries remain valid):
   ```bash
   python3 -m msb_v3.uac.chain_anchor --revoke <audit.db> --reason "retired after YubiKey migration"
   ```

### If the primary key is LOST (enclave died / YubiKey lost)

Recovery requires the pre-registered recovery seed:

```bash
python3 -m msb_v3.uac.chain_anchor --recover <audit.db> \
  --seed <recovery-seed-hex> --reason "primary key lost"
```

Fails closed when no recovery key was registered — the chain stays verifiable
via the off-box notary but cannot be re-anchored.

---

## 6. Unattended operation (launchd)

The notary and anchor-verify launchd jobs run without a human at the console,
so the PIN must be resolvable without a prompt. The enrollment script stores
it at `~/.yubikey-pin` (`0600`); the backend reads it automatically. The
YubiKey must stay plugged in — it is the trust root now, and the verify job
fails closed (`REMOTE_UNREACHABLE`-style) if it can't sign.

To test from launchd's minimal environment:

```bash
launchctl kickstart -k gui/$(id -u)/com.lordwilson.chain-anchor-verify
# then:
launchctl print gui/$(id -u)/com.lordwilson.chain-anchor-verify | grep -E "last exit code|run interval"
```

---

## 7. Failure modes & recovery

| Failure | Behavior | Recovery |
|---|---|---|
| YubiKey unplugged | Backend fails closed; anchor/notary jobs error loudly | Plug it back in; jobs self-heal on next run |
| Wrong PIN (3 attempts) | PIV PIN blocks after 3 wrong tries | Unblock with the PUK: `ykman piv access unblock-pin --puk <PUK> --new-pin <PIN>` |
| PUK lost | PIV app locked | `ykman piv reset` wipes PIV — **re-enroll and re-anchor** (new key era) |
| YubiKey lost/stolen | New key must be enrolled; old chain is preserved as historical evidence | Enroll a new key, re-anchor, start fresh notary era; the old notary log on gdrive proves what the old key signed |
| `libykcs11` missing | Backend reports "libykcs11 not found" | `brew install yubico-piv-tool` |

**Backup the PUK + PIN** (e.g. in a password manager): without them, a locked
PIV app means `ykman piv reset` and a full re-anchor.

---

## 8. Security properties

- **Key non-exportable by design** — the YubiKey has no export path for keys
  it generated; the private key physically cannot leave the secure element.
- **Signing is PIN-gated** — an attacker needs both the box *and* the PIN
  (and the device).
- **No Apple ID / provisioning profile needed** — unlike the Secure Enclave
  path, this works on any OS (macOS *and* Linux), which also makes it the
  right choice if MSB ever runs on a non-macOS sovereign node.
- **Same wire format as the other backends** — P-256 uncompressed public
  point + DER ECDSA-SHA256 signature, so the anchor/notary code is untouched.
- **Fail-closed** — until a key is enrolled, `available()` is `False` and
  `sign()` raises `SigningBackendUnavailable`; anchoring requested but
  unavailable never degrades silently to an unsigned chain.

---

## 9. Relationship to the Secure Enclave path

Both are implemented; they are alternatives, not replacements:

| | Secure Enclave | YubiKey PIV |
|---|---|---|
| Hardware | Mac's enclave | External device |
| Needs Apple ID / Xcode profile | ✅ (blocked for you today) | ❌ |
| Works on Linux | ❌ | ✅ |
| Key removable from box | No (it's on the Mac) | Yes — carry it / store it |
| Cost | $0 (but blocked) | ~$29–$60 |

**Recommendation:** if you buy a YubiKey, it becomes the primary hardware
backend (no Apple ID needed). The Secure Enclave backend stays available if
you later get an Apple ID and want the no-extra-hardware option.
