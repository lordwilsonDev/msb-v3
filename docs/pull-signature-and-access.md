# Pull signatures & access model

Two things are in place to make every interaction with this repo auditable
and to keep `main` locked to fork-based contributions.

## 1. Every pull/checkout leaves a digital signature

The repo ships a git hook pack in `hooks/` (activated by
`core.hooksPath`, no `.git/hooks` fiddling needed):

| Hook | When | What it does |
|---|---|---|
| `post-merge` / `post-checkout` | every `git pull`, clone, branch switch | appends a **cryptographically signed** entry to `~/.msb-v3/pull-signatures.log` |
| `pre-commit` | every commit | **refuses unsigned commits** (signing must be configured) |
| `commit-msg` | every commit | **requires a `Signed-off-by:` trailer** (DCO) |

### The ledger

One line per pull/checkout:

```
2026-08-19T12:34:56Z|alice@mbp|abc1234..def5678|SIG:<base64 ssh signature>
```

The signature is an SSH signature (`ssh-keygen -Y sign`, ed25519 key
`~/.msb-v3/signing_key`, namespace `msb-v3-pull`) over the
`TS|user@host|from..to` portion — so the entry is **verifiable**, not just
asserted.

### Setup (one-time, per machine)

```bash
bash scripts/install-hooks.sh     # or: make install-hooks
```

Generates the signing key if absent, writes `~/.msb-v3/allowed_signers`,
and sets `commit.gpgsign` / `gpg.format ssh` / `user.signingkey` (never
overriding existing config). To show commits as **Verified** on GitHub,
register the printed public key as a *Signing* SSH key in account settings.

### Verification

```bash
bash scripts/verify-pull-signatures.sh    # or: make verify-pull-signatures
```

Re-verifies every entry in the ledger against `allowed_signers`; exits
non-zero if any entry fails. Recorded pulls are best-effort by design — the
audit trail must never break a checkout — but commits are **hard-gated**:
unsigned commits or missing `Signed-off-by` are rejected client-side, and
GitHub branch protection independently requires valid signatures on `main`.

## 2. Access model: fork to contribute

| Surface | Public repo (current) | Private repo (optional) |
|---|---|---|
| `git clone` / API pulls | anyone (GitHub-enforced, cannot be blocked) | authenticated users only |
| Push to `main` | blocked — PRs only (branch protection) | same |
| Contribute | fork → PR (signed commits) | collaborator fork → PR |
| Anonymous API tarball | allowed by GitHub | blocked |

`main` is branch-protected via the GitHub API: **required signatures** (the
server rejects unsigned commits on push) and **required pull request
reviews** with `enforce_admins: false` — so non-maintainers cannot push
directly; the only path in is a PR, which for outsiders means **forking
first**. The owner keeps direct-push ability (admin bypass).

**Reality check:** on a *public* repo, GitHub does not let you block
anonymous clones or API pulls — "you must fork it" is enforceable for
*contributions*, not for *reading*. If the goal is to lock down reading
too (anonymous API/clone access), the only lever is making the repo
**private**, which hides it entirely and restricts access to
collaborators. That flip is a one-command change (`gh repo edit
--visibility private`) but affects every current reader and any public
integrations — deliberate before doing it.

## 3. The source-license gate: anonymous pulls are inert

Because GitHub cannot block anonymous pulls on a public repo, the repo is
**source-available**: the code is public, but the server refuses to start
without a **source license** — a single signed line:

```
holder=<name>|granted=<YYYY-MM-DD>|scope=<full|demo>|repo=lordwilsonDev/msb-v3|SIG:<base64 ssh signature>
```

signed by the owner's key (the public half is committed at
`config/license-authorized-keys`) over the namespace
`msb-v3-source-license`. Verification reuses the same machinery as the
pull ledger (`ssh-keygen -Y verify`, stdin message — the macOS quirk).

- `scripts/run.sh` (the launchd supervisor) **refuses to start the server
  without a valid license** — an anonymous clone or API tarball is inert
  code.
- The owner's machine self-issues a license during
  `scripts/install-hooks.sh` (only when the local key matches the
  committed authorized key — nobody else can self-issue).
- Everyone else follows the intended path: **fork the repo → request a
  license → the owner signs one for you.**

### The flow

```bash
# contributor (after forking + cloning their fork)
bash scripts/request-access.sh          # opens a license-request issue, names your fork

# owner (after the request lands)
make issue-license HOLDER=jane          # or: bash scripts/issue-license.sh jane
# send ~/.msb-v3/source-license (or the printed file) back to jane

# contributor (after saving the license at ~/.msb-v3/source-license)
bash scripts/verify-license.sh          # -> VALID holder=jane scope=full ...
bash scripts/start.sh                   # server starts
```

`make license-status` / `bash scripts/verify-license.sh` report validity;
`ops-status` includes it. Tampered, wrong-key, or missing licenses are
rejected (exit 1/2). `issue-license.sh` accepts `full` (default) or
`demo` scope for a restricted tier if you ever want one.
