#!/usr/bin/env bash
set -euo pipefail

# test-ops.sh — regression suite for the ops scripts: vault-backup,
# disk-health, cache-trim, backup-watchdog, rotate-logs.
#
# Every check runs under macOS /bin/bash (3.2 — the interpreter launchd
# uses; homebrew bash 5 hides real bugs like the empty-array 'unbound
# variable' crash) and against SCRATCH dirs only: the real vault, caches,
# state files, and agents are never touched. Fast by design — the
# vault-backup verify step uses a trivial command here; the full-suite
# restore verification is exercised by the pre-push portability gate.
#
# Run: bash scripts/test-ops.sh

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d /tmp/test-ops-XXXXXX)"
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); echo "  PASS: $1"; }
bad() { FAIL=$((FAIL + 1)); echo "  FAIL: $1"; }
section() { echo; echo "== $1 =="; }

# Run a script with a scratch env; echo only its exit code.
rc_of() { "$@" >/dev/null 2>&1; echo $?; }

# Count dirs matching a glob (empty glob is fine — ls would exit non-zero).
# NOTE: $1 must stay UNQUOTED so the shell expands the glob before ls sees it.
count_glob() { ls -d $1 2>/dev/null | wc -l | tr -d ' ' || true; }

# ---------------------------------------------------------------------------
section "vault-backup: cycle, retention, integrity, restore plumbing"
S="$TMP/src1"
V="$TMP/vault1"
mkdir -p "$S/sub" && echo x > "$S/sub/f.txt"
for i in 1 2 3; do
  rc=$(rc_of env MSB_BACKUP_SRC="$S" MSB_VAULT="$V" MSB_BACKUP_LABEL=testops \
    MSB_BACKUP_KEEP=2 MSB_BACKUP_VERIFY_CMD=true MSB_BACKUP_INTEGRITY_PATHS=sub \
    MSB_BACKUP_LOG="$TMP/vb.log" bash "$ROOT/scripts/vault-backup.sh")
  [ "$rc" = 0 ] || bad "run $i exited $rc"
done
n=$(count_glob "$V/Backups/testops-*")
[ "$n" = 2 ] && ok "KEEP=2 pruned to $n snapshots" || bad "expected 2 snapshots, got $n"
nidx=$(grep -c "^$V/Backups/testops-" "$V/Backups/.backup-index" 2>/dev/null || true)
[ "$nidx" = 2 ] && ok "index has 2 entries" || bad "index has $nidx entries"
grep -q "integrity OK" "$TMP/vb.log" && ok "integrity gate ran" || bad "no integrity line in log"
grep -q "restore OK" "$TMP/vb.log" && ok "restore verify ran" || bad "no restore OK line in log"

section "vault-backup: per-label retention (second label must not touch first)"
for i in 1 2 3; do
  rc=$(rc_of env MSB_BACKUP_SRC="$S" MSB_VAULT="$V" MSB_BACKUP_LABEL=testops2 \
    MSB_BACKUP_KEEP=2 MSB_BACKUP_VERIFY_CMD=true MSB_BACKUP_INTEGRITY_PATHS=sub \
    MSB_BACKUP_LOG="$TMP/vb2.log" bash "$ROOT/scripts/vault-backup.sh")
  [ "$rc" = 0 ] || bad "testops2 run $i exited $rc"
done
n1=$(count_glob "$V/Backups/testops-*")
n2=$(count_glob "$V/Backups/testops2-*")
nidx=$(grep -cE "testops" "$V/Backups/.backup-index" || true)
if [ "$n1" = 2 ] && [ "$n2" = 2 ] && [ "$nidx" = 4 ]; then
  ok "both labels kept ($n1 + $n2, index $nidx)"
else
  bad "label isolation broken: testops=$n1 testops2=$n2 index=$nidx"
fi

