#!/usr/bin/env bash
set -euo pipefail

# test-ops.sh — regression suite for the ops scripts: vault-backup,
# disk-health, cache-trim, backup-watchdog, rotate-logs, housekeeping,
# reclaim-port.
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
section "housekeeping: rotate-logs always, cache-trim+disk-health Sunday-only"
mkdir -p "$TMP/hk"
echo x > "$TMP/hk/dummy.log"
hk() {
  env MSB_HOUSEKEEPING_RUN_WEEKLY="$1" \
    MSB_HOUSEKEEPING_LOG="$TMP/hk/housekeeping.log" \
    MSB_ROTATE_TARGETS="$TMP/hk/dummy.log" MSB_ROTATE_LOG="$TMP/hk/rotate.log" \
    MSB_CACHE_DIRS="$TMP/hk/nocache" MSB_CACHE_LOG="$TMP/hk/cache.log" \
    MSB_DISK_STATE="$TMP/hk/disk-state" MSB_DISK_LOG="$TMP/hk/disk.log" \
    bash "$ROOT/scripts/housekeeping.sh"
}
rc=$(rc_of hk 0)
[ "$rc" = 0 ] && [ -f "$TMP/hk/rotate.log" ] \
  && ok "weekday: rotate-logs ran (rc=0)" || bad "weekday: rotate-logs did not run (rc=$rc)"
[ ! -f "$TMP/hk/cache.log" ] && ok "weekday: cache-trim skipped" || bad "weekday: cache-trim ran, should skip"
[ ! -f "$TMP/hk/disk.log" ] && ok "weekday: disk-health skipped" || bad "weekday: disk-health ran, should skip"
rc=$(rc_of hk 1)
[ "$rc" = 0 ] && ok "sunday: exits 0" || bad "sunday: run failed (rc=$rc)"
[ -f "$TMP/hk/cache.log" ] && ok "sunday: cache-trim ran" || bad "sunday: cache-trim did not run"
[ -f "$TMP/hk/disk.log" ] && ok "sunday: disk-health ran" || bad "sunday: disk-health did not run"

# ---------------------------------------------------------------------------
section "reclaim-port: clears an msb_v3 orphan, leaves unrelated processes alone"
RP_PORT=19917
RP_SCRIPT='
import http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self): self.send_response(200); self.end_headers()
    def log_message(self, *a): pass
s = http.server.HTTPServer(("127.0.0.1", '"$RP_PORT"'), H)
s.serve_forever()
'
rp() { # runs reclaim_stale_port with a test log() + MSB_PORT in a fresh shell
  MSB_PORT="$RP_PORT" bash -c '
    set -euo pipefail
    MSB_PORT="'"$RP_PORT"'"
    log() { echo "[reclaim-test] $*"; }
    . "'"$ROOT"'/scripts/lib/reclaim-port.sh"
    reclaim_stale_port
  '
}

# case 1: free port -> no-op, no crash under set -e/pipefail
rp >"$TMP/rp1.log" 2>&1
rc=$?
[ "$rc" = 0 ] && [ ! -s "$TMP/rp1.log" ] && ok "free port: no-op (rc=0, silent)" \
  || bad "free port: rc=$rc, output: $(cat "$TMP/rp1.log")"

# case 2: unrelated process holding the port -> warned, left alone.
# NB: spawned directly at this scope (not via a $(...) command-substitution
# helper) so `$!` names a real direct child of THIS shell -- a helper
# function called as `pid=$(fn)` runs in a subshell, and a job backgrounded
# inside it is orphaned the instant that subshell exits, making later
# `kill`/`wait` on it unreliable.
python3 -c "$RP_SCRIPT" >/dev/null 2>&1 &
other_pid=$!
sleep 0.3
rp >"$TMP/rp2.log" 2>&1 || true
sleep 0.2
if kill -0 "$other_pid" 2>/dev/null; then
  grep -q "not an msb-v3 process" "$TMP/rp2.log" \
    && ok "unrelated process: left alone + warned" \
    || bad "unrelated process: left alone but no warning logged"
else
  bad "unrelated process: was killed (should never touch a non-msb_v3 holder)"
fi
kill -9 "$other_pid" 2>/dev/null || true

