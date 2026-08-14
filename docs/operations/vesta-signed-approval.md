# Vesta Signed Owner Approval

The phone does not receive a shell. It receives a precise approval contract and signs an acknowledgement for that contract.

## Contract

A pending `/vesta/shell/execute` receipt contains:

- `approval_id`
- `command_sha256`
- `policy_version`
- bounded command details
- expiration
- evidence references

The iPhone signs a versioned `node.v1` request whose intent is:

```json
{
  "type": "shell_approval",
  "objective": "Approve the exact shell contract",
  "target": {
    "approval_id": "shell_ack_...",
    "command_sha256": "<exact pending hash>",
    "policy_version": "vesta-policy-1"
  },
  "requested_capabilities": ["human.request_ack"]
}
```

The server verifies the enrolled device session, timestamp, nonce, signature,
and durable replay state before reading the approval. It then compares the
route approval ID, command hash, and policy version with the durable row. The
existing approval service rechecks expiry, exact command integrity, allowlist,
kill state, execution limits, output, and postconditions.

## FILE_WRITE approval

The same signed ACK boundary covers the atomic filesystem writer. Its intent is:

```json
{
  "type": "file_write_approval",
  "target": {
    "approval_id": "ack_...",
    "target_path": "notes/output.txt",
    "payload_sha256": "<exact payload hash>",
    "expected_sha256": "<precondition hash or empty string>",
    "policy_version": "vesta-policy-1"
  }
}
```

Endpoint:

```text
POST /vesta/approvals/{approval_id}/signed-approve
```

Vesta compares every target field against the durable approval before invoking
atomic write, postcondition verification, rollback, and quarantine behavior.

## Endpoint

```text
POST /vesta/shell/approvals/{approval_id}/signed-approve
```

No bearer token is required for the signed route, but private transport admission
still applies when `MSB_VESTA_REQUIRE_TUNNEL=1`. The maintenance-token approval
route remains available for local recovery and is distinct from phone identity.

## Fail-closed cases

- invalid or revoked device/session: `401`
- replayed request ID or nonce: `409`
- wrong approval ID, command hash, or policy version: `409`
- unknown approval: `404`
- expired/already-decided approval: `409`
- kill switch, execution mismatch, timeout, or verification failure: quarantined

A signed request is consumed by the replay store before contract comparison.
Consequently, correcting a malformed ACK requires a new request ID and nonce;
no signed envelope can be reused.