section "vault-backup: integrity failure deletes snapshot + aborts"
V2="$TMP/vault2"
rc=$(rc_of env MSB_BACKUP_SRC="$S" MSB_VAULT="$V2" MSB_BACKUP_LABEL=bad \
  MSB_BACKUP_VERIFY_CMD=true MSB_BACKUP_INTEGRITY_PATHS="nonexistent-dir" \
  MSB_BACKUP_LOG="$TMP/vb-bad.log" bash "$ROOT/scripts/vault-backup.sh")
left=$(count_glob "$V2/Backups/bad-*")
[ "$rc" != 0 ] && [ "$left" = 0 ] && ok "integrity failure aborted (rc=$rc, snapshot removed)" \
  || bad "expected abort+delete, got rc=$rc snapshots=$left"

section "vault-backup: restore failure deletes snapshot + aborts"
V3="$TMP/vault3"
rc=$(rc_of env MSB_BACKUP_SRC="$S" MSB_VAULT="$V3" MSB_BACKUP_LABEL=badrestore \
  MSB_BACKUP_VERIFY_CMD=false MSB_BACKUP_LOG="$TMP/vb-br.log" \
  bash "$ROOT/scripts/vault-backup.sh")
left=$(count_glob "$V3/Backups/badrestore-*")
[ "$rc" != 0 ] && [ "$left" = 0 ] && ok "restore failure aborted (rc=$rc, snapshot removed)" \
  || bad "expected abort+delete, got rc=$rc snapshots=$left"

# ---------------------------------------------------------------------------
section "disk-health: alert once per episode, escalation, clear, trend"
D="$TMP/dh"
run_dh() { # state log warn crit horizon
  rc_of env MSB_DISK_STATE="$1" MSB_DISK_LOG="$2" MSB_DISK_WARN_PCT="$3" \
    MSB_DISK_CRIT_PCT="$4" MSB_DISK_HORIZON_DAYS="$5" \
    bash "$ROOT/scripts/disk-health.sh"
}
run_dh "$D/state" "$D/log" 1 1 0 >/dev/null   # usage >= crit -> alert
n1=$(grep -c ALERT "$D/log" || true)
[ "$n1" -ge 1 ] && ok "alert fired on high usage" || bad "no alert on high usage"
run_dh "$D/state" "$D/log" 1 1 0 >/dev/null   # same episode -> no re-alert
n2=$(grep -c ALERT "$D/log" || true)
[ "$n2" = "$n1" ] && ok "episode held (still $n2 alert(s))" || bad "re-alerted: $n1 -> $n2"
run_dh "$D/state" "$D/log" 100 100 0 >/dev/null  # conditions clear -> episode resets
[ "$(head -1 "$D/state")" = "EPISODE|0|0" ] && ok "episode cleared" || bad "episode not cleared: $(head -1 "$D/state")"
# trend: crafted shrinking history, immediate thresholds off
printf 'EPISODE|0|0\n2026-07-22 00:00|100000000\n2026-08-05 00:00|80000000\n' > "$D/state2"
run_dh "$D/state2" "$D/log2" 100 100 60 >/dev/null
grep -q "projected full" "$D/log2" && ok "trend alert fired (projected full)" || bad "no trend alert"

# ---------------------------------------------------------------------------
section "cache-trim: trims big, keeps small, skips missing"
mkdir -p "$TMP/ct-big" "$TMP/ct-small"
dd if=/dev/zero of="$TMP/ct-big/big.bin" bs=1m count=30 2>/dev/null
dd if=/dev/zero of="$TMP/ct-small/small.bin" bs=1k count=2 2>/dev/null
rc=$(rc_of env MSB_CACHE_DIRS="$TMP/ct-big $TMP/ct-small $TMP/ct-missing" \
  MSB_CACHE_LOG="$TMP/ct.log" bash "$ROOT/scripts/cache-trim.sh")