# case 3: msb_v3-shaped orphan holding the port -> reclaimed. The extra
# "-m msb_v3" argv makes this process's command line contain "msb_v3", the
# same way a real `python -m msb_v3` process's would.
python3 -c "$RP_SCRIPT" -m msb_v3 >/dev/null 2>&1 &
orphan_pid=$!
sleep 0.3
rp >"$TMP/rp3.log" 2>&1 || true
sleep 0.3
if kill -0 "$orphan_pid" 2>/dev/null; then
  bad "msb_v3 orphan: still alive after reclaim"
  kill -9 "$orphan_pid" 2>/dev/null || true
else
  ok "msb_v3 orphan: reclaimed (port freed for the real supervisor)"
fi

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
section "supervisor lock: one supervisor only, standby takes over, stale lock cleared"
# Regression cover for the 2026-10-11 restart war: two supervisors ran at once
# and each reclaim-killed the other's child every ~14s for 29h (7268 restarts).
# Runs a scratch copy of the real run.sh so the live service on :8766 is never
# touched -- MSB_PORT points at a port nothing listens on, so reclaim is a
# no-op, and MSB_CONTAINER=1 skips the license gate.
LK="$TMP/lock/repo"
mkdir -p "$LK/scripts/lib" "$LK/data"
cp "$ROOT/scripts/run.sh" "$LK/scripts/run.sh"
cp "$ROOT/scripts/lib/reclaim-port.sh" "$LK/scripts/lib/"
: > "$LK/.env"
# Child exits on its own, so the loop cycles and a TERM lands on the shell's own
# sleep rather than on a long foreground child (bash runs a trap only after the
# foreground command returns).
printf '#!/bin/bash\nsleep 1\n' > "$TMP/lock/fakepy"
chmod +x "$TMP/lock/fakepy"
SUPLOCK="$TMP/lock/sup.lock"

lk_start() { # $1 = log file; echoes the supervisor pid
  env MSB_REPO="$LK" MSB_CONTAINER=1 MSB_PYTHON="$TMP/lock/fakepy" \
      MSB_PORT=19733 MSB_SUPERVISOR_LOCK="$SUPLOCK" \
      /bin/bash "$LK/scripts/run.sh" > "$1" 2>&1 &
  echo $!
}

A_PID="$(lk_start "$TMP/lock/a.log")"
sleep 3
B_PID="$(lk_start "$TMP/lock/b.log")"
sleep 3

# case 1: first supervisor owns the lock; the second stands by and never starts
# a child. Pre-fix behaviour was for both to run and fight.
LOCK_OWNER="$(cat "$SUPLOCK/pid" 2>/dev/null || echo "")"
if [ "$LOCK_OWNER" = "$A_PID" ] && grep -q "standing by for the lock" "$TMP/lock/b.log" \
  && ! grep -q "starting msb-v3" "$TMP/lock/b.log"; then
  ok "second supervisor stands by, starts no child (owner=$LOCK_OWNER)"
else
  bad "standby: owner=$LOCK_OWNER want=$A_PID b.log=$(tr '\n' ' ' < "$TMP/lock/b.log")"
fi

# case 2: the owner exits -> the standby takes the lock (real failover, not a
# deadlock).
kill -TERM "$A_PID" 2>/dev/null || true
took=""
for _ in $(seq 1 15); do
  sleep 1
  grep -q "supervisor lock held" "$TMP/lock/b.log" && { took="$B_PID"; break; }
done
if [ -n "$took" ] && [ "$(cat "$SUPLOCK/pid" 2>/dev/null || echo "")" = "$B_PID" ]; then
  ok "standby took over after the owner exited"
else
  bad "failover: b.log=$(tr '\n' ' ' < "$TMP/lock/b.log")"
fi

# case 3: a lock left behind by SIGKILL (no EXIT trap can run) must not block a
# fresh supervisor forever.
kill -9 "$B_PID" 2>/dev/null || true
sleep 1
DEAD_PID=999999
while kill -0 "$DEAD_PID" 2>/dev/null; do DEAD_PID=$((DEAD_PID + 1)); done
mkdir -p "$SUPLOCK"
echo "$DEAD_PID" > "$SUPLOCK/pid"
C_PID="$(lk_start "$TMP/lock/c.log")"
sleep 3
if grep -q "clearing stale supervisor lock" "$TMP/lock/c.log" \
  && grep -q "supervisor lock held" "$TMP/lock/c.log"; then
  ok "stale lock (dead owner $DEAD_PID) cleared and re-acquired"
