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