big_left=$(ls -A "$TMP/ct-big" 2>/dev/null | wc -l | tr -d ' ')
small_left=$(ls -A "$TMP/ct-small" 2>/dev/null | wc -l | tr -d ' ')
[ "$rc" = 0 ] && [ "$big_left" = 0 ] && [ "$small_left" = 1 ] \
  && ok "trimmed big (0 left), kept small (1), skipped missing (rc=0)" \
  || bad "rc=$rc big_left=$big_left small_left=$small_left"

# ---------------------------------------------------------------------------
section "backup-watchdog: one alert per failure episode, clears on recovery"
mkdir -p "$TMP/fakebin"
cat > "$TMP/fakebin/launchctl" <<'EOF'
#!/usr/bin/env bash
# stub for: launchctl print gui/UID/LABEL
n="$(cat "$FAKE_RUNS" 2>/dev/null || echo 0)"
st="$(cat "$FAKE_STATE" 2>/dev/null || echo waiting)"
if [ -f "$FAKE_MISSING" ]; then echo "no such service" >&2; exit 1; fi
echo -e "\tstate = $st"
echo -e "\truns = $n"
if [ -f "$FAKE_FAIL" ]; then echo -e "\tlast exit code = 1"; else echo -e "\tlast exit code = 0"; fi
EOF
chmod +x "$TMP/fakebin/launchctl"
wd() { # runs fail-file state log -> exit code
  echo "$1" > "$TMP/runs"
  if [ "$2" = fail ]; then : > "$TMP/fail"; else rm -f "$TMP/fail"; fi
  echo "${3:-waiting}" > "$TMP/fstate"
  rc_of env PATH="$TMP/fakebin:$PATH" FAKE_RUNS="$TMP/runs" FAKE_FAIL="$TMP/fail" \
    FAKE_STATE="$TMP/fstate" MSB_WATCHDOG_STATE="$4" MSB_WATCHDOG_LOG="$5" \
    MSB_WATCHDOG_AGENTS="com.lordwilson.fake|fake agent|fake.err" \
    bash "$ROOT/scripts/backup-watchdog.sh"
}
W="$TMP/wd-state"
WLOG="$TMP/wd.log"
wd 1 fail waiting "$W" "$WLOG" >/dev/null     # new failed run -> alert
a1=$(grep -c ALERT "$WLOG" || true)
wd 1 fail waiting "$W" "$WLOG" >/dev/null     # same run, no new -> no alert
a2=$(grep -c ALERT "$WLOG" || true)
wd 2 fail waiting "$W" "$WLOG" >/dev/null     # new failed run, already alerted -> no alert
a3=$(grep -c ALERT "$WLOG" || true)
wd 3 ok waiting "$W" "$WLOG" >/dev/null       # success -> episode clears
wd 4 fail waiting "$W" "$WLOG" >/dev/null     # new failure after recovery -> alert again
a4=$(grep -c ALERT "$WLOG" || true)
[ "$a1" = 1 ] && [ "$a2" = 1 ] && [ "$a3" = 1 ] && [ "$a4" = 2 ] \
  && ok "alert state machine correct (1,1,1,2)" \
  || bad "alert counts wrong: $a1,$a2,$a3,$a4"
# in-flight scheduled run (state=running, stale prev exit) must NOT alert
wd 5 fail running "$W" "$WLOG" >/dev/null
a5=$(grep -c ALERT "$WLOG" || true)
# completed success for the same run count clears any stale alert
wd 5 ok waiting "$W" "$WLOG" >/dev/null
a6=$(grep -c ALERT "$WLOG" || true)
# exit-code change on the SAME run count (0 -> 1) is a real event -> alert
wd 5 fail waiting "$W" "$WLOG" >/dev/null
a7=$(grep -c ALERT "$WLOG" || true)
if [ "$a5" = 2 ] && [ "$a6" = 2 ] && [ "$a7" = 3 ] \
  && [ "$(cut -d'|' -f3,4 "$W")" = "1|1" ]; then
  ok "in-flight skipped (2), completed success cleared (2), same-run exit change alerted (3)"