else
  bad "stale lock: c.log=$(tr '\n' ' ' < "$TMP/lock/c.log")"
fi

# case 4: the lock does not outlive a supervisor that exits.
kill -TERM "$C_PID" 2>/dev/null || true
released=""
for _ in $(seq 1 15); do
  sleep 1
  [ ! -d "$SUPLOCK" ] && { released=1; break; }
done
[ -n "$released" ] && ok "lock released on supervisor exit" \
  || bad "lock still present at $SUPLOCK after exit"

kill -9 "$A_PID" "$B_PID" "$C_PID" 2>/dev/null || true

# ---------------------------------------------------------------------------
section "factory gate: kickstarts the launchd supervisor, never a rival tmux one"
# The start path is where the war originated: the gate spawned a second
# supervisor under tmux whenever its health probe missed. Stubs drive it so the
# real service, the real launchd job, the real log and OS notifications are all
# untouched; MSB_BASE_URL / MSB_GATE_LOG are the script's own overrides.
GT="$TMP/gate"
mkdir -p "$GT/bin" "$GT/repo/artifacts/hygiene"
GATE_CALLS="$GT/calls.log"
GATE_CURL_COUNT="$GT/curl.count"

cat > "$GT/bin/launchctl" <<'STUB'
#!/bin/bash
echo "launchctl $*" >> "${GATE_CALLS:-/dev/null}"
if [ "${1:-}" = print ]; then
  # launchctl's own contract: exit 0 means the job IS loaded.
  [ "${GATE_LAUNCHD_LOADED:-0}" = 1 ] && exit 0
  exit 1
fi
exit 0
STUB
printf '#!/bin/bash\necho "tmux $*" >> "${GATE_CALLS:-/dev/null}"\nexit 0\n' > "$GT/bin/tmux"
printf '#!/bin/bash\necho "osascript" >> "${GATE_CALLS:-/dev/null}"\nexit 0\n' > "$GT/bin/osascript"
# 'up' = healthy throughout; 'recover' = the two probes before the decision
# fail, then it answers -- models a service the gate just brought back.
cat > "$GT/bin/curl" <<'STUB'
#!/bin/bash
n=$(cat "$GATE_CURL_COUNT" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$GATE_CURL_COUNT"
[ "${GATE_HEALTH:-down}" = up ] && exit 0
[ "$n" -gt 2 ] && exit 0
exit 1
STUB
printf '#!/bin/bash\nexit 1\n' > "$GT/fakepy"   # self-test stub: fails fast
# Emulates run_factory's documented contract: exit 0 for --self-test, then
# write the gate artifact and exit 1 to signal a FAIL verdict (not a crash).
cat > "$GT/fakepy_verdict" <<'STUB'
#!/bin/bash
case "$*" in
  *--self-test*) exit 0 ;;
esac
mkdir -p "$GATE_REPO/artifacts/hygiene"
cat > "$GATE_REPO/artifacts/hygiene/factory_gate.json" <<'JSON'
{"RELEASE_VERDICT": {"release_verdict": "FAILED", "unresolved_unknowns": ["stub: pytest suite did not pass"]}}
JSON
exit 1
STUB
chmod +x "$GT/bin/launchctl" "$GT/bin/tmux" "$GT/bin/osascript" "$GT/bin/curl" \
  "$GT/fakepy" "$GT/fakepy_verdict"

run_gate() { # $1 = scenario, $2 = health (up|recover), $3 = launchd job loaded (0|1)
  #        $4 = python stub (default: one that fails the zero-spend self-test)
  local py="${4:-$GT/fakepy}"
  : > "$GATE_CALLS"; : > "$GATE_CURL_COUNT"
  env PATH="$GT/bin:$PATH" MSB_REPO="$GT/repo" MSB_PYTHON="$py" \
      FACTORY_RUNNER="$GT/factory.py" MSB_BASE_URL="http://127.0.0.1:1" \
      MSB_GATE_LOG="$GT/gate-$1.log" GATE_CALLS="$GATE_CALLS" \
      GATE_CURL_COUNT="$GATE_CURL_COUNT" GATE_HEALTH="$2" GATE_LAUNCHD_LOADED="$3" \
      GATE_REPO="$GT/repo" \
      /bin/bash "$ROOT/scripts/factory_gate_daily.sh" >/dev/null 2>&1 || true
}

