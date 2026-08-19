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
| `com.lordwilson.ops-audit` | Sun 06:50 | Full ops self-audit: regression suite + pull-signature ledger + source license; non-zero exit alerts via the watchdog | `logs/ops-audit.log` |
| `com.lordwilson.backup-watchdog` | every 15 min | Polls all agents' last exit code; fires a notification once per failure episode (re-arms on success; KeepAlive agents re-arm on a 6h timer) | `logs/backup-watchdog.log` |
| `com.lordwilson.msb-v3` | KeepAlive | The API server | `logs/gateway.out.log`, `gateway.err.log` |
| `com.lordwilson.qdrant` | KeepAlive | Qdrant vector store | `logs/qdrant.log` |

Sunday cascade: **04:30** msb code → **05:30** dsh code → **06:30** DB drill →
**06:40** cache trim → **06:45** disk-health (post-trim) → **06:50** ops
self-audit (any regression alerts via the watchdog).

## Failure alert flow

The watchdog is the single alerting path for agent failures: any agent's
completed run with a non-zero exit triggers one macOS notification
("Backup failure") + a line in `logs/backup-watchdog.log`. It is idempotent
per episode — no repeat alerts until the agent's next run succeeds (or, for
KeepAlive agents, 6h elapse). `disk-health` alerts separately ("Disk usage
warning") on its own thresholds and trend.

State files: `~/.backup-watchdog-state`, `~/.disk-health-state` (safe to
delete; they rebuild on the next run).

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