else
  bad "in-flight/exit-change handling wrong: $a5,$a6,$a7 state=$(cat "$W")"
fi
# missing agent (launchctl print fails) -> alert once per episode; recovery clears
wd_missing() { # state log
  : > "$TMP/missing"
  rc_of env PATH="$TMP/fakebin:$PATH" FAKE_RUNS=0 FAKE_STATE=waiting FAKE_MISSING="$TMP/missing" \
    MSB_WATCHDOG_STATE="$1" MSB_WATCHDOG_LOG="$2" \
    MSB_WATCHDOG_AGENTS="com.lordwilson.fake|fake agent|fake.err" \
    bash "$ROOT/scripts/backup-watchdog.sh"
}
wd_missing "$W" "$WLOG" >/dev/null      # agent gone -> alert
a8=$(grep -c ALERT "$WLOG" || true)
wd_missing "$W" "$WLOG" >/dev/null      # same episode -> no re-alert
a9=$(grep -c ALERT "$WLOG" || true)
rm -f "$TMP/missing"
wd 6 ok waiting "$W" "$WLOG" >/dev/null   # agent back -> episode clears
wd 7 fail waiting "$W" "$WLOG" >/dev/null   # (next real failure still alerts)
a10=$(grep -c ALERT "$WLOG" || true)
if [ "$a8" = 4 ] && [ "$a9" = 4 ] && [ "$a10" = 5 ] \
  && [ "$(cut -d'|' -f2,4 "$W")" = "7|1" ]; then
  ok "missing agent alerts once (4), recovery clears, next failure alerts (5)"
else
  bad "missing-agent handling wrong: $a8,$a9,$a10 state=$(cat "$W")"
fi

# ---------------------------------------------------------------------------
section "rotate-logs: cap + shift with scratch targets"
mkdir -p "$TMP/rl"
f="$TMP/rl/big.log"
dd if=/dev/zero of="$f" bs=1m count=20 2>/dev/null
rl() {
  rc_of env MSB_ROTATE_TARGETS="$f" MSB_ROTATE_CAP=1048576 MSB_ROTATE_KEEP=2 \
    MSB_ROTATE_LOG="$TMP/rl/rotate.log" bash "$ROOT/scripts/rotate-logs.sh"
}
rl >/dev/null
[ -f "$f.1" ] && [ "$(stat -f%z "$f")" -lt 1048576 ] \
  && ok "rotated (live < cap, .1 exists)" || bad "first rotation failed"
dd if=/dev/zero of="$f" bs=1m count=20 2>/dev/null
rl >/dev/null
[ -f "$f.2" ] && ok "history shifted (.2 exists)" || bad "second rotation did not shift"
[ -f "$f" ] && [ "$(stat -f%z "$f")" -lt 1048576 ] && ok "live file capped" || bad "live file not capped"

# ---------------------------------------------------------------------------
section "license: issue, verify, tamper, wrong-key, missing"
mkdir -p "$TMP/lic"
ssh-keygen -q -t ed25519 -N "" -C msb-signing-key -f "$TMP/lic/owner-key"
printf 'msb-signing-key %s\n' "$(cut -d' ' -f1,2 "$TMP/lic/owner-key.pub")" > "$TMP/lic/authorized"
LK="$TMP/lic/owner-key" LA="$TMP/lic/authorized"
# issue + verify with the authorized key
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" MSB_LICENSE_FILE="$TMP/lic/lic" \
  bash "$ROOT/scripts/issue-license.sh" tester >/dev/null 2>&1
[ -f "$TMP/lic/lic" ] && ok "license issued" || bad "issue-license failed"
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" bash "$ROOT/scripts/verify-license.sh" "$TMP/lic/lic" >/dev/null 2>&1 \
  && ok "license verifies (rc=0)" || bad "valid license rejected"
