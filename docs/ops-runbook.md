# Ops Runbook

Everything that runs on this machine to keep the stack backed up, verified,
and the disk from filling. All agents are LaunchAgents in
`~/Library/LaunchAgents/` with templates in `scripts/launchd/`; all scripts
live in `scripts/`.

**Quick commands**

```bash
make ops-status   # one-glance status: agents, disk, backups, log tails
make test-ops     # regression suite for the ops scripts (bash 3.2, scratch dirs only)
```

## Agent inventory

| Agent | Schedule | What it does | Log |
|---|---|---|---|
| `com.lordwilson.msb-backup` | daily 03:00 | SQLite + Qdrant storage backup to `~/msb-backups/msb-v3` (keep 7), checksum-verified at creation | `logs/backup.log`, `backup.err` |
| `com.lordwilson.msb-vault-backup` | Sun 04:30 | Fresh snapshot of msb-v3 into `~/Documents/Vault/Backups/` — integrity gate + full-suite restore verification, then retention prune (label keep 8) | `logs/vault-backup.log` |
| `com.lordwilson.dsh-vault-backup` | Sun 05:30 | Same for deepseek-harness (label keep 4, verify runs `pnpm install` + vitest with env-sensitive files excluded) | `~/deepseek-harness/logs/vault-backup.log` |
| `com.lordwilson.db-restore-drill` | Sun 06:30 | Restores the latest DB backup to a temp dir, re-checksums, `PRAGMA integrity_check` on every restored db + storage structure | `logs/db-restore-drill.log`, `.err` |
| `com.lordwilson.rotate-logs` | daily 06:00 | Copy-truncate rotates launchd-captured logs past a 5M cap, keeping 3 copies | `logs/rotate-logs.log` |
| `com.lordwilson.cache-trim` | Sun 06:40 | Clears the regenerable caches that refill the disk ~1G/day (Google, hermit, Citro Labs, SiriTTS, pnpm, ollama; ≥10M floor; pnpm prune = orphans only) | `logs/cache-trim.log` |
| `com.lordwilson.disk-health` | Sun 06:45 | Alerts when used% ≥ 85 (warn) / 92 (crit), or when the free-space trend projects full within 14 days | `logs/disk-health.log` |
| `com.lordwilson.ops-audit` | Sun 06:50 | Full ops self-audit: regression suite + pull-signature ledger + source license; non-zero exit alerts via the watchdog AND out-of-band channels (email/Telegram when configured); with `MSB_PUBLISH_AUDIT=1` the dated report is committed + pushed to origin | `logs/ops-audit.log` |
| `com.lordwilson.heartbeat` | daily 12:00 | Liveness line + state snapshot + `audit/` copy onto an external volume (`MSB_HEARTBEAT_DIR`); absent volume = graceful skip | `logs/heartbeat.log`, `.err` |
| `com.lordwilson.replicate` | Sun 07:05 | Mirrors the repo (incl. `.git` signed history) to a secondary node (`MSB_REPLICATION_TARGET`); unconfigured = skip, configured-but-unreachable = alert | `logs/replicate.log`, `.err` |
| `com.lordwilson.backup-watchdog` | every 15 min | Polls all agents' last exit code; fires a notification once per failure episode (re-arms on success; KeepAlive agents re-arm on a 6h timer) | `logs/backup-watchdog.log` |
| `com.lordwilson.msb-v3` | KeepAlive | The API server | `logs/gateway.out.log`, `gateway.err.log` |
| `com.lordwilson.qdrant` | KeepAlive | Qdrant vector store | `logs/qdrant.log` |

Sunday cascade: **04:30** msb code → **05:30** dsh code → **06:30** DB drill →
**06:40** cache trim → **06:45** disk-health (post-trim) → **06:50** ops
self-audit (alerts via the watchdog + email/Telegram; publishes the dated
report) → **07:05** replicate to secondary (when configured). Heartbeat
runs daily 12:00.

## Failure alert flow

