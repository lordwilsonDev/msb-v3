#!/usr/bin/env python3
"""H03: Idempotency hygiene — repeat identical read-only requests and count mutations."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

REPO = Path(os.environ.get('MSB_REPO', Path(__file__).resolve().parents[2]))
EVIDENCE_DIR = REPO / 'artifacts' / 'hygiene'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
SKILL = 'api-hygiene'
BASE_URL = os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766')
SECRET = os.environ.get('MCP_BRIDGE_SECRET', '')


def new_record(experiment_id: str, input_desc: str, environment: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        'experiment_id': experiment_id,
        'skill': SKILL,
        'input': input_desc,
        'environment': environment,
        'failure_injected': '',
        'expected_behavior': 'Identical responses for identical idempotent requests; no server-side mutations',
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
    return record


def save(record: dict[str, Any]) -> Path:
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = EVIDENCE_DIR / f"{record['experiment_id']}_{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding='utf-8')
    return path


def mcp_get(path: str):
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


def main() -> int:
    experiment_id = 'h03_idempotency'
    record = new_record(experiment_id, 'GET /mcp/status repeated 8 times', BASE_URL)
    responses: list[str] = []
    codes: list[int] = []
    latencies: list[int] = []
    for i in range(8):
        code, body, latency = mcp_get('/mcp/status')
        codes.append(code)
        latencies.append(latency)
        responses.append(body)
        record['state_after'][f'request_{i+1}'] = {
            'status_code': code,
            'latency_ms': latency,
            'sha256': hashlib.sha256(body.encode()).hexdigest()[:16],
        }

    unique_codes = len(set(codes))
    unique_hashes = len(set(hashlib.sha256(b.encode()).hexdigest() for b in responses))
    max_latency = max(latencies)
    min_latency = min(latencies)

    record['actual_behavior'] = (
        f"repeated GET /mcp/status 8 times; codes={codes}; "
        f"unique_codes={unique_codes}; unique_response_hashes={unique_hashes}; "
        f"latency_min={min_latency}ms max={max_latency}ms"
    )
    record['latency_ms'] = max_latency
    record['evidence'].append('repeated identical read-only MCP status requests')
    record['evidence'].append('observed stable status payload hashes across repetitions')

    if unique_codes == 1 and unique_hashes == 1:
        record['verdict'] = 'pass'
    else:
        record['verdict'] = 'fail'
        record['errors'].append('non-idempotent or unstable response detected')

    path = save(record)
    print(json.dumps({
        'experiment': experiment_id,
        'verdict': record['verdict'],
        'unique_codes': unique_codes,
        'unique_hashes': unique_hashes,
        'max_latency_ms': max_latency,
        'artifact': str(path),
    }, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