# case 1: healthy -> the gate must not start or kickstart anything.
run_gate up up 0
if grep -q "server already up (2/2 probes)" "$GT/gate-up.log" \
  && ! grep -q "launchctl" "$GATE_CALLS" && ! grep -q "tmux" "$GATE_CALLS"; then
  ok "server healthy: gate started nothing (no launchctl, no tmux)"
else
  bad "healthy path: calls=$(tr '\n' ' ' < "$GATE_CALLS")"
fi

# case 2: down, launchd owns the job -> kickstart it, spawn nothing.
run_gate loaded recover 1
if grep -q "launchctl kickstart" "$GATE_CALLS" && ! grep -q "tmux" "$GATE_CALLS"; then
  ok "server down + launchd loaded: kickstarted, no rival tmux supervisor"
else
  bad "loaded path: calls=$(tr '\n' ' ' < "$GATE_CALLS")"
fi

# case 3: down and launchd does NOT own the job -> tmux is the only option left.
run_gate unloaded recover 0
if grep -q "tmux new-session" "$GATE_CALLS" && ! grep -q "kickstart" "$GATE_CALLS"; then
  ok "server down + launchd unloaded: falls back to tmux (no kickstart)"
else
  bad "unloaded path: calls=$(tr '\n' ' ' < "$GATE_CALLS")"
fi

# case 4: the factory exits 1, which per its own contract means FAIL -- a
# verdict, not a crash. Treating it as a crash (the behaviour until
# 2026-10-11) skipped both the verdict line and the daily_gate_events entry, so
# the daily record stopped dead on the first failing day.
GEVENTS="$GT/repo/artifacts/hygiene/daily_gate_events.jsonl"
rm -f "$GT/repo/artifacts/hygiene/factory_gate.json" "$GEVENTS"
run_gate failed up 1 "$GT/fakepy_verdict"
if grep -q "gate verdict=FAILED" "$GT/gate-failed.log" \
  && ! grep -q "crashed" "$GT/gate-failed.log" \
  && grep -q '"event": "gate_run"' "$GEVENTS" \
  && grep -q '"verdict": "FAILED"' "$GEVENTS"; then
  ok "factory exit 1 (= FAIL) recorded as a verdict, not a crash"
else
  bad "FAIL-verdict path: log=$(tr '\n' ' ' < "$GT/gate-failed.log") events=$(cat "$GEVENTS" 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
section "supervisor-detect: sees relative invocations, filters by cwd"
# start.sh used `pgrep -f 'msb-v3/scripts/run.sh'` — absolute-path only — so a
# supervisor started as `bash scripts/run.sh` read as "not running". The shared
# detector must see it AND must not be fooled by an unrelated checkout running
# an identical command line.
for d in "$TMP/sd/repo-a" "$TMP/sd/repo-b"; do
  mkdir -p "$d/scripts/lib" "$d/data"
  cp "$ROOT/scripts/run.sh" "$d/scripts/run.sh"
  cp "$ROOT/scripts/lib/reclaim-port.sh" "$d/scripts/lib/"
  cp "$ROOT/scripts/lib/supervisor-detect.sh" "$d/scripts/lib/"
  : > "$d/.env"
done
printf '#!/bin/bash\nsleep 1\n' > "$TMP/sd/fakepy"; chmod +x "$TMP/sd/fakepy"

sd_ask() { # $1 = checkout to ask about; prints running|not-running
  REPO="$1" bash -c "
    set -uo pipefail
    . '$ROOT/scripts/lib/supervisor-detect.sh'
    supervisor_running && echo running || echo not-running
  "
}

sd_spawn() { # $1 = checkout, $2 = port, $3 = lock; starts a RELATIVE run.sh there
  (
    cd "$1" || exit 1
    env MSB_REPO="$1" MSB_CONTAINER=1 MSB_PYTHON="$TMP/sd/fakepy" \
        MSB_PORT="$2" MSB_SUPERVISOR_LOCK="$3" \
        /bin/bash scripts/run.sh > "$1/../$( basename "$1" ).log" 2>&1 &
    echo $! > "$1/../$( basename "$1" ).pid"
  )
}

SD_A="$TMP/sd/repo-a"
SD_B="$TMP/sd/repo-b"

