# CLAUDE.archive.md

Deep reference moved out of `CLAUDE.md` on 2026-08-29 to keep the operational
surface under ~160 lines. Nothing here is load-bearing for day-to-day work —
it's runbooks and rationale you reach for a few times a year.

---

## CI internals

### Self-hosted runner (harness-gate)

`~/actions-runner`, name `msb-v3-mac-arm64`, labels `macOS, self-hosted`. Runs
the harness-gate job because it exercises machine-local state (`~/bin/webcheck.py`,
`~/video-harness/evidence`, live `:8766`/`:6333`, system Chrome). Supervised by
user-domain LaunchAgent `com.blackswanlabz.msb-v3.runner` (source
`scripts/com.blackswanlabz.msb-v3.runner.plist`, installed copy in
`~/Library/LaunchAgents/`, no sudo — `launchctl bootstrap gui/$(id -u)`).
Full fresh-machine registration runbook: `Self-Hosted-CI-Runner-macOS` (30-012)
in the vault, `~/Documents/Vault/30_Architecture/decisions/`.

### Keeping harness-gate green

Automatic via daily LaunchAgent `com.blackswanlabz.harness-evidence` (06:30;
template `scripts/com.blackswanlabz.harness-evidence.plist` →
`scripts/freshen-harness-evidence.sh`) — skips when evidence is fresh, else
re-runs `make run/run-p1/run-p2` in `~/video-harness` and proves the gate (log
`~/Library/Logs/msb-harness-evidence.log`). `harness-gate.yml` also runs the
freshener as a pre-flight step, so a stale-evidence push self-heals before the
gate. Manual freshen: `bash scripts/freshen-harness-evidence.sh`. Local dry-run:
`make harness-gate-dryrun`.

### Codecov coverage upload / token rotation

The `msb-v3 CI` test job uploads coverage via `codecov/codecov-action@v4` with
`token: ${{ secrets.CODECOV_TOKEN }}` and `fail_ci_if_error: true` — a
revoked/expired token turns the test job red instead of silently dropping
coverage. The token is REQUIRED: v4 has no tokenless upload for main-repo pushes.
Rotate:
1. Regenerate the Repository Upload Token at
   `app.codecov.io/gh/lordwilsonDev/msb-v3` → Settings → Config.
2. `printf '%s' '<NEW_TOKEN>' | gh secret set CODECOV_TOKEN -R lordwilsonDev/msb-v3`
3. Verify: the next CI test job's `Upload coverage` step is green and the commit
   page on Codecov shows the %. (Repo going private changes nothing — same token
   secret covers it.)

---

## Governance internals

The autonomy brakes the flywheel runs behind (blueprint §0.6 — the engine does
not run itself until these are proven).

- **Ouroboros governor** — deterministic convergence throttle on MoIE expansion
  (HALT on stall/duplicate-ratio, SLOW on declining novelty; suggests
  `trim_candidates`, never deletes).
- **Budget caps** — research_calls / tokens / iterations per rolling window; `-1`
  unlimited, `0` denies all. Caps halt the loop.
- **Approval queue** — `build`, `combine`, `promote_knowledge`, `git_commit`,
  `vault_write` never run without an owner-APPROVED item; survives restarts;
  transitions only from PENDING (double-decide = 409).
- **Kill switch** — one control to pause the whole loop; survives restarts;
  unreadable ⇒ armed.
- `POST /governance/check` — drill endpoint: run the exact gate the flywheel
  calls and see the verdict without executing anything.

The brakes gate the **flywheel (Phase 2)** — today's endpoints don't call
`Guard.check_run` yet. `POST /governance/check` is the drill that proves the
gates; the loop wires `check_run` + `record_action` when it lands.

---

## Flywheel internals

Every stage transition is gated by the Phase 0B brakes: kill switch + iterations
budget on every stage, research_calls on charge/scan, owner approval at
**build/combine/record** (the turn parks at `WAITING_APPROVAL` until
`make flywheel-approve ID=...` or the CLI/API approve), and the Ouroboros
governor fed the charge signal. Turn state persists in `data/flywheel/turns.db`
and survives restarts; every transition is audited (component `flywheel`).

The generative brain is pluggable: `--charger stub` (deterministic, offline,
UIM-format-compatible — the default, runs without burning tokens) or
`--charger sovereign` (real `SovereignResearchAssistant`, local LLM). The paper
scanner is the real Tavily feed (Phase 2b): `TavilyScanner` searches arxiv.org
via `TavilyResearchBackend` (`TAVILY_API_KEY` from `.env`), persists matches to
`runtime/flywheel/scans/{turn_id}.json`, and the surface stage surfaces paper
titles as next problems. No key or a feed outage degrades to an honest
`0 papers` note — the scan never fabricates. `StubScanner` is the explicit
offline fallback (inject it in tests; CI never touches the network). The cockpit
has a read-only FLYWHEEL panel.

---

## Tag ruleset

Repo ruleset `release-tag-immutability`, target **tag**, active, `refs/tags/v*` —
rules: `deletion` (tags cannot be deleted) + `update` (tags cannot be
force-moved). Verified live 2026-08-13: `v*` tag creation succeeds, force-update
and deletion are rejected (owner is NOT auto-bypassed; the rules bind everyone).

**Why there is no required-status-check on tag creation:** GitHub evaluates tag
rulesets' required status checks against the **tag ref**, not the underlying
commit — a check suite only exists on a tag ref after a workflow ran on that
tag, which requires the tag to already exist. So a status check cannot gate a
tag's first creation (chicken-and-egg; `do_not_enforce_on_create` exists
precisely because of it, and makes creation un-gated). Verification is enforced
where it CAN be: `scripts/verify-release.sh` runs locally pre-tag,
`release-verify.yml` runs post-tag in CI, and every commit on `main` passes the
full gates before `scripts/release.sh` is allowed to tag it. The tag ruleset
locks that verified state: once a release tag exists it is immutable.

**Emergency path (e.g. cut a bad release tag):**
1. Disable the ruleset: `gh api repos/lordwilsonDev/msb-v3/rulesets/20801997 -X PUT --input <(echo '{"enforcement": "disabled"}')`
2. Delete the tag (API ref deletion is more reliable than `git push origin :tag`):
   `gh api repos/lordwilsonDev/msb-v3/git/refs/tags/<tag> -X DELETE`
3. Re-enable: `gh api repos/lordwilsonDev/msb-v3/rulesets/20801997 -X PUT --input <(echo '{"enforcement": "active"}')`