# tampered license must be rejected
sed 's/|SIG:/|SIG:AAAA/' "$TMP/lic/lic" > "$TMP/lic/tampered"
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" bash "$ROOT/scripts/verify-license.sh" "$TMP/lic/tampered" >/dev/null 2>&1 \
  && bad "tampered license accepted" || ok "tampered license rejected"
# a license self-issued with a DIFFERENT key must be rejected (no self-issue)
ssh-keygen -q -t ed25519 -N "" -C msb-signing-key -f "$TMP/lic/other-key"
MSB_LICENSE_KEY="$TMP/lic/other-key" MSB_LICENSE_AUTHORIZED="$LA" \
  MSB_LICENSE_FILE="$TMP/lic/fake" bash "$ROOT/scripts/issue-license.sh" attacker >/dev/null 2>&1
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" bash "$ROOT/scripts/verify-license.sh" "$TMP/lic/fake" >/dev/null 2>&1 \
  && bad "wrong-key license accepted" || ok "wrong-key (self-issued) license rejected"
# missing license -> exit 2 (guard the failing call: set -e would kill us)
rc=0
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" bash "$ROOT/scripts/verify-license.sh" "$TMP/lic/none" >/dev/null 2>&1 || rc=$?
[ "$rc" = 2 ] && ok "missing license -> exit 2" || bad "missing license rc=$rc (want 2)"

# ---------------------------------------------------------------------------
section "alert channels: email captured, telegram skipped when unconfigured"
mkdir -p "$TMP/fakemail"
MAIL_CAPTURE="$TMP/mail-capture"
cat > "$TMP/fakemail/mail" <<'EOF'
#!/usr/bin/env bash
echo "subject=$2" >> "$MAIL_CAPTURE"
cat >> "$MAIL_CAPTURE"
EOF
chmod +x "$TMP/fakemail/mail"
rm -f "$MAIL_CAPTURE"
# shellcheck source=../scripts/lib/alert.sh
REPO="$ROOT" MAIL_CAPTURE="$MAIL_CAPTURE" MSB_ALERT_EMAIL=ops@example.com \
  MSB_ALERT_LOG="$TMP/alert.log" PATH="$TMP/fakemail:$PATH" bash -c '
    . "$REPO/scripts/lib/alert.sh"
    notify_all "TEST TITLE" "test body line"
  '
[ -f "$MAIL_CAPTURE" ] && grep -q "subject=TEST TITLE" "$MAIL_CAPTURE" \
  && ok "email channel delivered (fake mail captured subject + body)" \
  || bad "email not captured: $(cat "$MAIL_CAPTURE" 2>/dev/null)"
grep -q "telegram skipped (token=unset chat=unset)" "$TMP/alert.log" \
  && ok "telegram skipped cleanly when unconfigured" || bad "telegram skip not logged"