The watchdog is the primary alerting path for agent failures: any agent's
completed run with a non-zero exit triggers one macOS notification
("Backup failure") + a line in `logs/backup-watchdog.log`. It is idempotent
per episode — no repeat alerts until the agent's next run succeeds (or, for
KeepAlive agents, 6h elapse). `disk-health` alerts separately ("Disk usage
warning") on its own thresholds and trend.

**Out-of-band channels** (`scripts/lib/alert.sh`, fail-soft — a missing
channel is logged, never fatal): on audit failure the ops-audit also fires
email and/or Telegram when configured. Set them in the agent's plist
(`~/Library/LaunchAgents/com.lordwilson.ops-audit.plist`, commented
examples included):

- `MSB_ALERT_EMAIL` — recipient; sent via `/usr/bin/mail` (needs a working
  MTA/postfix relay)
- `MSB_TELEGRAM_BOT_TOKEN` + `MSB_TELEGRAM_CHAT_ID` — Telegram
  `sendMessage` (via `MSB_TELEGRAM_API`, default `api.telegram.org`)

Every channel attempt is logged to `logs/ops-alerts.log`. Manual test:
`MSB_ALERT_EMAIL=you@example.com bash scripts/ops-audit.sh` with a broken
check (e.g. `MSB_PULL_LEDGER=/tmp/tampered-ledger`).

State files: `~/.backup-watchdog-state`, `~/.disk-health-state` (safe to
delete; they rebuild on the next run).

## Second signature trust (two witnesses)

The pull-signature trail is not owner-only. `config/pull-trusted-keys`
lists every trusted witness; `install-hooks.sh` seeds `~/.msb-v3/allowed_signers`
from it, and `verify-pull-signatures.sh` attributes each ledger entry to the
witness whose key signed it, reporting per-witness coverage at the end of
every audit (a trusted witness with zero entries is flagged as not-yet-active
— informational, not an error).

Add a second witness (recommended, so the trail survives the owner):

```bash
make add-trusted-signer ARGS="~/Downloads/friend.pub friend"
```

Then `make verify-pull-signatures` shows `signed by friend: N entries` and
coverage `N of M trusted witness(es) have signed the trail`.

## Self-publishing audit evidence

The dated audit report (`audit/YYYY-MM-DD_audit.md`) is written and, when
`MSB_PUBLISH_AUDIT=1` (the ops-audit plist sets it), committed (signed +
DCO) and pushed to origin — the record of what passed/failed lives in the
repository itself, not just on this machine. The `audit/` dir is also
copied off-machine by the heartbeat when a volume is configured.

```bash
make publish-audit ARGS=--dry-run   # write today's report, no git
MSB_PUBLISH_AUDIT=1 make ops-audit  # full audit + publish
```

A failed publish counts as a failed audit check (non-zero exit -> watchdog
alert). Note the publish push runs the full pre-push gate (lint +
portability suite), so a Sunday publish is also a weekly full-suite
verification.

## Off-machine redundancy

- **Heartbeat** (`com.lordwilson.heartbeat`, daily 12:00): set
  `MSB_HEARTBEAT_DIR` to an external volume mount; it appends a liveness
  line, writes a dated state snapshot, and rsyncs `audit/` there. No volume
  configured/mounted = graceful skip (exit 0).
- **Secondary replication** (`com.lordwilson.replicate`, Sun 07:05): set
  `MSB_REPLICATION_TARGET` to `user@host:/path` (rsync over ssh,
  BatchMode + 5s connect timeout) or a local path. Unconfigured = skip;
  configured-but-unreachable = non-zero exit, so the watchdog alerts.

Scheduled agents deliberately do **not** use `KeepAlive` — that would
relaunch a finished weekly job in an endless loop. Liveness for the machine
itself is the heartbeat + watchdog pairing, not KeepAlive on cron jobs.

## Testing & audit

```bash
make test-ops          # regression suite (or: bash scripts/test-ops.sh)
make ops-audit         # full audit: suite + ledger + license (or: bash scripts/ops-audit.sh)
```

Runs the regression suite under macOS `/bin/bash` (3.2 — the interpreter
launchd uses) against scratch dirs only: never touches the real vault,
caches, state, or agents. Covers the vault-backup cycle + per-label
retention + integrity/restore failure paths, disk-health alert/episode/
trend, cache-trim, the watchdog alert state machine, and rotate-logs.

The vault backups' full-suite restore verification is additionally proven
by the pre-push portability gate (a fresh checkout on a foreign path, all
tests green) on every push.

## Disabling / re-enabling an agent

```bash
launchctl bootout gui/$(id -u)/com.lordwilson.<label>      # stop + unload
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lordwilson.<label>.plist
launchctl kickstart gui/$(id -u)/com.lordwilson.<label>    # run once now
```

Deleting the plist from `~/Library/LaunchAgents/` removes it permanently.
Note the 600s ThrottleInterval: a kicked job may sit in `spawn scheduled`
briefly after a recent run — that is normal.

## Key overrides

All scripts honor env overrides (see their headers) for testing and
relocation:

- `vault-backup.sh`: `MSB_BACKUP_SRC`, `MSB_VAULT`, `MSB_BACKUP_LABEL`,
  `MSB_BACKUP_KEEP`, `MSB_BACKUP_VERIFY` (0 disables the restore check),
  `MSB_BACKUP_VERIFY_CMD`, `MSB_BACKUP_INTEGRITY_PATHS`, `MSB_BACKUP_LOG`
- `disk-health.sh`: `MSB_DISK_WARN_PCT`, `MSB_DISK_CRIT_PCT`,
  `MSB_DISK_HORIZON_DAYS`, `MSB_DISK_STATE`, `MSB_DISK_LOG`, `MSB_DISK_MOUNT`
- `cache-trim.sh`: `MSB_CACHE_DIRS`, `MSB_CACHE_MIN_MB`, `MSB_CACHE_LOG`
- `rotate-logs.sh`: `MSB_ROTATE_CAP`, `MSB_ROTATE_KEEP`, `MSB_ROTATE_TARGETS`,
  `MSB_ROTATE_LOG`
- `backup-watchdog.sh`: `MSB_WATCHDOG_STATE`, `MSB_WATCHDOG_LOG`,
  `MSB_WATCHDOG_AGENTS`, `MSB_WATCHDOG_REARM`
- `ops-audit.sh`: `MSB_OPS_AUDIT_LOG`, `MSB_PUBLISH_AUDIT`,
  `MSB_AUDIT_SKIP_SUITE` (test hook), plus the check overrides it passes
  through (`MSB_PULL_LEDGER`, `MSB_PULL_ALLOWED`, `MSB_LICENSE_FILE`, ...)
- `alert.sh` (sourced): `MSB_ALERT_EMAIL`, `MSB_TELEGRAM_BOT_TOKEN`,
  `MSB_TELEGRAM_CHAT_ID`, `MSB_TELEGRAM_API`, `MSB_ALERT_LOG`
- `publish-audit.sh`: `MSB_AUDIT_DIR`, `MSB_AUDIT_SUMMARY`, `MSB_PUBLISH_AUDIT`
- `heartbeat.sh`: `MSB_HEARTBEAT_DIR`, `MSB_HEARTBEAT_LOG`, `MSB_AUDIT_DIR`
- `replicate-to-secondary.sh`: `MSB_REPLICATION_TARGET`, `MSB_REPLICATION_LOG`
- `add-trusted-signer.sh`: `MSB_TRUSTED_KEYS`, `MSB_PULL_ALLOWED`
- `db-restore-drill.sh`: `MSB_PYTHON`

## Portability contract

The pre-push gate rejects hardcoded `/Users/...` literals in any `*.sh` /
`*.py` (plists are exempt — they are machine-specific templates). New or
edited scripts must derive paths from
`REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"` (and `$HOME` for
sibling-repo or user paths), with env overrides for testing. Scripts must
also stay `/bin/bash` 3.2 compatible: no `mapfile`, no associative arrays,
and no `${arr[@]}` expansion on an empty array under `set -u`.

## Known limitations

- **Qdrant storage consistency**: the DB backup copies `storage/` while
  Qdrant may be running. The checksum manifest + structural check catch
  torn/incomplete copies at backup and drill time, but a live-copy snapshot
  is not a Qdrant-consistent snapshot — full logical consistency would
  require a second Qdrant instance to load the restored storage. The
  checksums make a silently-corrupt backup fail loudly instead.
- **Disk headroom**: the boot disk runs near 95–98% used. `cache-trim` +
  DB keep-7 cap the *growth*; durable headroom needs moving `~/models`
  (6.9G) or the Docker VM image to another volume.
