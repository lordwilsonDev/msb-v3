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
echo -e "\truns = $n"
if [ -f "$FAKE_FAIL" ]; then echo -e "\tlast exit code = 1"; else echo -e "\tlast exit code = 0"; fi
EOF
chmod +x "$TMP/fakebin/launchctl"
wd() { # runs fail-file state log -> exit code
  echo "$1" > "$TMP/runs"
  if [ "$2" = fail ]; then : > "$TMP/fail"; else rm -f "$TMP/fail"; fi
  rc_of env PATH="$TMP/fakebin:$PATH" FAKE_RUNS="$TMP/runs" FAKE_FAIL="$TMP/fail" \
    MSB_WATCHDOG_STATE="$3" MSB_WATCHDOG_LOG="$4" \
    MSB_WATCHDOG_AGENTS="com.lordwilson.fake|fake agent|fake.err" \
    bash "$ROOT/scripts/backup-watchdog.sh"
}
W="$TMP/wd-state"
WLOG="$TMP/wd.log"
wd 1 fail "$W" "$WLOG" >/dev/null     # new failed run -> alert
a1=$(grep -c ALERT "$WLOG" || true)
wd 1 fail "$W" "$WLOG" >/dev/null     # same run, no new -> no alert
a2=$(grep -c ALERT "$WLOG" || true)
wd 2 fail "$W" "$WLOG" >/dev/null     # new failed run, already alerted -> no alert
a3=$(grep -c ALERT "$WLOG" || true)
wd 3 ok "$W" "$WLOG" >/dev/null       # success -> episode clears
wd 4 fail "$W" "$WLOG" >/dev/null     # new failure after recovery -> alert again
a4=$(grep -c ALERT "$WLOG" || true)
[ "$a1" = 1 ] && [ "$a2" = 1 ] && [ "$a3" = 1 ] && [ "$a4" = 2 ] \
  && ok "alert state machine correct (1,1,1,2)" \
  || bad "alert counts wrong: $a1,$a2,$a3,$a4"

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
# missing license -> exit 2
MSB_LICENSE_KEY="$LK" MSB_LICENSE_AUTHORIZED="$LA" bash "$ROOT/scripts/verify-license.sh" "$TMP/lic/none" >/dev/null 2>&1; rc=$?
[ "$rc" = 2 ] && ok "missing license -> exit 2" || bad "missing license rc=$rc (want 2)"

# ---------------------------------------------------------------------------
echo
echo "=== result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
