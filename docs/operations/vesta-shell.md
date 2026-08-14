# Vesta SHELL_EXEC Development Gate

`SHELL_EXEC` is an approval-only capability. It is not a remote shell and it does not accept a command string.

## Current allowlist

The first development slice permits only these named commands:

```text
echo <non-flag arguments>
pwd
```

The server maps names to fixed absolute executable paths. The caller cannot provide an executable path, shell syntax, environment, working directory, pipes, redirects, command substitution, or network permission.

The executor always uses:

- `shell=False`
- the configured Vesta sandbox as cwd
- `stdin=DEVNULL`
- a scrubbed `PATH`/locale environment
- bounded argument count and size
- a hard timeout with process-group termination
- bounded captured output

## Approval flow

```text
POST /vesta/shell/execute
  -> validate named command and arguments
  -> persist exact canonical command hash
  -> REQUIRE_APPROVAL
  -> owner approves exact approval ID
  -> revalidate hash, policy, expiry, kill state, and allowlist
  -> execute
  -> capture output evidence
  -> verify return code, timeout, output bound, expected stdout
  -> COMPLETED or QUARANTINED
```

Approvals cannot be reused. Changing the command record after submission causes quarantine rather than execution.

## Cryptographic phone approval

An enrolled device can approve the exact pending contract without the maintenance bearer token:

```text
POST /vesta/shell/approvals/<approval_id>/signed-approve
```

The signed `node.v1` intent must contain:

```json
{
  "type": "shell_approval",
  "target": {
    "approval_id": "shell_ack_...",
    "command_sha256": "<exact hash from the pending receipt>",
    "policy_version": "vesta-policy-1"
  }
}
```

Vesta verifies the enrolled device session and replay state, then independently
loads the approval row and compares all three fields before calling the existing
approval/execution path. The device cannot change the command, target, policy,
expiry, or capability set. The signed request is consumed even when the target
is wrong, so a failed ACK cannot be corrected by replaying the same envelope.

## Example

```bash
curl -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"executable":"echo","args":["HELLO"]}' \
  http://127.0.0.1:8766/vesta/shell/execute

curl -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -X POST \
  http://127.0.0.1:8766/vesta/shell/approvals/<approval_id>/approve
```

Do not add an executable to the allowlist without a dedicated argument schema, threat review, output contract, timeout test, and recovery test. `sh`, `bash`, `python`, `osascript`, package managers, network clients, and arbitrary user-provided paths remain denied.
