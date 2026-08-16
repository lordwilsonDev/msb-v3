"""Signed-device CLI client for the msb-v3 Vesta / Sovereign Node perimeter.

A real device client that speaks the production node.v1 protocol: enrolls a
P-256 identity with the pairing code, opens a signed session, and drives the
signed chat / FILE_READ / FILE_WRITE / SHELL_EXEC flows end-to-end. Identity
and session state persist under ``~/.msb-v3`` (0600) so a terminal session
survives restarts — unlike the ephemeral ``LoopbackDevice`` harness.

Flow ownership matches the server's two-actor model:
  * ``chat`` / ``read`` — the DEVICE signs the request directly.
  * ``write`` / ``shell`` — the OPERATOR submits the exact contract
    (bearer MSB_OPERATOR_TOKEN) and the DEVICE cryptographically ACKs it
    via signed-approve; the server only executes the exact stored contract.

Usage (with ``.env`` loaded):
  python scripts/device-client.py enroll
  python scripts/device-client.py chat "hello"
  python scripts/device-client.py read runtime/node-sandbox/notes.md
  python scripts/device-client.py write runtime/node-sandbox/notes.md "hi"
  python scripts/device-client.py shell echo hello world
  python scripts/device-client.py status
  python scripts/device-client.py approvals          # pending write+shell (operator)
  python scripts/device-client.py approve ack_...    # operator-approve one
  python scripts/device-client.py reject ack_... no  # or reject it
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
from uuid import uuid4

import httpx

from msb_v3.node.crypto import generate_keypair, sign
from msb_v3.node.protocol import (
    b64encode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)

DEFAULT_BASE_URL = os.getenv("VESTA_LOOPBACK_URL", "http://127.0.0.1:8766")
DEFAULT_STATE_DIR = os.getenv("MSB_DEVICE_STATE_DIR", str(Path.home() / ".msb-v3"))
DEFAULT_TIMEOUT_S = 60.0
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_CONFIG = 2


class DeviceClientError(Exception):
    """Raised for server-side failures surfaced to the CLI."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approval_route(approval_id: str, action: str) -> str:
    """Route an approval decision to the write or shell surface.

    Write approvals are created as ``ack_*`` and shell approvals as
    ``shell_ack_*`` (vesta/approvals.py, vesta/shell.py), so the prefix is
    authoritative; anything else falls back to the write route, which 404s
    on unknown ids."""
    if approval_id.startswith("shell_ack_"):
        return f"/vesta/shell/approvals/{approval_id}/{action}"
    return f"/vesta/approvals/{approval_id}/{action}"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text())


def _write_json_0600(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.urandom(4).hex()}.tmp")
    try:
        tmp.write_text(json.dumps(value, indent=2, sort_keys=True))
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _expired(iso: str) -> bool:
    try:
        expires = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return True  # unparseable expiry — treat as expired, re-auth
    return datetime.now(timezone.utc) >= expires