# case 1: nothing (of ours) running -> not-running, and no crash under set -u
[ "$(sd_ask "$SD_A")" = "not-running" ] \
  && ok "no supervisor for this checkout: reports not-running" \
  || bad "empty case: got $(sd_ask "$SD_A")"

# case 2: repo-a started via a RELATIVE path -> detected (the old needle missed it)
sd_spawn "$SD_A" 19735 "$TMP/sd/lock-a"
sleep 3
A_PID="$(cat "$TMP/sd/repo-a.pid" 2>/dev/null || echo "")"
[ "$(sd_ask "$SD_A")" = "running" ] \
  && ok "relative 'bash scripts/run.sh' of repo-a detected (old check missed this)" \
  || bad "relative invocation undetected: got $(sd_ask "$SD_A") log=$(tr '\n' ' ' < "$TMP/sd/repo-a.log" 2>/dev/null)"

# case 3: an unrelated checkout running the same command line must not leak in.
sd_spawn "$SD_B" 19736 "$TMP/sd/lock-b"
sleep 3
B_PID="$(cat "$TMP/sd/repo-b.pid" 2>/dev/null || echo "")"
[ "$(sd_ask "$SD_B")" = "running" ] \
  && ok "repo-b's own supervisor detected for repo-b" \
  || bad "repo-b undetected: got $(sd_ask "$SD_B")"
kill -9 "$B_PID" 2>/dev/null || true
sleep 2
# repo-a must still be seen after repo-b exits: proves the cwd filter is
# per-checkout, not merely "some run.sh exists somewhere".
[ "$(sd_ask "$SD_A")" = "running" ] \
  && ok "repo-a still detected after repo-b exits (cwd filter is per-checkout)" \
  || bad "repo-a lost detection: got $(sd_ask "$SD_A")"
kill -9 "$A_PID" 2>/dev/null || true
sleep 2
[ "$(sd_ask "$SD_A")" = "not-running" ] \
  && ok "after exit: reports not-running again" \
  || bad "still running after kill: got $(sd_ask "$SD_A")"

# ---------------------------------------------------------------------------
section "H9 schema check: absent data dir is not drift, a v0 DB is"
# Regression cover for origin/main's permanently red ops suite. `data/` is
# gitignored, so a fresh clone has none; the check exited 1 on the missing dir
# and ops-audit's H9 step reported that as "unstamped SQLite DB" when there
# were no DBs at all. Absent and empty now mean the same thing (0 DBs), while
# a real unstamped DB still fails.
SSC="$ROOT/scripts/stamp-schemas.py"

rc=0
"${MSB_PYTHON:-python3}" "$SSC" --check --data-dir "$TMP/ss-absent" >"$TMP/ss1.out" 2>&1 || rc=$?
[ "$rc" = 0 ] && grep -q "0 DB(s)" "$TMP/ss1.out" \
  && ok "absent data dir -> rc=0, 0 DB(s) (was rc=1, misreported as drift)" \
  || bad "absent data dir rc=$rc out=$(tr '\n' ' ' < "$TMP/ss1.out")"

mkdir -p "$TMP/ss-empty"
rc=0
"${MSB_PYTHON:-python3}" "$SSC" --check --data-dir "$TMP/ss-empty" >"$TMP/ss2.out" 2>&1 || rc=$?
[ "$rc" = 0 ] \
  && ok "empty data dir -> rc=0 (same verdict as absent, as it must be)" \
  || bad "empty data dir rc=$rc out=$(tr '\n' ' ' < "$TMP/ss2.out")"

mkdir -p "$TMP/ss-drift"
"${MSB_PYTHON:-python3}" -c "import sqlite3,sys; sqlite3.connect(sys.argv[1]).close()" "$TMP/ss-drift/old.db"
rc=0
"${MSB_PYTHON:-python3}" "$SSC" --check --data-dir "$TMP/ss-drift" >"$TMP/ss3.out" 2>&1 || rc=$?
[ "$rc" != 0 ] && grep -q "unstamped" "$TMP/ss3.out" \
  && ok "a genuinely unstamped DB still fails rc=$rc (the signal is intact)" \
  || bad "real drift not caught: rc=$rc out=$(tr '\n' ' ' < "$TMP/ss3.out")"

# ---------------------------------------------------------------------------
echo
echo "=== result: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] || exit 1
