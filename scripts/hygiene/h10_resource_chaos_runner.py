#!/usr/bin/env python3
"""H10 Resource exhaustion / chaos.

Probes three scenarios:
1. Disk pressure: flood truth registry with many large JSON files
2. Payload pressure: send oversized truth entity payloads
3. Burst health probes: rapid /health requests to check latency/availability
"""

from __future__ import annotations

import datetime as dt
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
SKILL = 'resource-exhaustion'
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
        'failure_injected': (
            'flooded truth registry dir with 500 large JSON files, sent 10 oversized payloads, '
            'issued 200 rapid /health requests'
        ),
        'expected_behavior': (
            '/health remains 200 with latency under 2s; /register rejects oversized payloads '
            'with 413 or 400; service stays alive throughout chaos'
        ),
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


def http_request(payload: dict[str, object], path: str) -> tuple[int, str, int]:
    url = f"{BASE_URL}{path}"
    data = json.dumps(payload).encode('utf-8')
    req = Request(url, data=data, headers={
        'x-mcp-secret': SECRET,
        'content-type': 'application/json',
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


def simple_get(path: str) -> tuple[int, str, int]:
    url = f"{BASE_URL}{path}"
    req = Request(url, headers={'x-mcp-secret': SECRET, 'accept': 'application/json'})
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


experiment_id = 'h10_resource_chaos'
record = new_record(experiment_id, 'resource exhaustion chaos', BASE_URL)
cleanup = True
truth_backup = TRUTH_DIR.parent / f'{TRUTH_DIR.name}.hygiene_backup'
chaos_dir = TRUTH_DIR / 'h10_chaos_flood'
oversize_entity = TRUTH_DIR / 'h10_oversize.json'
try:
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    # Reset stale probe entity from any prior run (a leftover caused a false
    # 409-already-exists in an earlier session; never trust the archive).
    if oversize_entity.exists():
        oversize_entity.unlink()

    # Baseline health/latency
    b_health_code, b_health_body, b_health_lat = simple_get('/health')
    record['state_before']['baseline_health_status'] = b_health_code
    record['state_before']['baseline_health_latency_ms'] = b_health_lat
    record['evidence'].append(f"baseline_health status={b_health_code} latency={b_health_lat}ms")

    # Chaos 1: flood truth registry with 500 files
    chaos_dir.mkdir(parents=True, exist_ok=True)
    start_flood = time.time()
    for i in range(500):
        payload = {'claim': 'chaos flood payload ' + 'x' * 1024, 'index': i, 'garbage': 'x' * 4096}
        (chaos_dir / f'chaos_{i}.json').write_text(json.dumps(payload), encoding='utf-8')
    flood_duration_ms = int((time.time() - start_flood) * 1000)
    record['state_after']['flood_duration_ms'] = flood_duration_ms
    record['evidence'].append(f"flooded 500 truth files in {flood_duration_ms}ms")

    # Probe health during flood
    c_health_code, c_health_body, c_health_lat = simple_get('/health')
    record['state_after']['flood_health_status'] = c_health_code
    record['state_after']['flood_health_latency_ms'] = c_health_lat
    record['evidence'].append(f"flood_health status={c_health_code} latency={c_health_lat}ms")

    # Chaos 2: oversized payloads
    large_text = 'x' * (1024 * 1024)
    oversized_payload = {'id': 'h10_oversize', 'claim': large_text, 'version': 1}
    os_code, os_body, os_lat = http_request(oversized_payload, '/business/register')
    record['state_after']['oversized_status'] = os_code
    record['state_after']['oversized_latency_ms'] = os_lat
    record['evidence'].append(f"oversized_payload status={os_code} latency={os_lat}ms")

    # Chaos 3: burst health probes
    burst_statuses = []
    burst_latencies = []
    for i in range(200):
        code, body, lat = simple_get('/health')
        burst_statuses.append(code)
        burst_latencies.append(lat)
    record['state_after']['burst_count'] = len(burst_statuses)
    record['state_after']['burst_statuses'] = burst_statuses
    record['state_after']['burst_latencies'] = burst_latencies
    record['state_after']['burst_p95_latency_ms'] = sorted(burst_latencies)[int(len(burst_latencies) * 0.95)]
    record['state_after']['burst_non_200_count'] = sum(1 for s in burst_statuses if s != 200)
    record['evidence'].append(
        f"burst 200 requests: non_200={record['state_after']['burst_non_200_count']} p95_latency={record['state_after']['burst_p95_latency_ms']}ms"
    )

    # Overall verdict
    service_alive = c_health_code == 200
    oversized_rejected = os_code == 413 or os_code == 400 or os_code == 422
    burst_survived = record['state_after']['burst_non_200_count'] < 20 and record['state_after']['burst_p95_latency_ms'] < 2000
    record['actual_behavior'] = (
        f"health_alive={service_alive} oversized_rejected={oversized_rejected} "
        f"burst_survived={burst_survived} non_200={record['state_after']['burst_non_200_count']}"
    )
    record['verdict'] = 'pass' if (service_alive and oversized_rejected and burst_survived) else 'fail'
    if not service_alive:
        record['errors'].append('service became unreachable during resource chaos')
    if not oversized_rejected:
        record['errors'].append('oversized payload was accepted instead of rejected')
    if not burst_survived:
        record['errors'].append('burst health probes caused excessive failures or latency')
finally:
    if cleanup:
        if chaos_dir.exists():
            shutil.rmtree(chaos_dir)
        if oversize_entity.exists():
            oversize_entity.unlink()
        if truth_backup.exists():
            if TRUTH_DIR.exists():
                shutil.rmtree(TRUTH_DIR)
            shutil.move(str(truth_backup), str(TRUTH_DIR))

path = save(record)
print(json.dumps({
    'experiment': experiment_id,
    'verdict': record['verdict'],
    'service_alive': record['state_after'].get('flood_health_status'),
    'oversized_rejected': record['state_after'].get('oversized_status'),
    'burst_non_200': record['state_after'].get('burst_non_200_count'),
    'burst_p95_ms': record['state_after'].get('burst_p95_latency_ms'),
    'artifact': str(path),
}, indent=2))
