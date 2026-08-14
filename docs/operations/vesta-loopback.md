# Vesta Loopback Development Path

**Purpose:** exercise the real signed-device protocol while the iPhone and WireGuard peer are not yet available.

The loopback harness is a development adapter, not a replacement for device enrollment or private transport. It generates an ephemeral P-256 key, enrolls it through the real `/node/v1/auth/*` endpoints, opens a real signed session, and calls `/vesta/signed-chat`. Vesta still owns the capabilities and rejects replayed or tampered requests.

## Run against the local service

Start MSB with a non-empty local pairing code, then run:

```bash
MSB_NODE_PAIRING_CODE="$MSB_NODE_PAIRING_CODE" \
  python scripts/vesta-loopback.py
```

If the pairing code should not be present in the shell environment, pipe it through stdin:

```bash
printf '%s\n' "$MSB_NODE_PAIRING_CODE" | \
  python scripts/vesta-loopback.py --pairing-code-stdin
```

Optional settings:

```bash
python scripts/vesta-loopback.py \
  --url http://127.0.0.1:8766 \
  --query 'Reply with exactly LOOPBACK_OK.' \
  --device-id loopback-dev
```

The default device ID is ephemeral. Reusing `--device-id` requires the same key, so use a fresh ID for a fresh fixture or remove the development enrollment through the normal operator procedure.

## What the probe proves

1. Enrollment accepts the public key only with the pairing code.
2. The challenge is signed with the generated private key.
3. The session is opened through the real durable identity store.
4. The chat request uses the canonical `node.v1` signature payload.
5. The request becomes a Vesta A-BIND with the device as actor.
6. Client-requested escalation is not authority: the fixture includes `filesystem.write`, but Vesta applies its server-owned Phase 0–2 capabilities.
7. The response includes the normal task, evidence, and audit receipt.

## Explicit limits

- The harness reports `software-loopback`; it is not Secure Enclave attestation.
- It runs over loopback and does not prove WireGuard, firewall, or remote-interface admission.
- It does not add a second policy engine or bypass owner approval.
- The generated private key exists only in the process and is discarded on exit.
- Enrollment remains closed when `MSB_NODE_PAIRING_CODE` is empty.

The deterministic protocol test is `tests/vesta/test_dev_harness.py`. The production iPhone path remains `apps/iphone` and uses the same canonical signing contract.
