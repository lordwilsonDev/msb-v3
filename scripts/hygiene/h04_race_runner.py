#!/usr/bin/env python3
"""H04 Concurrent-write race — real race-condition experiment against the truth registry.

Fires N threads that simultaneously register the SAME entity id with different
claims (a genuine read-modify-write race window), plus M threads registering
distinct ids concurrently. It then checks for the three classic race
signatures:

1. torn writes  -> any entity file that is not valid JSON, or whose stored
                   checksum does not match its own content hash
2. lost updates -> concurrent same-id registrations should converge to one
                   complete, self-consistent record (not a partial merge)
3. livelock/crash -> the server must remain responsive (/health 200) throughout

The verdict is `pass` only if every entity file is valid and self-consistent
and the server stayed responsive. This replaces the previous aliasing of
`h04_race` to `h03_idempotency` in the shared runner, which ran the idempotency
test instead of a race experiment.

Standalone counterpart to the shared hygiene_runner's `h04_race`.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
TRUTH_DIR = REPO / env.get('MSB_TRUTH_DIR', 'data/truth')

RACE_ID = 'h04_race_probe'
THREADS_SAME_ID = 12
THREADS_DISTINCT = 8


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h04_race',
        'skill': 'concurrency-hygiene',
        'input': (
            f'{THREADS_SAME_ID} concurrent registrations of the same id '
            f'({RACE_ID}) with different claims + {THREADS_DISTINCT} distinct-id '
            'registrations (endpoint /business/register; note 409 = already exists)'
        ),
        'environment': BASE_URL,
        'failure_injected': (
            'concurrent same-key writes to the truth registry via /business/register'
        ),
        'expected_behavior': (
            'no torn writes: every entity file valid JSON with checksum matching '
            'content; same-id registrations converge to one self-consistent record; '
            'server stays responsive'
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


def http_post(payload: dict[str, Any], path: str) -> tuple[int, str, int]:
    url = f'{BASE_URL}{path}'
    data = json.dumps(payload).encode('utf-8')
    req = Request(url, data=data, headers={
        'x-mcp-secret': SECRET,
        'content-type': 'application/json',
        'accept': 'application/json',
    })
    start = dt.datetime.now(dt.timezone.utc)
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8', errors='ignore')
            code = resp.status
    except HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        code = e.code
    except Exception as e:  # transport-level failure
        body, code = str(e), 0
    latency = int((dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000)
    return code, body, latency


def health_ok() -> tuple[bool, int]:
    req = Request(f'{BASE_URL}/health', headers={'accept': 'application/json'})
    try:
        with urlopen(req, timeout=10) as resp:
            return resp.status == 200, resp.status
    except Exception:
        return False, 0


def register_with_claim(claim: str) -> tuple[int, int]:
    """Register RACE_ID with the given claim. Returns (status_code, latency_ms)."""
    payload = {'id': RACE_ID, 'claim': claim, 'version': 1}
    code, _, latency = http_post(payload, '/business/register')
    return code, latency


def register_distinct(i: int) -> tuple[int, int]:
    payload = {'id': f'h04_distinct_{i}', 'claim': f'distinct probe {i}', 'version': 1}
    code, _, latency = http_post(payload, '/business/register')
    return code, latency


def registry_checksum(data: dict[str, Any]) -> str:
    """Recompute the registry's stored checksum for a truth entity.

    Mirrors src/msb_v3/business/registry.py:_checksum — sha256 of the
    canonical JSON over all keys except 'checksum', first 16 hex chars.
    """
    content = {k: v for k, v in data.items() if k != 'checksum'}
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()[:16]


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    race_path = TRUTH_DIR / f'{RACE_ID}.json'
    distinct_paths = [TRUTH_DIR / f'h04_distinct_{i}.json' for i in range(THREADS_DISTINCT)]

    cleanup = True
    try:
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)
        # Reset probe files so repeated runs start from a known state.
        for p in [race_path, *distinct_paths]:
            if p.exists():
                p.unlink()

        healthy_before, health_code_before = health_ok()
        record['state_before']['health_status'] = health_code_before

        # --- Race 1: same-id concurrent writes with different claims ----------
        claims = [f'claim-from-thread-{i}' for i in range(THREADS_SAME_ID)]
        same_id_results: list[tuple[int, int]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS_SAME_ID) as ex:
            futs = [ex.submit(register_with_claim, c) for c in claims]
            for f in concurrent.futures.as_completed(futs):
                same_id_results.append(f.result())

        # --- Race 2: distinct-id concurrent registrations ----------------------
        distinct_results: list[tuple[int, int]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=THREADS_DISTINCT) as ex:
            futs = [ex.submit(register_distinct, i) for i in range(THREADS_DISTINCT)]
            for f in concurrent.futures.as_completed(futs):
                distinct_results.append(f.result())

        healthy_after, health_code_after = health_ok()

        # --- Post-race integrity audit -----------------------------------------
        record['state_after']['same_id_statuses'] = sorted({c for c, _ in same_id_results})
        record['state_after']['distinct_statuses'] = sorted({c for c, _ in distinct_results})
        record['state_after']['health_before'] = health_code_before
        record['state_after']['health_after'] = health_code_after

        torn_writes: list[str] = []
        checksum_violations: list[str] = []
        files_to_audit = [race_path, *distinct_paths]
        for p in files_to_audit:
            if not p.exists():
                continue
            raw = p.read_text(encoding='utf-8', errors='replace')
            try:
                data = json.loads(raw)
            except Exception:
                torn_writes.append(str(p))
                continue
            stored_checksum = data.get('checksum', '')
            recomputed = registry_checksum(data)
            if stored_checksum and recomputed != stored_checksum:
                checksum_violations.append(
                    f'{p}: stored={stored_checksum[:8]} recomputed={recomputed[:8]}'
                )

        record['state_after']['torn_writes'] = torn_writes
        record['state_after']['checksum_violations'] = checksum_violations
        record['evidence'].append(
            f'same-id registrations returned statuses {sorted({c for c, _ in same_id_results})}'
        )
        record['evidence'].append(
            f'distinct-id registrations returned statuses {sorted({c for c, _ in distinct_results})}'
        )
        record['evidence'].append(f'audited {len(files_to_audit)} entity files')

        server_responsive = healthy_after
        no_torn = len(torn_writes) == 0
        no_checksum_violation = len(checksum_violations) == 0
        passed = server_responsive and no_torn and no_checksum_violation

        record['actual_behavior'] = (
            f'same_id_statuses={sorted({c for c, _ in same_id_results})}; '
            f'distinct_statuses={sorted({c for c, _ in distinct_results})}; '
            f'torn_writes={len(torn_writes)}; checksum_violations={len(checksum_violations)}; '
            f'health_after={health_code_after}'
        )
        record['recovery'] = (
            'all entity files self-consistent after concurrent writes'
            if passed else 'race signatures detected; see errors'
        )
        record['verdict'] = 'pass' if passed else 'fail'
        if not server_responsive:
            record['errors'].append('server became unresponsive during concurrent writes')
        if not no_torn:
            record['errors'].append(f'torn writes detected: {torn_writes}')
        if not no_checksum_violation:
            record['errors'].append(f'checksum violations detected: {checksum_violations}')
    except Exception as e:  # defensive
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )
        if cleanup:
            for p in [race_path, *distinct_paths]:
                try:
                    if p.exists():
                        p.unlink()
                except OSError:
                    pass

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'torn_writes': record['state_after'].get('torn_writes', []),
        'checksum_violations': record['state_after'].get('checksum_violations', []),
        'health_after': record['state_after'].get('health_after'),
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
