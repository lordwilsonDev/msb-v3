#!/usr/bin/env bash
set -uo pipefail

# harness-evidence -- gate the video-harness evidence dir on FRESH PASS
# verdicts for the main experiments.
#
# The evidence dir accumulates BOTH main experiments (p0_basic, p1_ffmpeg,
# p2_inference -- the `make run/run-p1/run-p2` baseline experiments) AND
# test-generated runs (t_*, p0_drop, p1_degraded, ...) whose FAIL/DEGRADE
# verdicts are EXPECTED and correct. A naive "latest run is PASS" check
# would therefore flake on test evidence.
#
# Instead, for each configured experiment, the LATEST evidence.json is found
# (dirs are <experiment_id>_<timestamp>, timestamp in UTC sortable form) and
# must have:
#   - verdict == "PASS"
#   - timestamp within HARNESS_MAX_AGE_H hours of now (default 24)
#
# Designed as a `custom` stage for webcheck-all, or as part of the built-in
# `harness` stage which runs this producer and then ci-harness-gate.sh on the
# report in one command:
#   make webcheck-all STAGES=endpoints,custom \
#     CUSTOM_CMD='bash scripts/harness-evidence.sh'
#   make webcheck-all STAGES=endpoints,harness
#
# Env:
#   HARNESS_DIR          video-harness root (default ~/video-harness)
#   HARNESS_EXPERIMENTS  comma-separated experiment ids to require fresh PASS
#                        (default p0_basic,p1_ffmpeg,p2_inference)
#   HARNESS_MAX_AGE_H    freshness window in hours (default 24)
#   HARNESS_STRICT       set to 1 to ALSO fail when ANY older run of a
#                        configured experiment is stale (default 0 -- only
#                        the newest run per experiment is judged)
#   HARNESS_SUMMARY      set to 0 to skip the all-evidence summary table
#                        (default 1)
#   HARNESS_SUMMARY_MAX  cap summary table rows, 0 = all (default 0)
#   HARNESS_REPORT_FILE  write a machine-readable JSON report (schema
#                        harness-evidence-report/v1) to this path, for CI
#                        consumers (default: unset = no report)
#
# After the gate, a summary of ALL evidence runs (newest first: run dir,
# verdict, age / STALE flag) is printed so stale/failed test runs stay
# visible alongside the gate. It is informational only -- the exit code is
# decided solely by the configured experiments.
#
# When HARNESS_REPORT_FILE is set, a JSON report is written containing the
# per-experiment gate results (including strict findings) and the full
# all-evidence table. The gate, table, and report are computed by ONE python
# pass so the artifact can never disagree with the human verdict. A report
# write failure fails the run (exit 2).
#
# Note: the whole output block (gate lines + table) is routed to stdout when
# the gate passes and stderr when it fails -- identical in merged CI logs,
# but consumers splitting streams should read both.
#
# Exit: 0 = all configured experiments have fresh PASS evidence (strict:
#        and no stale older run); 1 = any missing / stale / non-PASS;
#        2 = env/config error or report write failure.

PY="/opt/homebrew/Caskroom/miniforge/base/bin/python"
HARNESS_DIR="${HARNESS_DIR:-$HOME/video-harness}"
EVIDENCE_DIR="$HARNESS_DIR/evidence"
EXPERIMENTS="${HARNESS_EXPERIMENTS:-p0_basic,p1_ffmpeg,p2_inference}"
MAX_AGE_H="${HARNESS_MAX_AGE_H:-24}"
STRICT="${HARNESS_STRICT:-0}"

if [ ! -d "$EVIDENCE_DIR" ]; then
  echo "[harness-evidence] ERROR: evidence dir not found: $EVIDENCE_DIR (set HARNESS_DIR)" >&2
  exit 2
fi

status=0
count=0
IFS=',' read -r -a _exps <<< "$EXPERIMENTS"
for exp in "${_exps[@]}"; do
  [ -n "$exp" ] || continue
  count=$((count + 1))
done
if [ "$count" -eq 0 ]; then
  echo "[harness-evidence] ERROR: HARNESS_EXPERIMENTS produced no entries ('$EXPERIMENTS')" >&2
  exit 2
fi

