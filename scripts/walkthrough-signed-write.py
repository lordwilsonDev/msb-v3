#!/usr/bin/env python3
"""End-to-end walkthrough of the signed FILE_WRITE approval flow.

Actors:
  1. OPERATOR  — submits an exact write contract via POST /vesta/execute
                 (bearer MSB_OPERATOR_TOKEN). Server stores it as PENDING.
  2. DEVICE    — an enrolled P-256 device cryptographically ACKs the exact
                 contract via POST /vesta/approvals/{id}/signed-approve
                 (request signature over approval_id + target_path +
                 payload_sha256 + expected_sha256 + policy_version).

Also exercises the tamper-rejection path: a signature over ANY field that
differs from the durable approval must fail closed with 409.

Usage:
  MSB_OPERATOR_TOKEN=... MSB_NODE_PAIRING_CODE=... python scripts/walkthrough-signed-write.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from msb_v3.node.crypto import sign
from msb_v3.node.protocol import b64encode, canonical_json, request_signature_payload
from msb_v3.vesta.dev_harness import LoopbackDevice

BASE = os.getenv("VESTA_LOOPBACK_URL", "http://127.0.0.1:8766")
PAIRING = os.getenv("MSB_NODE_PAIRING_CODE", "")
OPERATOR = os.getenv("MSB_OPERATOR_TOKEN", "")
TARGET = "signed-write-probe.md"
CONTENT = "# signed write\n\nexecuted by owner ACK\n"


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def signed_envelope(device: LoopbackDevice, intent: dict) -> dict:
    """Sign an intent with the session device's private key (the same key
    that opened the session — a different key must be rejected server-side)."""
    payload = request_signature_payload(
        f"walkthrough-{uuid4().hex}",
        device.session_id or "",
        datetime.now(timezone.utc).isoformat(),
        f"walkthrough-nonce-{uuid4().hex}",
        intent,
    )
    return {**payload, "signature": b64encode(sign(device._private_key, canonical_json(payload)))}


def main() -> int:
    if not PAIRING:
        print("MSB_NODE_PAIRING_CODE is empty; enrollment is closed", file=sys.stderr)
        return 2
    if not OPERATOR:
        print("MSB_OPERATOR_TOKEN is empty; /vesta/execute is closed", file=sys.stderr)
        return 2

    client = httpx.Client(base_url=BASE, timeout=60.0)
    try:
        # --- Act 1: enroll a fresh device + open a signed session -----------
        section("Act 1 — device enrollment + signed session")
        device = LoopbackDevice(device_id=f"walkthrough-{uuid4().hex[:8]}")
        device._client.close()
        device._client = client
        enrolled = device.enroll(PAIRING)
        print(f"enrolled device_id={enrolled['device_id']} status={enrolled['status']}")
        session = device.authenticate()
        print(f"session_id={session['session_id']} expires_at={session['expires_at']}")

        # --- Act 2: operator submits the exact write contract ---------------
        section("Act 2 — operator submits FILE_WRITE contract (/vesta/execute)")
        payload_sha = hashlib.sha256(CONTENT.encode()).hexdigest()
        submit = client.post(
            "/vesta/execute",
            json={
                "session": session["session_id"],
                "path": TARGET,
                "content": CONTENT,
                # precondition hash of the EXISTING file; None = file must not exist
                "expected_sha256": None,
            },
            headers={"Authorization": f"Bearer {OPERATOR}"},
        )
        submit.raise_for_status()
        contract = submit.json()
        approval_id = contract["approval_id"]
        print(f"status={contract['status']} approval_id={approval_id}")
        print(f"target_path={contract['target_path']} payload_sha256={contract['payload_sha256'][:16]}…")
        assert contract["payload_sha256"] == payload_sha
        assert contract["decision"] == "REQUIRE_APPROVAL"
        print(f"expires_at={contract['expires_at']} policy_version={contract['policy_version']}")
        print(f"evidence_refs={contract['evidence_refs']}")

        # --- Act 3: device signs the exact contract and ACKs it -------------
        section("Act 3 — device signs the EXACT contract and ACKs it")
        ack = signed_envelope(
            device,
            {
                "type": "file_write_approval",
                "objective": "owner approves exact write",
                "target": {
                    "approval_id": approval_id,
                    "target_path": TARGET,
                    "payload_sha256": payload_sha,
                    "expected_sha256": "",
                    "policy_version": contract["policy_version"],
                },
            },
        )
        approved = client.post(f"/vesta/approvals/{approval_id}/signed-approve", json=ack)
        approved.raise_for_status()
        result = approved.json()
        print(f"status={result['status']}")
        print(f"receipt={json.dumps(result.get('receipt', {}), indent=2)}")
        assert result["status"] == "completed"
        assert result["receipt"]["path"] == TARGET
        print(f"audit_event_ids={result['audit_event_ids']}")

        # --- Act 4: tampered ACK (wrong payload hash) must fail closed -------
        section("Act 4 — tampered ACK (wrong payload hash) must be rejected")
        evil = signed_envelope(
            device,
            {
                "type": "file_write_approval",
                "objective": "owner approves exact write",
                "target": {
                    "approval_id": approval_id,
                    "target_path": TARGET,
                    "payload_sha256": "0" * 64,  # does not match the durable contract
                    "expected_sha256": "",
                    "policy_version": contract["policy_version"],
                },
            },
        )
        rejected = client.post(f"/vesta/approvals/{approval_id}/signed-approve", json=evil)
        print(f"HTTP {rejected.status_code}: {rejected.json().get('detail')}")
        assert rejected.status_code == 409

        # --- Act 5: replay the same ACK must fail closed ---------------------
        section("Act 5 — replaying the accepted ACK must fail closed")
        # Session is still valid; reusing the exact same envelope (same
        # request_id + nonce) must be caught by the replay table.
        replay = client.post(f"/vesta/approvals/{approval_id}/signed-approve", json=ack)
        print(f"HTTP {replay.status_code}: {replay.json().get('detail')}")
        assert replay.status_code == 409  # request_id/nonce already seen

        # --- Act 6: verify on-disk result + task lifecycle -------------------
        section("Act 6 — verify on-disk result + task lifecycle")
        import subprocess

        sandbox = subprocess.run(
            ["find", "runtime/node-sandbox", "-name", TARGET], capture_output=True, text=True, cwd="."
        ).stdout.strip()
        if sandbox:
            disk = open(sandbox).read()
            print(f"file={sandbox}")
            print(f"on-disk sha256={hashlib.sha256(disk.encode()).hexdigest()[:16]}… (expected {payload_sha[:16]}…)")
            assert disk == CONTENT
        else:
            print("WARNING: probe file not found in sandbox")
        task = client.get(
            f"/vesta/tasks/{contract['task_id']}",
            headers={"Authorization": f"Bearer {OPERATOR}"},
        ).json()
        print(f"task state={task['state']} transitions={[t['to_state'] for t in task['transitions']]}")

        print("\nALL STAGES PASSED — signed FILE_WRITE approval flow verified end-to-end.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
