#!/usr/bin/env python3
"""End-to-end walkthrough of the signed SHELL_EXEC approval flow.

Actors:
  1. OPERATOR  — submits an exact shell contract via POST /vesta/shell/execute
                 (bearer MSB_OPERATOR_TOKEN). Server stores it as PENDING
                 with a command_sha256 over {executable, args, expected_stdout}.
  2. DEVICE    — an enrolled P-256 device cryptographically ACKs the exact
                 contract via POST /vesta/shell/approvals/{id}/signed-approve
                 (request signature over approval_id + command_sha256 +
                 policy_version).

Only the tiny allowlist (echo/pwd, no flags, bounded args) can ever reach the
executor, and the approval only executes the EXACT stored command. This
walkthrough also exercises the tamper-rejection and replay paths.

Usage:
  MSB_OPERATOR_TOKEN=... MSB_NODE_PAIRING_CODE=... python scripts/walkthrough-signed-shell.py
"""

from __future__ import annotations

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


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def signed_envelope(device: LoopbackDevice, intent: dict) -> dict:
    payload = request_signature_payload(
        f"walkthrough-shell-{uuid4().hex}",
        device.session_id or "",
        datetime.now(timezone.utc).isoformat(),
        f"walkthrough-shell-nonce-{uuid4().hex}",
        intent,
    )
    return {**payload, "signature": b64encode(sign(device._private_key, canonical_json(payload)))}


def main() -> int:
    if not PAIRING:
        print("MSB_NODE_PAIRING_CODE is empty; enrollment is closed", file=sys.stderr)
        return 2
    if not OPERATOR:
        print("MSB_OPERATOR_TOKEN is empty; /vesta/shell/execute is closed", file=sys.stderr)
        return 2

    client = httpx.Client(base_url=BASE, timeout=60.0)
    try:
        # --- Act 1: enroll a fresh device + open a signed session -----------
        section("Act 1 — device enrollment + signed session")
        device = LoopbackDevice(device_id=f"walkthrough-shell-{uuid4().hex[:8]}")
        device._client.close()
        device._client = client
        enrolled = device.enroll(PAIRING)
        print(f"enrolled device_id={enrolled['device_id']} status={enrolled['status']}")
        session = device.authenticate()
        print(f"session_id={session['session_id']} expires_at={session['expires_at']}")

        # --- Act 2: operator submits the exact shell contract ---------------
        section("Act 2 — operator submits SHELL_EXEC contract (/vesta/shell/execute)")
        command = {
            "session": session["session_id"],
            "executable": "echo",
            "args": ["signed", "shell", "ok"],
            # /bin/echo emits a trailing newline; the postcondition requires
            # an EXACT stdout match, so the expected value must include it
            # (a mismatch quarantines the task — verified fail-closed).
            "expected_stdout": "signed shell ok\n",
        }
        submit = client.post(
            "/vesta/shell/execute",
            json=command,
            headers={"Authorization": f"Bearer {OPERATOR}"},
        )
        submit.raise_for_status()
        contract = submit.json()
        approval_id = contract["approval_id"]
        print(f"status={contract['status']} approval_id={approval_id}")
        print(f"command_sha256={contract['command_sha256'][:16]}…")
        print(f"command={contract['command']}")
        assert contract["decision"] == "REQUIRE_APPROVAL"
        assert contract["command"]["executable"] == "echo"
        print(f"policy_version={contract['policy_version']}")

        # --- Act 3: device signs the exact contract and ACKs it -------------
        section("Act 3 — device signs the EXACT contract and ACKs it")
        ack = signed_envelope(
            device,
            {
                "type": "shell_approval",
                "objective": "owner approves exact shell command",
                "target": {
                    "approval_id": approval_id,
                    "command_sha256": contract["command_sha256"],
                    "policy_version": contract["policy_version"],
                },
            },
        )
        approved = client.post(f"/vesta/shell/approvals/{approval_id}/signed-approve", json=ack)
        approved.raise_for_status()
        result = approved.json()
        print(f"status={result['status']}")
        print(f"execution={result.get('execution', {})}")
        print(f"verification={result.get('verification', {})}")
        assert result["status"] == "completed"
        assert result["verification"]["ok"] is True
        assert result["execution"]["returncode"] == 0
        assert result["execution"]["stdout"] == "signed shell ok\n"
        print(f"audit_event_ids={result['audit_event_ids']}")

        # --- Act 4: tampered ACK (wrong command hash) must fail closed -------
        section("Act 4 — tampered ACK (wrong command hash) must be rejected")
        evil = signed_envelope(
            device,
            {
                "type": "shell_approval",
                "objective": "owner approves exact shell command",
                "target": {
                    "approval_id": approval_id,
                    "command_sha256": "0" * 64,  # does not match the durable contract
                    "policy_version": contract["policy_version"],
                },
            },
        )
        rejected = client.post(f"/vesta/shell/approvals/{approval_id}/signed-approve", json=evil)
        print(f"HTTP {rejected.status_code}: {rejected.json().get('detail')}")
        assert rejected.status_code == 409

        # --- Act 5: replay the accepted ACK must fail closed -----------------
        section("Act 5 — replaying the accepted ACK must fail closed")
        replay = client.post(f"/vesta/shell/approvals/{approval_id}/signed-approve", json=ack)
        print(f"HTTP {replay.status_code}: {replay.json().get('detail')}")
        assert replay.status_code == 409  # request_id/nonce already seen

        # --- Act 6: verify task lifecycle ------------------------------------
        section("Act 6 — verify task lifecycle")
        task = client.get(
            f"/vesta/tasks/{contract['task_id']}",
            headers={"Authorization": f"Bearer {OPERATOR}"},
        ).json()
        print(f"task state={task['state']} transitions={[t['to_state'] for t in task['transitions']]}")

        # --- Act 7: a non-allowlisted command must never reach execution -----
        section("Act 7 — non-allowlisted command is DENIED at submission")
        denied = client.post(
            "/vesta/shell/execute",
            json={
                "session": session["session_id"],
                "executable": "bash",
                "args": ["-c", "id"],
                "expected_stdout": None,
            },
            headers={"Authorization": f"Bearer {OPERATOR}"},
        )
        print(f"status={denied.status_code}: {denied.json().get('status')} error={denied.json().get('error')}")
        assert denied.json()["status"] == "denied"

        print("\nALL STAGES PASSED — signed SHELL_EXEC approval flow verified end-to-end.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
