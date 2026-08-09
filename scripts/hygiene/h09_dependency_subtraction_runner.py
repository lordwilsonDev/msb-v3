#!/usr/bin/env python3
"""H09 Dependency subtraction testing.

Tests graceful degradation when a non-builtin dependency is removed.
Targets:
1. truth registry directory removal -> /health, /ready should still respond degraded
2. optional external dependency unavailability -> verify fallback path
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = Path(os.environ.get('MSB_REPO', Path(__file__).resolve().parents[2]))
EVIDENCE_DIR = REPO / 'artifacts' / 'hygiene'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SKILL = 'dependency-hygiene'
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


def new_record(experiment_id: str, input_desc: str, environment: str) -> dict[str, Any]:
    return {
        'experiment_id': experiment_id,
        'skill': SKILL,
        'input': input_desc,
        'environment': environment,
        'failure_injected': 'removed truth registry directory during runtime',
        'expected_behavior': 'service stays alive, /health returns 200, /ready returns 503 degraded; after restore, service recovers without manual restart',
        'actual_behavior': '',
        'latency_ms': None,
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


def http_request(path: str) -> tuple[int, str, int]:
    url = f"{BASE_URL}{path}"
    req = Request(url, headers={
        'x-mcp-secret': SECRET,
        'accept': 'application/json',
    })
    start = dt.datetime.now(dt.timezone.utc)
    try:
        with urlopen(req, timeout=20) as resp:
            body = resp.read().decode('utf-8', errors='ignore')
            code = resp.status
    except HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        code = e.code
    latency = int((dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000)
    return code, body, latency


def probe(record: dict[str, Any], label: str, path: str) -> tuple[int, str, int]:
    code, body, latency = http_request(path)
    record['state_after'][f"{label}_status"] = code
    record['state_after'][f"{label}_body_sha256"] = hashlib.sha256(body.encode()).hexdigest()[:16]
    record['evidence'].append(f"{label}: status={code} latency={latency}ms")
    return code, body, latency


experiment_id = 'h09_dependency_subtraction'
record = new_record(experiment_id, 'truth registry directory removal', BASE_URL)
backup_dir = TRUTH_DIR.parent / f'{TRUTH_DIR.name}.hygiene_backup'
cleanup = True
try:
    if not TRUTH_DIR.exists():
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)

    # Baseline: service healthy, truth dir present
    code0, body0, lat0 = probe(record, 'baseline_health', '/health')
    code1, body1, lat1 = probe(record, 'baseline_ready', '/ready')
    code2, body2, lat2 = probe(record, 'baseline_status', '/status')
    record['state_before']['health_status'] = code0
    record['state_before']['ready_status'] = code1
    record['state_before']['status_status'] = code2

    # Subtract dependency: rename truth registry directory
    if TRUTH_DIR.exists():
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        shutil.move(str(TRUTH_DIR), str(backup_dir))
    time.sleep(1)

    # Probe during subtraction
    code3, body3, lat3 = probe(record, 'subtracted_health', '/health')
    code4, body4, lat4 = probe(record, 'subtracted_ready', '/ready')
    code5, body5, lat5 = probe(record, 'subtracted_status', '/status')

    degraded_health = code3 == 200
    degraded_ready = code4 == 503 or 'ready' in body4.lower()
    still_alive = code3 > 0

    record['state_after']['subtraction_degraded_health'] = degraded_health
    record['state_after']['subtraction_degraded_ready'] = degraded_ready
    record['state_after']['subtraction_still_alive'] = still_alive
    record['actual_behavior'] = (
        f"health={code3} ready={code4} status={code5}; "
        f"degraded_health={degraded_health} degraded_ready={degraded_ready} alive={still_alive}"
    )
    record['latency_ms'] = lat3

    # Restore dependency
    if backup_dir.exists():
        if TRUTH_DIR.exists():
            shutil.rmtree(TRUTH_DIR)
        shutil.move(str(backup_dir), str(TRUTH_DIR))
    time.sleep(1)

    # Probe after restoration
    code6, body6, lat6 = probe(record, 'restored_health', '/health')
    code7, body7, lat7 = probe(record, 'restored_ready', '/ready')
    code8, body8, lat8 = probe(record, 'restored_status', '/status')

    recovered = code6 == 200 and code7 == 200
    record['state_after']['restoration_recovered'] = recovered
    record['evidence'].append(f"restored: health={code6} ready={code7} status={code8} recovered={recovered}")
    record['recovery'] = f"restored truth dir; health={code6} ready={code7}"

    # Verdict: pass if service stayed alive during subtraction and recovered after
    record['verdict'] = 'pass' if (still_alive and recovered) else 'fail'
    if not still_alive:
        record['errors'].append('service crashed or became unreachable during dependency subtraction')
    if not recovered:
        record['errors'].append('service did not recover after restoring dependency')
except Exception as e:
    record['verdict'] = 'fail'
    record['errors'].append(str(e))
finally:
    if cleanup and backup_dir.exists():
        if TRUTH_DIR.exists():
            shutil.rmtree(TRUTH_DIR)
        shutil.move(str(backup_dir), str(TRUTH_DIR))

path = save(record)
print(json.dumps({
    'experiment': experiment_id,
    'verdict': record['verdict'],
    'still_alive': record['state_after'].get('subtraction_still_alive'),
    'recovered': record['state_after'].get('restoration_recovered'),
    'artifact': str(path),
}, indent=2))