class DeviceClient:
    """Persistent signed-device client speaking the node.v1 protocol."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        state_dir: str | Path = DEFAULT_STATE_DIR,
        pairing_code: str | None = None,
        operator_token: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.state_dir = Path(state_dir)
        self.pairing_code = pairing_code
        self.operator_token = operator_token
        self.device_file = self.state_dir / "device.json"
        self.session_file = self.state_dir / "session.json"
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout_s)

    # ── Identity persistence ────────────────────────────────────────────────
    @property
    def device_id(self) -> str:
        identity = self._load_identity()
        return identity["device_id"]

    def _load_identity(self) -> Dict[str, Any]:
        if not self.device_file.exists():
            raise DeviceClientError(
                f"no device identity at {self.device_file} — run 'device-client.py enroll' first"
            )
        return _read_json(self.device_file)

    def _save_session(self, session_id: str, expires_at: str) -> None:
        _write_json_0600(
            self.session_file,
            {"session_id": session_id, "opened_at": _now_iso(), "expires_at": expires_at},
        )

    def _load_session(self) -> str | None:
        if not self.session_file.exists():
            return None
        session = _read_json(self.session_file)
        if _expired(session.get("expires_at", "")):
            return None
        return session.get("session_id")

    # ── Enrollment ──────────────────────────────────────────────────────────
    def enroll(self, *, force: bool = False, device_id: str | None = None, hardware_assurance: str = "software") -> Dict[str, Any]:
        if not self.pairing_code:
            raise DeviceClientError(
                "MSB_NODE_PAIRING_CODE is empty; enrollment is closed. Set it in .env "
                "(rotate it after enrolling your real device)."
            )
        if self.device_file.exists() and not force:
            identity = _read_json(self.device_file)
            return {
                "device_id": identity["device_id"],
                "status": "already enrolled (use --force to re-enroll)",
            }
        private_key, public_key = generate_keypair()
        identity = {
            "device_id": device_id or f"device-{uuid4().hex[:8]}",
            "private_key": private_key,
            "public_key": b64encode(public_key),
            "hardware_assurance": hardware_assurance,
            "enrolled_at": _now_iso(),
        }
        response = self._client.post(
            "/node/v1/auth/enroll",
            json={
                "device_id": identity["device_id"],
                "public_key": identity["public_key"],
                "pairing_code": self.pairing_code,
                "hardware_assurance": hardware_assurance,
            },
        )
        if response.status_code >= 400:
            raise DeviceClientError(f"enroll failed: HTTP {response.status_code} {response.json().get('detail', '')}")
        _write_json_0600(self.device_file, identity)
        return {"device_id": identity["device_id"], **response.json()}

    # ── Signed session ──────────────────────────────────────────────────────
    def ensure_session(self, *, force: bool = False) -> str:
        """Reuse a valid stored session, or open a fresh signed one."""
        if not force:
            existing = self._load_session()
            if existing:
                return existing
        identity = self._load_identity()
        challenge_response = self._client.post(
            "/node/v1/auth/challenge",
            json={"device_id": identity["device_id"]},
        )
        if challenge_response.status_code >= 400:
            raise DeviceClientError(
                f"challenge failed: HTTP {challenge_response.status_code} "
                f"{challenge_response.json().get('detail', '')}"
            )
        challenge = challenge_response.json()["challenge"]
        signature = b64encode(
            sign(
                identity["private_key"],
                canonical_json(session_signature_payload(identity["device_id"], challenge)),
            )
        )
        session_response = self._client.post(
            "/node/v1/auth/session",
            json={"device_id": identity["device_id"], "challenge": challenge, "signature": signature},
        )
        if session_response.status_code >= 400:
            raise DeviceClientError(
                f"session failed: HTTP {session_response.status_code} "
                f"{session_response.json().get('detail', '')}"
            )
        result = session_response.json()
        self._save_session(result["session_id"], result["expires_at"])
        return result["session_id"]

    def signed_envelope(self, intent: Dict[str, Any], prefix: str = "client") -> Dict[str, Any]:
        """Build and sign a node.v1 request envelope with the device key."""
        identity = self._load_identity()
        session_id = self.ensure_session()
        payload = request_signature_payload(
            f"{prefix}-{uuid4().hex}",
            session_id,
            _now_iso(),
            f"{prefix}-nonce-{uuid4().hex}",
            intent,
        )
        return {**payload, "signature": b64encode(sign(identity["private_key"], canonical_json(payload)))}

    def _post(self, path: str, json_body: Dict[str, Any], *, operator: bool = False) -> Dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.operator_token}"} if operator else {}
        response = self._client.post(path, json=json_body, headers=headers)
        if response.status_code >= 400:
            raise DeviceClientError(f"POST {path} failed: HTTP {response.status_code} {response.json().get('detail', '')}")
        return response.json()

    # ── Operations ──────────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        health = self._client.get("/health").json()
        node = self._client.get("/node/v1/status").json()
        ledger = self._client.get("/vesta/ledger/verify").json()
        return {"health": health, "node": node, "ledger": ledger}

    def chat(self, query: str) -> Dict[str, Any]:
        intent = {
            "type": "chat",
            "objective": query,
            "target": {"query": query},
            # Server-owned capabilities are authoritative; the client may
            # request, never assert.
            "requested_capabilities": ["model.inference"],
        }
        return self._post("/vesta/signed-chat", self.signed_envelope(intent, prefix="chat"))

    def read(self, path: str) -> Dict[str, Any]:
        intent = {
            "type": "read_file",
            "objective": f"Read {path}",
            "target": {"path": path},
            "requested_capabilities": ["FILE_READ"],
        }
        return self._post("/node/v1/engage", self.signed_envelope(intent, prefix="read"))

    def write(self, path: str, content: str, *, expected_sha256: str | None = None) -> Dict[str, Any]:
        if not self.operator_token:
            raise DeviceClientError(
                "MSB_OPERATOR_TOKEN is empty; /vesta/execute (operator submit) is closed. Set it in .env."
            )
        session_id = self.ensure_session()
        payload_sha = hashlib.sha256(content.encode()).hexdigest()
        contract = self._post(
            "/vesta/execute",
            {
                "session": session_id,
                "path": path,
                "content": content,
                "expected_sha256": expected_sha256,
            },
            operator=True,
        )
        ack = self.signed_envelope(
            {
                "type": "file_write_approval",
                "objective": "owner approves exact write",
                "target": {
                    "approval_id": contract["approval_id"],
                    "target_path": contract["target_path"],
                    "payload_sha256": payload_sha,
                    "expected_sha256": expected_sha256 or "",
                    "policy_version": contract["policy_version"],
                },
            },
            prefix="write-ack",
        )
        return self._post(f"/vesta/approvals/{contract['approval_id']}/signed-approve", ack)

    def shell(self, executable: str, args: list[str], *, expected_stdout: str | None = None) -> Dict[str, Any]:
        if not self.operator_token:
            raise DeviceClientError(
                "MSB_OPERATOR_TOKEN is empty; /vesta/shell/execute (operator submit) is closed. Set it in .env."
            )
        session_id = self.ensure_session()
        contract = self._post(
            "/vesta/shell/execute",
            {"session": session_id, "executable": executable, "args": args, "expected_stdout": expected_stdout},
            operator=True,
        )
        ack = self.signed_envelope(
            {
                "type": "shell_approval",
                "objective": "owner approves exact shell command",
                "target": {
                    "approval_id": contract["approval_id"],
                    "command_sha256": contract["command_sha256"],
                    "policy_version": contract["policy_version"],
                },
            },
            prefix="shell-ack",
        )
        return self._post(f"/vesta/shell/approvals/{contract['approval_id']}/signed-approve", ack)

    # ── Operator approval queue ──────────────────────────────────────────────
    def approvals(self, status: str = "PENDING") -> Dict[str, Any]:
        """List durable write + shell approvals (operator view)."""
        if not self.operator_token:
            raise DeviceClientError(
                "MSB_OPERATOR_TOKEN is empty; /vesta/approvals (operator view) is closed. Set it in .env."
            )
        response = self._client.get(
            "/vesta/approvals",
            params={"status": status},
            headers={"Authorization": f"Bearer {self.operator_token}"},
        )
        if response.status_code >= 400:
            raise DeviceClientError(
                f"GET /vesta/approvals failed: HTTP {response.status_code} "
                f"{response.json().get('detail', '')}"
            )
        return response.json()

    def approve(self, approval_id: str) -> Dict[str, Any]:
        """Operator-approve one pending write or shell approval."""
        if not self.operator_token:
            raise DeviceClientError(
                "MSB_OPERATOR_TOKEN is empty; approval decisions are closed. Set it in .env."
            )
        return self._post(_approval_route(approval_id, "approve"), {}, operator=True)

    def reject(self, approval_id: str, reason: str = "owner rejected") -> Dict[str, Any]:
        """Reject one pending write or shell approval."""
        if not self.operator_token:
            raise DeviceClientError(
                "MSB_OPERATOR_TOKEN is empty; approval decisions are closed. Set it in .env."
            )
        return self._post(_approval_route(approval_id, "reject"), {"reason": reason}, operator=True)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DeviceClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ── CLI ─────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=DEFAULT_BASE_URL, help=f"MSB base URL (default {DEFAULT_BASE_URL})")
    common.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help="device/session state dir (default ~/.msb-v3)")
    common.add_argument("--json", action="store_true", help="print the raw JSON result")

    parser = argparse.ArgumentParser(
        prog="device-client",
        description="Signed-device CLI for the msb-v3 Vesta / Sovereign Node perimeter.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("enroll", parents=[common], help="enroll this device with the pairing code")
    p.add_argument("--force", action="store_true", help="re-enroll even if an identity exists")
    p.add_argument("--device-id", default=None, help="explicit device id (default: auto-generated)")

    sub.add_parser("session", parents=[common], help="open a signed session (or reuse a valid one)")

    sub.add_parser("status", parents=[common], help="server health + node + ledger anchored state")

    p = sub.add_parser("chat", parents=[common], help="signed chat")
    p.add_argument("query")

    p = sub.add_parser("read", parents=[common], help="signed FILE_READ")
    p.add_argument("path", help="path relative to the node sandbox root (e.g. notes.md)")

    p = sub.add_parser("write", parents=[common], help="signed FILE_WRITE (operator submit + device ACK)")
    p.add_argument("path", help="path relative to the node sandbox root (e.g. notes.md)")
    p.add_argument("content", help="file content (or --file to read it from disk)")
    p.add_argument("--file", action="store_true", help="treat CONTENT as a path to read content from")
    p.add_argument("--expect-sha256", default=None, help="precondition hash of the existing file (None = must not exist)")

    p = sub.add_parser("shell", parents=[common], help="signed SHELL_EXEC (operator submit + device ACK)")
    p.add_argument("executable")
    p.add_argument("args", nargs="*")
    p.add_argument("--expect-stdout", default=None, help="exact expected stdout (byte-exact postcondition)")

    p = sub.add_parser("approvals", parents=[common], help="list pending write + shell approvals (operator)")
    p.add_argument("--status", default="PENDING", help="filter by status (default PENDING)")

    p = sub.add_parser("approve", parents=[common], help="operator-approve a pending write/shell approval")
    p.add_argument("approval_id", help="approval id (ack_* write or shell_ack_* shell)")

    p = sub.add_parser("reject", parents=[common], help="reject a pending write/shell approval")
    p.add_argument("approval_id", help="approval id (ack_* write or shell_ack_* shell)")
    p.add_argument("reason", nargs="?", default="owner rejected", help="rejection reason")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        with DeviceClient(
            base_url=args.url,
            state_dir=args.state_dir,
            pairing_code=os.getenv("MSB_NODE_PAIRING_CODE"),
            operator_token=os.getenv("MSB_OPERATOR_TOKEN"),
        ) as client:
            if args.cmd == "enroll":
                result = client.enroll(force=args.force, device_id=args.device_id)
            elif args.cmd == "session":
                result = {"session_id": client.ensure_session(force=True)}
            elif args.cmd == "status":
                result = client.status()
            elif args.cmd == "chat":
                result = client.chat(args.query)
            elif args.cmd == "read":
                result = client.read(args.path)
            elif args.cmd == "write":
                content = Path(args.content).read_text() if args.file else args.content
                result = client.write(args.path, content, expected_sha256=args.expect_sha256)
            elif args.cmd == "shell":
                result = client.shell(args.executable, list(args.args), expected_stdout=args.expect_stdout)
            elif args.cmd == "approvals":
                result = client.approvals(status=args.status)
            elif args.cmd == "approve":
                result = client.approve(args.approval_id)
            elif args.cmd == "reject":
                result = client.reject(args.approval_id, args.reason)
            else:  # pragma: no cover — argparse requires a subcommand
                parser.error(f"unknown command: {args.cmd}")
                return EXIT_ERROR
    except DeviceClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG if "is empty" in str(exc) or "no device identity" in str(exc) else EXIT_ERROR
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f"error: request failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _print_human(args.cmd, result)
    return EXIT_OK


def _print_human(cmd: str, result: Dict[str, Any]) -> None:
    if cmd == "enroll":
        print(f"enrolled device_id={result['device_id']} status={result.get('status', result.get('status'))}")
        print(f"identity saved under {DEFAULT_STATE_DIR}/device.json (0600)")
    elif cmd == "session":
        print(f"session_id={result['session_id']}")
    elif cmd == "status":
        health = result["health"]
        node = result["node"]
        ledger = result["ledger"]
        anchored = ledger.get("anchored", {})
        print(f"service={health.get('service')} version={health.get('version')} ok={health.get('ok')}")
        print(f"node={node.get('status', node)}")
        print(
            f"ledger records={ledger.get('record_count')} chain_valid={ledger.get('valid')} "
            f"anchored={anchored.get('valid')} stale={anchored.get('stale')}"
        )
    elif cmd == "chat":
        payload = result.get("payload", {})
        print(f"decision={result.get('decision')} model={payload.get('model')}")
        print(payload.get("text", ""))
    elif cmd == "read":
        if "content" in result:
            print(result["content"])
        else:
            print(json.dumps(result, indent=2, sort_keys=True))
    elif cmd == "write":
        receipt = result.get("receipt", {})
        print(f"status={result.get('status')} path={receipt.get('path')} after_sha256={receipt.get('after_sha256')}")
    elif cmd == "shell":
        execution = result.get("execution", {})
        verification = result.get("verification", {})
        print(f"status={result.get('status')} returncode={execution.get('returncode')} verified={verification.get('ok')}")
        stdout = execution.get("stdout", "")
        if stdout:
            print(stdout.rstrip("\n"))
    elif cmd == "approvals":
        write = result.get("write", [])
        shell = result.get("shell", [])
        if not write and not shell:
            print("no pending approvals")
        for approval in write:
            print(
                f"WRITE {approval['approval_id']}  -> {approval['target_path']}  "
                f"sha256={approval['payload_sha256'][:12]}  {approval['status']}  {approval['created_at']}"
            )
        for approval in shell:
            command = json.loads(approval.get("command_json", "{}"))
            args = " ".join(command.get("args", []))
            print(
                f"SHELL {approval['approval_id']}  -> {command.get('executable', '?')} {args}  "
                f"sha256={approval['command_sha256'][:12]}  {approval['status']}  {approval['created_at']}"
            )
    elif cmd in ("approve", "reject"):
        print(f"status={result.get('status')} approval={result.get('approval_id')}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