# One authoritative python pass: per-experiment gate lines, all-evidence
# table, and (when requested) the machine-readable JSON report. All lines
# are emitted with the [harness-evidence] prefix; the shell routes the whole
# block to stdout on rc 0, stderr otherwise, and propagates rc 2.
out=$("$PY" - "$EVIDENCE_DIR" "$EXPERIMENTS" "$MAX_AGE_H" "$STRICT" "${HARNESS_SUMMARY:-1}" "${HARNESS_SUMMARY_MAX:-0}" "${HARNESS_REPORT_FILE:-}" <<'PYEOF' 2>&1
import json
import os
import sys
from datetime import datetime, timezone

try:
    evidence_dir = sys.argv[1]
    experiments = [e for e in sys.argv[2].split(",") if e]
    max_age_h = float(sys.argv[3])
    strict = sys.argv[4] != "0"
    show_summary = sys.argv[5] != "0"
    max_rows = int(sys.argv[6]) if len(sys.argv) > 6 else 0
    report_file = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else None
except (ValueError, IndexError) as e:
    print(f"[harness-evidence] bad args: {e}", file=sys.stderr)
    sys.exit(2)

now = datetime.now(timezone.utc)

try:
    all_dirs = sorted(os.listdir(evidence_dir))
except OSError as e:
    print(f"[harness-evidence] cannot list evidence dir {evidence_dir}: {e}", file=sys.stderr)
    sys.exit(2)


def _ts(value):
    # e.g. "20260811T003158Z" -> aware UTC datetime (3.11+ handles trailing Z)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _load(exp, name, fallback_ts=False):
    # Returns (evidence_dict_or_None, ts_or_None, exception_or_None).
    # With fallback_ts, an unreadable run is judged by its dirname timestamp
    # (the same fallback the newest-run check uses); the strict scan uses it
    # so a corrupt older run is still judged rather than silently skipped.
    d = None
    try:
        with open(os.path.join(evidence_dir, name, "evidence.json")) as fh:
            d = json.load(fh)
        return d, _ts(d.get("timestamp", name[len(exp) + 1:])), None
    except Exception as e:
        if fallback_ts:
            return None, _ts(name[len(exp) + 1:]), e
        return None, None, e


# --- per-experiment gate (also feeds the JSON report) ---
# Dirs are <experiment_id>_<timestamp>; lexicographic order == chronological.
# Only the NEWEST matching dir is judged for verdict+freshness: if its
# evidence.json is missing or unreadable that is a hard FAIL, never a silent
# fallback to older runs (which could mask a broken latest run with a stale
# PASS).
fail_count = 0
gate_rows = []
for exp in experiments:
    prefix = exp + "_"
    matching = sorted(n for n in all_dirs if n.startswith(prefix))
    row = {"experiment": exp, "status": "FAIL", "latest_run": None,
           "verdict": None, "age_h": None, "message": "", "strict_stale": []}
    if not matching:
        msg = f"{exp}: NO EVIDENCE (no '{prefix}*' dirs)"
        print(f"[harness-evidence] {msg}")
        row["message"] = msg
        fail_count += 1
        gate_rows.append(row)
        continue
    name = matching[-1]
    d, ts, err = _load(exp, name)
    if d is None:
        msg = f"{exp}: latest run {name} UNREADABLE evidence.json ({type(err).__name__}: {err})"
        print(f"[harness-evidence] {msg}")
        row.update(latest_run=name, message=msg)
        fail_count += 1
        gate_rows.append(row)
        continue
    if ts is None:
        msg = f"{exp}: latest run {name} bad timestamp '{d.get('timestamp')}' ({err})"
        print(f"[harness-evidence] {msg}")
        row.update(latest_run=name, message=msg)
        fail_count += 1
        gate_rows.append(row)
        continue
    verdict = d.get("verdict", "?")
    age_h = (now - ts).total_seconds() / 3600.0
    row.update(latest_run=name, verdict=verdict, age_h=round(age_h, 2))
    if verdict != "PASS":
        msg = f"{exp}: latest run {name} verdict={verdict} (expected PASS)"
        print(f"[harness-evidence] {msg}")
        row["message"] = msg
        fail_count += 1
    elif age_h > max_age_h:
        msg = f"{exp}: latest run {name} PASS but STALE ({age_h:.1f}h > {max_age_h:g}h)"
        print(f"[harness-evidence] {msg}")
        row["message"] = msg
        fail_count += 1
    else:
        # STRICT mode: older runs of this experiment must not be stale either.
        stale = []
        for older in matching[:-1]:
            od, ots, _ = _load(exp, older, fallback_ts=True)
            if ots is None:
                continue
            oage = (now - ots).total_seconds() / 3600.0
            if oage > max_age_h:
                stale.append({"run_dir": older, "verdict": (od or {}).get("verdict", "?"),
                              "age_h": round(oage, 2)})
        if strict and stale:
            for s in stale:
                msg = f"{exp}: STRICT stale run {s['run_dir']} verdict={s['verdict']} age={s['age_h']:.1f}h > {max_age_h:g}h"
                print(f"[harness-evidence] {msg}")
            row.update(strict_stale=stale, message=f"STRICT: {len(stale)} stale older run(s)")
            fail_count += 1
        else:
            msg = f"{exp}: latest run {name} verdict=PASS age={age_h:.1f}h (ok)"
            print(f"[harness-evidence] {msg}")
            row.update(status="PASS", strict_stale=stale, message=msg)
    gate_rows.append(row)

# --- all-evidence table + structured rows (newest first) ---
ev_rows = []
for name in reversed(all_dirs):
    base = {"run_dir": name, "experiment": name.rsplit("_", 1)[0],
            "verdict": None, "age_h": None, "issue": None, "stale": None, "flag": ""}
    path = os.path.join(evidence_dir, name, "evidence.json")
    if not os.path.isfile(path):
        base.update(issue="MISSING", flag="MISSING evidence.json")
        ev_rows.append(base)
        continue
    try:
        with open(path) as fh:
            d = json.load(fh)
    except Exception as e:
        base.update(issue="UNREADABLE", flag=f"UNREADABLE ({type(e).__name__})")
        ev_rows.append(base)
        continue
    verdict = d.get("verdict", "?")
    ts = _ts(d.get("timestamp", ""))
    if ts is None:
        base.update(verdict=verdict, issue="NO_TIMESTAMP", flag="no timestamp")
        ev_rows.append(base)
        continue
    age_h = (now - ts).total_seconds() / 3600.0
    stale_flag = age_h > max_age_h
    flag = f"STALE {age_h:.1f}h" if stale_flag else f"{age_h:.1f}h"
    base.update(verdict=verdict, age_h=round(age_h, 2), stale=stale_flag, flag=flag)
    ev_rows.append(base)

if show_summary:
    rows = ev_rows if max_rows <= 0 else ev_rows[:max_rows]
    width = max((len(r["run_dir"]) for r in rows), default=0)
    print()
    print("[harness-evidence] ALL EVIDENCE (newest first; STALE is informational unless HARNESS_STRICT=1, then any stale run of a configured experiment fails the gate):")
    print(f"[harness-evidence] {'run dir':<{width}}  verdict    age")
    print(f"[harness-evidence] {'-------':<{width}}  -------    ---")
    for r in rows:
        v = r["verdict"] if r["verdict"] is not None else "?"
        print(f"[harness-evidence] {r['run_dir']:<{width}}  {str(v):<9}  {r['flag']}")

# --- machine-readable report for CI consumers ---
if report_file:
    report = {
        "schema": "harness-evidence-report/v1",
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence_dir": evidence_dir,
        "config": {"experiments": experiments, "max_age_h": max_age_h,
                   "strict": strict, "max_rows": max_rows},
        "gate": {"verdict": "PASS" if fail_count == 0 else "FAIL",
                 "experiments": gate_rows},
        "all_evidence": ev_rows,
        "summary": {"runs": len(ev_rows),
                    "stale": sum(1 for r in ev_rows if r["stale"]),
                    "missing": sum(1 for r in ev_rows if r["issue"] == "MISSING"),
                    "unreadable": sum(1 for r in ev_rows if r["issue"] == "UNREADABLE"),
                    "no_timestamp": sum(1 for r in ev_rows if r["issue"] == "NO_TIMESTAMP")},
    }
    try:
        with open(report_file, "w") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
        print(f"[harness-evidence] report written: {report_file}")
    except Exception as e:
        print(f"[harness-evidence] ERROR writing report {report_file} ({type(e).__name__}: {e})", file=sys.stderr)
        sys.exit(2)

sys.exit(1 if fail_count else 0)
PYEOF
)
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "$out"
else
  echo "$out" >&2
  [ "$rc" -eq 2 ] && status=2 || status=1
fi

echo "[harness-evidence] evidence dir: $EVIDENCE_DIR (max age ${MAX_AGE_H}h)"
if [ "$status" -eq 0 ]; then
  echo "[harness-evidence] PASS: all $count experiment(s) have fresh PASS evidence"
  exit 0
elif [ "$status" -eq 2 ]; then
  echo "[harness-evidence] FAIL: env/config error (see above)" >&2
  exit 2
fi
echo "[harness-evidence] FAIL: at least one experiment lacks fresh PASS evidence" >&2
exit 1
