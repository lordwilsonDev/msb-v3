# Global verify-build MCP tool — design

Status: approved (fast-tracked per explicit "end to end, whatever you need")
Date: 2026-08-08

## Origin

SMI-018 (`scripts/verify_claims.py`, shipped 2026-08-07) checks structured
claim blocks in `msb-v3`'s own markdown docs against real files on disk —
scoped to one repo. Wilson asked for the same principle made available
globally: any process/agent, in any project, should be able to say "I
built X, here's proof" and have that checked for real before it's echoed
anywhere — first to a local file, then into the Obsidian vault as a
durable, cross-session record.

## Purpose

A new MCP tool, `verify_build`, reachable the same way every other
msb-v3 tool already is (`mcp_adapter.py` → `mcp_bridge.py`'s
`/mcp/proxy`), callable from any project on this machine, not just
`msb-v3`. It answers: "do these claimed files/tests actually exist?" —
and only if the answer is yes does it echo a confirmation locally and
into the vault.

## Non-goals

- Not a replacement for SMI-018 — that stays as msb-v3's own CI gate over
  its own docs. This is a separate, general-purpose tool for direct,
  intentional "I built this" calls from any caller, not a markdown scanner.
- Not authentication/authorization for the caller's identity — like every
  other tool behind `/mcp/proxy`, it's gated by the existing `x-mcp-secret`
  check (see `mcp_bridge.py`'s `_check_auth`), nothing new here.
- No test-execution ("did it pass"), same restraint as SMI-018 v0.1 —
  file/test existence only.

## Interface

New case in `mcp_proxy()`'s tool `match` statement in
`src/msb_v3/api/mcp_bridge.py`:

```
tool: "verify_build"
args:
  id: str (required)       — identifier for this build claim
  files: list[str]          — absolute paths claimed to exist
  tests: list[str] = []     — absolute paths to test files claimed to exist
```

At least one of `files`/`tests` must be non-empty — an empty claim is
rejected the same way SMI-018 rejects an `implemented` claim with no
evidence target.

Paths are **absolute**, unlike SMI-018's claim blocks. SMI-018 scans
untrusted markdown content and must reject absolute paths as a gate-bypass
vector (a doc author could otherwise point at `/etc/passwd`). This tool is
the opposite trust model: a direct, intentional MCP tool call is the
caller stating a fact about its own project, which could legitimately be
anywhere on disk — there is no untrusted-content-scanning step to defend
against here. `is_file()` is still used, not `exists()`, so a directory or
`.` cannot satisfy a claim (same fix class as SMI-018's final review).

## Behavior

```
verify_build(id, files, tests)
  │
  ├─ validate: id present, at least one of files/tests non-empty
  │    → invalid: return {"status": "FAILED", "error": "..."}, nothing written anywhere
  │
  ├─ check each path: Path(p).is_file()
  │    → any missing/not-a-file: return
  │        {"status": "FAILED", "missing_files": [...], "missing_tests": [...]}
  │        nothing written anywhere — an unverified claim never gets echoed
  │        as if it were true, matching SMI-018's core principle
  │
  └─ all present:
       ├─ write local echo file:
       │    ~/.local/share/msb-v3/verify-build/<id>.txt
       │    contents: VERIFIED / id / files / tests / ISO8601 timestamp
       ├─ append the same content as an entry to the vault note
       │    40_Memory/Verified-Builds-Log.md
       │    (via the same vault-write code path already in this file)
       └─ return {"status": "VERIFIED", "echo_path": ..., "vault_note": "40_Memory/Verified-Builds-Log.md"}
```

Audit logging: reuse the existing `_log_audit`/`_AuditEvent` mechanism
already in `mcp_bridge.py` (added in commit `3e2928a` earlier today) —
`verify_build` calls get an audit entry the same way `vault_write` etc.
already do.

## Local echo file location

`~/.local/share/msb-v3/verify-build/` — follows the existing
`~/.local/` convention already used on this machine for other personal
tools (`~/.local/bin`, `~/.local/lib`, `~/.local/pipx`), namespaced under
`msb-v3` since that's the process actually writing it, `share/` matching
the XDG convention for persistent application-written data (not cache,
not config). Created with `mkdir(parents=True, exist_ok=True)` on first
use — no separate setup step required.

## Vault note format

`40_Memory/Verified-Builds-Log.md` — a single running log, append-only,
one entry per verified build:

```markdown
## 2026-08-08T14:32:00Z — smi018-evidence-verifier
Files: scripts/verify_claims.py
Tests: tests/test_verify_claims.py
```

If the note doesn't exist yet, it's created with a short header on first
write (handled by the existing `vault_write`/`vault_append` tool-call
pattern already in `mcp_bridge.py` — `vault_append` creates the parent
directory and file if absent, per its existing implementation).

## Error handling

- Missing `id`, or both `files` and `tests` empty → `FAILED`, no writes.
- Any claimed path not a real file → `FAILED`, no writes, exact missing
  paths listed.
- Local echo file write fails (disk full, permissions) → the tool call
  itself raises an `HTTPException` (500), matching how every other
  filesystem-touching tool in `mcp_bridge.py` already behaves on I/O
  failure — no silent partial success.
- Vault write fails (e.g., vault base directory unreachable) → same:
  raises, surfaces to the caller. Do not report `VERIFIED` if the vault
  half of the echo didn't actually happen — partial success would itself
  be a claim not backed by what actually occurred, the exact thing this
  whole tool exists to prevent.

## Testing

`tests/api/test_mcp_security.py` already covers this router with the real
pattern: `TestClient(create_app())`, an autouse fixture monkeypatching
`mcp_bridge._MCP_BRIDGE_SECRET`, and `monkeypatch.setattr(mcp_bridge,
"_VAULT_BASE", ...)` to redirect vault writes into `tmp_path` — not
black-box subprocess (that was SMI-018's pattern, needed there because it
scans untrusted markdown; this is direct in-process API testing, same as
every other test already in this file). New tests for `verify_build` go
in this same file, following its exact conventions. The local echo
directory needs the same treatment: introduce a module-level constant
(e.g. `_VERIFY_BUILD_ECHO_DIR`, mirroring `_VAULT_BASE`) so tests can
monkeypatch it to `tmp_path` instead of writing into the real
`~/.local/share/msb-v3/verify-build/`.

Cases: valid claim with real files/tests → VERIFIED, echo file written
with correct content at the (monkeypatched) echo dir, vault entry
appended at the (monkeypatched) vault base; missing file → FAILED, no
echo file written, no vault write attempted; claim with a directory path
instead of a file → FAILED (same `is_file()` fix as SMI-018); empty `id`
→ FAILED; both `files` and `tests` empty → FAILED; no `x-mcp-secret`
header → 401 (already covered generically by this file's existing
`test_missing_secret_returns_401`, but worth one explicit case naming
`verify_build` so the router-wide test doesn't silently stop covering a
tool added later).

## Acceptance criteria

- [ ] A real file/test claim returns VERIFIED, writes the local echo
      file, and appends to the vault note — verified against the real
      running server and the real vault, not mocked.
- [ ] A false claim (missing file) returns FAILED and writes nothing —
      neither the local echo file nor the vault note.
- [ ] Directory/`.` paths don't satisfy a claim.
- [ ] Auth-gated like every other tool on this router.
- [ ] Reuses the existing audit-logging mechanism, doesn't invent a new one.
