"""Single-instance guard for scripts/notarize_chain_anchor.sh.

Root cause (root-caused 2026-10-11, evidence in
docs/supervisor-restart-war-2026-10-11.md): two launchd jobs ran the notarizer
on the same 07:10 schedule. Two concurrent notarize() runs both compute
``seq = len(local lines) + 1`` and produce sibling entries; the next run's
backfill — deduping the remote BY SEQ ONLY — re-pushed one (seq, ts) name
twice, and Google Drive keeps same-name siblings, so the object arrived as one
name resolving to TWO objects (read back as valid JSON pasted onto itself,
labelled "not JSON" — i.e. as tampering — until the verdict learned the
distinction). The lock is the root-cause choke point: with it, no two
notarizations can overlap, and a stuck lock degrades to a quiet skip.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "notarize_chain_anchor.sh"


def _run(script_body: str, env_extra: dict) -> subprocess.CompletedProcess:
    """Run a small bash harness that sources the guard lines from the real
    script (between its top and the notarize call) under a scratch env."""
    # Extract everything up to (but not including) the notarize invocation so
    # we exercise the REAL guard text, not a re-typed copy of it.
    text = SCRIPT.read_text()
    start = text.index('DB="${1:-')
    guard = text[start:text.index('# Local append must succeed')]
    harness = f"""{text.split('set -euo pipefail')[0]}
set -uo pipefail
REPO={str(REPO)!r}
{script_body}
{guard}
echo GUARD_PASSED
"""
    env = dict(os.environ)
    env.update(env_extra)
    env.setdefault("MSB_PYTHON", sys.executable)
    return subprocess.run(
        ["/bin/bash", "-c", harness], capture_output=True, text=True,
        env=env, timeout=60,
    )


def test_lock_free_acquires_and_releases(tmp_path):
    r = _run("", {"MSB_NOTARY_LOCK": str(tmp_path / "base")})
    assert "another notarization" not in r.stderr, r.stderr
    # Released on exit: the lock IS <MSB_NOTARY_LOCK>/notarize.lock — it and
    # its pid file must both be gone.
    assert not (tmp_path / "base" / "notarize.lock").exists(), \
        f"lock leaked: {list((tmp_path/'base'/'notarize.lock').iterdir())}"


def test_second_runner_exits_zero_and_does_not_touch_the_lock(tmp_path):
    # The guard's layout: LOCK_DIR/notarize.lock/pid
    lock = tmp_path / "base" / "notarize.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("999\n")
    r = _run("", {"MSB_NOTARY_LOCK": str(tmp_path / "base")})
    # Loud on stderr, exit 0 (skip, not failure), and the held lock survives
    # (it is not ours to remove).
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "another notarization holds the lock" in r.stderr
    assert (lock / "pid").read_text() == "999\n"


def test_stale_lock_from_killed_predecessor_does_not_wedge_forever(tmp_path):
    """A crashed predecessor leaves the dir behind (no trap ran). The second
    runner must skip cleanly — the operator removes the dir after checking
    the pid — rather than hang, fail, or deadlock the daily notary."""
    lock = tmp_path / "base" / "notarize.lock"
    lock.mkdir(parents=True)
    (lock / "pid").write_text("12345\n")
    r = _run("", {"MSB_NOTARY_LOCK": str(tmp_path / "base")})
    assert r.returncode == 0 and "holds the lock" in r.stderr
