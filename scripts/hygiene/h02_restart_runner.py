#!/usr/bin/env python3
"""H02 Restart hygiene — REAL process kill + supervised restart experiment.

This replaces the previously-blocked filesystem-only probe. The msb-v3
server runs under a supervisor loop (scripts/run.sh: on any non-zero exit,
sleep 2s, restart), typically hosted in a tmux session. That supervisor is
the *real* restart mechanism, so the experiment is:

  1. Register a probe truth entity through the live API and capture its
     full state (including the stored checksum).
  2. SIGKILL the process actually listening on the API port — a hard crash:
     no graceful shutdown, no SIGTERM handler, exit code 137.
  3. The supervisor restarts the server; poll /health until it returns 200.
  4. Retrieve the probe entity again and compare the full state.

Verdict = pass iff the probe entity is identical (same data + same stored
checksum) after a genuine process crash + supervised restart. That is what
"MSB survives a restart" actually means: API-visible state re-initialized
from disk by a fresh process, not a filesystem round-trip in one process.

Safety: the runner refuses to SIGKILL the server unless a run.sh supervisor
process is present, so it can never leave the server down.

Standalone counterpart to the shared hygiene_runner's `h02_restart`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REPO = Path(os.environ.get('MSB_REPO', Path(__file__).resolve().parents[2]))
EVIDENCE_DIR = REPO / 'artifacts' / 'hygiene'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
dotenv = REPO / '.env'
env: dict[str, str] = {}
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
SECRET = env.get('MCP_BRIDGE_SECRET', os.environ.get('MCP_BRIDGE_SECRET', ''))
BASE_URL = env.get('MSB_BASE_URL', os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766'))
PORT = urlparse(BASE_URL).port or 8766

POLL_INTERVAL_S = 1.0
RESTART_TIMEOUT_S = 90.0


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h02_restart_hygiene',
        'skill': 'state-hygiene',
        'input': (
            'real process crash + supervised restart: register probe entity '
            f'via API, SIGKILL listener on :{PORT}, wait for supervisor '
            '(scripts/run.sh) to respawn, verify entity survived via API'
        ),
        'environment': BASE_URL,
        'failure_injected': (
            f'SIGKILL (exit 137) of the process listening on :{PORT} — a hard '
            'crash with no graceful shutdown path'
        ),
        'expected_behavior': (
            'supervisor restarts the server within the timeout; probe truth '
            'entity remains retrievable with byte-identical data + checksum'
        ),
        'actual_behavior': '',
        'latency_ms': 0,
        'errors': [],
        'state_before': {},
        'state_after': {},
        'recovery': '',
        'false_repair': False,
        'evidence': [],
        'verdict': 'unknown',
    }


def save(record: dict[str, Any]) -> Path:
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = EVIDENCE_DIR / f"{record['experiment_id']}_{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding='utf-8')
    return path


def http_request(method: str, path: str, payload: dict | None = None) -> tuple[int, Any]:
    """Return (status_code, parsed_json_or_raw). 0 status = transport failure."""
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(
        f'{BASE_URL}{path}',
        data=data,
        headers={
            'x-mcp-secret': SECRET,
            'content-type': 'application/json',
            'accept': 'application/json',
        },
        method=method,
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8', errors='ignore'))
    except HTTPError as e:
        try:
            body = json.loads(e.read().decode('utf-8', errors='ignore'))
        except Exception:
            body = {}
        return e.code, body
    except Exception as e:
        return 0, str(e)


def health_ok() -> bool:
    code, _ = http_request('GET', '/health')
    return code == 200


def registry_checksum(data: dict[str, Any]) -> str:
    content = {k: v for k, v in data.items() if k != 'checksum'}
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def listener_pids() -> list[int]:
    """PIDs of processes listening on the API port (lsof on macOS/Linux)."""
    try:
        out = subprocess.run(
            ['lsof', '-t', f'-iTCP:{PORT}', '-sTCP:LISTEN'],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return []
    return [int(p) for p in out.splitlines() if p.strip().isdigit()]


def supervisor_present() -> bool:
    """True if THIS repo's run.sh supervisor loop is running.

    Guarantees the SIGKILLed listener will be respawned. Two checks:
      1. a process whose command line matches `scripts/run.sh` exists
         (the supervisor is launched as `bash scripts/run.sh` from the repo
         root — a RELATIVE path, so the absolute path must not be the
         pgrep needle), and
      2. that process's working directory is THIS repo (via lsof cwd), so
         an unrelated run.sh in another directory cannot satisfy the guard
         while the killed process is actually unsupervised.
    """
    try:
        out = subprocess.run(
            ['pgrep', '-f', 'scripts/run.sh'],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return False
    pids = [p for p in out.splitlines() if p.strip().isdigit()]
    if not pids:
        return False
    for pid in pids:
        try:
            cwd_out = subprocess.run(
                ['lsof', '-a', '-p', pid, '-d', 'cwd', '-Fn'],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in cwd_out.splitlines():
                if line.startswith('n') and Path(line[1:]).resolve() == REPO.resolve():
                    return True
        except Exception:
            continue
    return False


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    probe_id = f'h02_restart_probe_{int(time.time())}'
    probe_payload = {
        'id': probe_id,
        'claim': 'restart-survival probe',
        'sequence': 42,
        'committed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    killed_pids: list[int] = []
    restart_ms: int | None = None

    try:
        # --- Preconditions -------------------------------------------------
        if not health_ok():
            record['verdict'] = 'fail'
            record['errors'].append(
                f'server not reachable at {BASE_URL}; cannot run restart experiment'
            )
            record['actual_behavior'] = 'precondition failed: server down before probe'
            raise SystemExit(0 if record['verdict'] == 'pass' else 1)

        pids_before = listener_pids()
        if not pids_before:
            record['verdict'] = 'fail'
            record['errors'].append(f'no listener found on :{PORT} despite healthy /health')
            raise SystemExit(1)
        record['state_before'] = {'listener_pids': pids_before, 'health': 200}

        if not supervisor_present():
            record['verdict'] = 'fail'
            record['errors'].append(
                'refusing to SIGKILL the server: no scripts/run.sh supervisor '
                'process found, so nothing would restart it (safety guard)'
            )
            record['actual_behavior'] = 'aborted before kill — no supervisor present'
            raise SystemExit(1)

        # --- Step 1: register probe + capture full state -------------------
        reg_code, reg_body = http_request('POST', '/business/register', probe_payload)
        if reg_code != 200:
            record['verdict'] = 'fail'
            record['errors'].append(
                f'probe registration failed: HTTP {reg_code} ({reg_body})'
            )
            raise SystemExit(1)
        ret_code, ret_body = http_request('GET', f'/business/retrieve/{probe_id}')
        if ret_code != 200:
            record['verdict'] = 'fail'
            record['errors'].append(f'probe pre-kill retrieve failed: HTTP {ret_code}')
            raise SystemExit(1)
        state_before = ret_body['data']
        record['evidence'].append(
            f'registered {probe_id}; pre-kill checksum={state_before["checksum"][:8]}'
        )

        # --- Step 2: SIGKILL the listener (hard crash) ----------------------
        for pid in pids_before:
            try:
                os.kill(pid, 9)
                killed_pids.append(pid)
            except ProcessLookupError:
                pass
        record['evidence'].append(f'SIGKILL sent to PIDs {killed_pids}')
        time.sleep(POLL_INTERVAL_S)

        # --- Step 3: wait for supervised restart -----------------------------
        restart_started = time.monotonic()
        recovered = False
        while time.monotonic() - restart_started < RESTART_TIMEOUT_S:
            if health_ok():
                recovered = True
                break
            time.sleep(POLL_INTERVAL_S)
        restart_ms = int((time.monotonic() - restart_started) * 1000)
        record['evidence'].append(
            f'health recovered={recovered} after {restart_ms}ms (supervisor restart)'
        )

        # --- Step 4: verify API-visible state survived ----------------------
        post_code, post_body = http_request('GET', f'/business/retrieve/{probe_id}')
        state_after: dict[str, Any] = {}
        identical = False
        if post_code == 200:
            state_after = post_body['data']
            identical = state_before == state_after
            recomputed = registry_checksum(state_after)
            checksum_ok = recomputed == state_after.get('checksum')
            record['state_after'] = {
                'identical': identical,
                'checksum_ok': checksum_ok,
                'stored_checksum': state_after.get('checksum'),
                'recomputed_checksum': recomputed,
                'retrieve_status_after_restart': post_code,
            }
            record['evidence'].append(
                f'post-restart retrieve: HTTP {post_code}; identical={identical}; '
                f'checksum_ok={checksum_ok}'
            )
        else:
            record['state_after'] = {'retrieve_status_after_restart': post_code}
            record['errors'].append(
                f'probe entity NOT retrievable after restart: HTTP {post_code}'
            )

        record['actual_behavior'] = (
            f'killed_pids={killed_pids}; health_recovered={recovered} in '
            f'{restart_ms}ms; probe_identical={identical}; '
            f'post_restart_retrieve={post_code}'
        )

        if recovered and post_code == 200 and identical:
            record['verdict'] = 'pass'
            record['recovery'] = (
                'server survived a real SIGKILL + supervised restart; '
                'probe entity re-hydrated from disk byte-identically '
                '(same data, same stored checksum)'
            )
        else:
            record['verdict'] = 'fail'
            record['recovery'] = 'restart did not preserve API-visible state (see errors)'
    except SystemExit:
        raise
    except Exception as e:  # defensive
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )
        # Best-effort cleanup: purge the probe entity. Retry across the whole
        # restart budget — a slow supervisor restart (up to RESTART_TIMEOUT_S)
        # can outlast a short retry window and leave the probe file on disk.
        # A leftover is harmless (timestamped id, no collision on future runs),
        # but we still try to clean it for the whole restart budget.
        deadline = time.monotonic() + RESTART_TIMEOUT_S
        while time.monotonic() < deadline:
            code, _ = http_request('DELETE', f'/business/purge/{probe_id}')
            if code in (200, 404):
                break
            time.sleep(1.0)

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'killed_pids': killed_pids,
        'restart_ms': restart_ms,
        'probe_identical': record['state_after'].get('identical'),
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