rc=$(rc_of env ROOT="$ROOT" MSB_ALERT_LOG="$TMP/alert2.log" bash -c '
    . "$ROOT/scripts/lib/alert.sh"; notify_all "x" "y"
  ')
[ "$rc" = 0 ] && ok "notify_all fail-soft (rc=0 with no channels)" || bad "notify_all rc=$rc"

# ---------------------------------------------------------------------------
section "trusted signers: second-witness attribution + coverage"
ssh-keygen -q -t ed25519 -N "" -C msb-signing-key -f "$TMP/lic/friend-key"
ssh-keygen -q -t ed25519 -N "" -C msb-signing-key -f "$TMP/lic/rogue-key"
TRUSTED="$TMP/trusted"
printf 'msb-signing-key %s\n' "$(cut -d' ' -f1,2 "$TMP/lic/owner-key.pub")" > "$TRUSTED"
printf 'friend %s\n' "$(cut -d' ' -f1,2 "$TMP/lic/friend-key.pub")" >> "$TRUSTED"
ALLOWED2="$TMP/allowed2"
cp "$TRUSTED" "$ALLOWED2"
E1='2026-08-19T00:00:01Z|owner@host|a..b'
E2='2026-08-19T00:00:02Z|friend@host|b..c'
E3='2026-08-19T00:00:03Z|rogue@host|c..d'
sig_of() { printf '%s' "$1" | ssh-keygen -Y sign -f "$2" -n msb-v3-pull 2>/dev/null | base64 | tr -d '\n'; }
LEDGER2="$TMP/ledger2"
{
  printf '%s|SIG:%s\n' "$E1" "$(sig_of "$E1" "$TMP/lic/owner-key")"
  printf '%s|SIG:%s\n' "$E2" "$(sig_of "$E2" "$TMP/lic/friend-key")"
  printf '%s|SIG:%s\n' "$E3" "$(sig_of "$E3" "$TMP/lic/rogue-key")"
} > "$LEDGER2"
rc=0
OUT=$(env MSB_PULL_LEDGER="$LEDGER2" MSB_PULL_ALLOWED="$ALLOWED2" \
  bash "$ROOT/scripts/verify-pull-signatures.sh" 2>&1) || rc=$?
if [ "${rc:-0}" != 0 ] \
  && echo "$OUT" | grep -q "signed by msb-signing-key" \
  && echo "$OUT" | grep -q "signed by friend" \
  && echo "$OUT" | grep -q "INVALID (no trusted witness)"; then
  ok "entries attributed per witness; untrusted signer rejected (rc=${rc:-0})"
else
  bad "attribution failed: rc=${rc:-0} out=$OUT"
fi
LEDGER3="$TMP/ledger3"
{
  printf '%s|SIG:%s\n' "$E1" "$(sig_of "$E1" "$TMP/lic/owner-key")"
  printf '%s|SIG:%s\n' "$E2" "$(sig_of "$E2" "$TMP/lic/friend-key")"
} > "$LEDGER3"
OUT=$(env MSB_PULL_LEDGER="$LEDGER3" MSB_PULL_ALLOWED="$ALLOWED2" \
  bash "$ROOT/scripts/verify-pull-signatures.sh" 2>&1) && rc=0 || rc=$?
[ "$rc" = 0 ] && echo "$OUT" | grep -q "2 of 2 trusted witness(es)" \
  && ok "two-witness trail verifies (rc=0, coverage 2/2)" \
  || bad "two-witness verify rc=$rc out=$OUT"
env MSB_TRUSTED_KEYS="$TRUSTED" MSB_PULL_ALLOWED="$ALLOWED2" \
  bash "$ROOT/scripts/add-trusted-signer.sh" "$TMP/lic/rogue-key.pub" rogue >/dev/null
if grep -q "^rogue " "$TRUSTED" && grep -q "^rogue " "$ALLOWED2"; then
  ok "add-trusted-signer appended + re-seeded allowed_signers"
else
  bad "add-trusted-signer did not append"
fi
OUT=$(env MSB_PULL_LEDGER="$LEDGER2" MSB_PULL_ALLOWED="$ALLOWED2" \
  bash "$ROOT/scripts/verify-pull-signatures.sh" 2>&1) && rc=0 || rc=$?
[ "$rc" = 0 ] && echo "$OUT" | grep -q "3 of 3 trusted witness(es)" \
  && ok "added witness now verifies (coverage 3/3)" \
  || bad "post-add verify rc=$rc out=$OUT"

# ---------------------------------------------------------------------------
section "ops-audit: failure path alerts via email/telegram wiring, success stays quiet"
AUD_LOG="$TMP/aud.log"
AUD_ALERT="$TMP/aud-alert.log"
printf '2026-08-19T00:00:00Z|tester@host|a..b|SIG:AAAA\n' > "$TMP/aud-ledger-bad"
printf 'msb-signing-key %s\n' "$(cut -d' ' -f1,2 "$TMP/lic/owner-key.pub")" > "$TMP/aud-allowed"
# Isolation guard: these audits must NEVER publish. When the real agent runs
# (MSB_PUBLISH_AUDIT=1 in its env), the suite's audits here would INHERIT it
# and commit+push reports — that race corrupted the first live audit run. We
# simulate the agent env (export) and strip it before the audit (env -u); the
# HEAD/audit-dir assertions below fail if the leak ever returns.
HEAD_BEFORE=$(git -C "$ROOT" rev-parse HEAD)
AUDIT_BEFORE=$(ls "$ROOT/audit")
rm -f "$MAIL_CAPTURE"
rc=0
(
  export MSB_PUBLISH_AUDIT=1
  env -u MSB_PUBLISH_AUDIT -u MSB_AUDIT_DIR MSB_AUDIT_SKIP_SUITE=1 \
    MSB_PULL_LEDGER="$TMP/aud-ledger-bad" MSB_PULL_ALLOWED="$TMP/aud-allowed" \
    MSB_LICENSE_FILE="$TMP/lic/lic" MSB_LICENSE_AUTHORIZED="$LA" \
    MSB_ALERT_EMAIL=ops@example.com MSB_ALERT_LOG="$AUD_ALERT" MSB_OPS_AUDIT_LOG="$AUD_LOG" \
    MAIL_CAPTURE="$MAIL_CAPTURE" PATH="$TMP/fakemail:$PATH" \
    bash "$ROOT/scripts/ops-audit.sh" >/dev/null 2>&1
) || rc=$?
if [ "$rc" != 0 ] && grep -q "AUDIT-FAIL" "$AUD_LOG" \
  && [ -f "$MAIL_CAPTURE" ] && grep -q "OPS AUDIT FAILED" "$MAIL_CAPTURE" \
  && grep -q "email sent" "$AUD_ALERT" && grep -q "telegram skipped" "$AUD_ALERT"; then
  ok "audit failure -> exit $rc, email fired, telegram skipped cleanly"
else
  bad "failure path wrong: rc=$rc capture=$(cat "$MAIL_CAPTURE" 2>/dev/null)"
fi
# success path: valid ledger + valid license, no alert sent
LEDGER_OK="$TMP/aud-ledger-ok"
printf '%s|SIG:%s\n' "$E1" "$(sig_of "$E1" "$TMP/lic/owner-key")" > "$LEDGER_OK"
rm -f "$MAIL_CAPTURE" "$AUD_LOG"
rc=0
(
  export MSB_PUBLISH_AUDIT=1
  env -u MSB_PUBLISH_AUDIT -u MSB_AUDIT_DIR MSB_AUDIT_SKIP_SUITE=1 \
    MSB_PULL_LEDGER="$LEDGER_OK" MSB_PULL_ALLOWED="$TMP/aud-allowed" \
    MSB_LICENSE_FILE="$TMP/lic/lic" MSB_LICENSE_AUTHORIZED="$LA" \
    MSB_ALERT_EMAIL=ops@example.com MSB_ALERT_LOG="$AUD_ALERT" MSB_OPS_AUDIT_LOG="$AUD_LOG" \
    PATH="$TMP/fakemail:$PATH" bash "$ROOT/scripts/ops-audit.sh" >/dev/null 2>&1
) || rc=$?
if [ "$rc" = 0 ] && grep -q "AUDIT-OK" "$AUD_LOG" && [ ! -f "$MAIL_CAPTURE" ]; then
  ok "audit success quiet (rc=0, no alert sent)"
else
  bad "success path wrong: rc=$rc capture=$(cat "$MAIL_CAPTURE" 2>/dev/null)"
fi
HEAD_AFTER=$(git -C "$ROOT" rev-parse HEAD)
AUDIT_AFTER=$(ls "$ROOT/audit")
if [ "$HEAD_BEFORE" = "$HEAD_AFTER" ] && [ "$AUDIT_BEFORE" = "$AUDIT_AFTER" ]; then
  ok "test audits isolated from publish (HEAD + audit/ unchanged despite MSB_PUBLISH_AUDIT=1)"
else
  bad "publish leak: HEAD or audit/ changed during test audits"
fi

# ---------------------------------------------------------------------------
section "publish-audit: report written, dry-run touches no git"
PUBDIR="$TMP/pub"
rc=0
env MSB_AUDIT_DIR="$PUBDIR" MSB_AUDIT_SUMMARY="suite=pass ledger=pass license=valid" \
  bash "$ROOT/scripts/publish-audit.sh" --dry-run >/dev/null 2>&1 || rc=$?
REPORT="$PUBDIR/$(date '+%Y-%m-%d')_audit.md"
if [ "$rc" = 0 ] && [ -f "$REPORT" ] && grep -q "suite=pass" "$REPORT"; then
  ok "publish --dry-run wrote report (rc=0, no git)"
else
  bad "dry-run publish rc=$rc report=$REPORT"
fi

# ---------------------------------------------------------------------------
section "heartbeat: off-machine copy on volume, graceful skip without"
mkdir -p "$TMP/hb-vol"
rc=0
env MSB_HEARTBEAT_DIR="$TMP/hb-vol" MSB_HEARTBEAT_LOG="$TMP/hb.log" \
  bash "$ROOT/scripts/heartbeat.sh" >/dev/null 2>&1 || rc=$?
if [ "$rc" = 0 ] && [ -f "$TMP/hb-vol/msb-v3/heartbeat.log" ] \
  && [ -f "$TMP/hb-vol/msb-v3/snapshot-latest.md" ] \
  && [ -f "$TMP/hb-vol/msb-v3/audit/README.md" ]; then
  ok "heartbeat recorded liveness + snapshot + audit copy"
else
  bad "heartbeat rc=$rc files missing: $(ls "$TMP/hb-vol/msb-v3" 2>/dev/null)"
fi
rc=0
env MSB_HEARTBEAT_LOG="$TMP/hb2.log" bash "$ROOT/scripts/heartbeat.sh" >/dev/null 2>&1 || rc=$?
[ "$rc" = 0 ] && grep -q "no heartbeat sink" "$TMP/hb2.log" \
  && ok "heartbeat skips cleanly without a sink (rc=0)" \
  || bad "heartbeat no-sink rc=$rc log=$(cat "$TMP/hb2.log" 2>/dev/null)"

# ---------------------------------------------------------------------------
section "replicate: local mirror, graceful skip, loud unreachable"
mkdir -p "$TMP/rep-dst"
rc=0
env MSB_REPLICATION_TARGET="$TMP/rep-dst" MSB_REPLICATION_LOG="$TMP/rep.log" \
  bash "$ROOT/scripts/replicate-to-secondary.sh" >/dev/null 2>&1 || rc=$?
[ "$rc" = 0 ] && [ -f "$TMP/rep-dst/README.md" ] \
  && ok "replicated to local target (README.md mirrored)" \
  || bad "local replicate rc=$rc"
rc=0
env MSB_REPLICATION_LOG="$TMP/rep2.log" bash "$ROOT/scripts/replicate-to-secondary.sh" >/dev/null 2>&1 || rc=$?
[ "$rc" = 0 ] && grep -q "no replication target" "$TMP/rep2.log" \
  && ok "replicate skips cleanly without target (rc=0)" \
  || bad "replicate no-target rc=$rc"
rc=0
env MSB_REPLICATION_TARGET="nobody@127.0.0.1:/tmp/rep-nowhere" MSB_REPLICATION_LOG="$TMP/rep3.log" \
  bash "$ROOT/scripts/replicate-to-secondary.sh" >/dev/null 2>&1 || rc=$?
[ "$rc" != 0 ] && grep -q "FAIL" "$TMP/rep3.log" \
  && ok "unreachable secondary is loud (rc=$rc)" \
  || bad "unreachable replicate rc=$rc log=$(cat "$TMP/rep3.log" 2>/dev/null)"

# ---------------------------------------------------------------------------
echo
echo "=== result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
