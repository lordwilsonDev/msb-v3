#!/usr/bin/env python3
"""H06 Audit-chain tampering detection.

Probes two tampering scenarios against the truth registry:
  1. alter claim text but keep original checksum -> not detected by checksum guard
  2. alter claim text and body -> expected 409 checksum mismatch
"""

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
SKILL = 'audit-hygiene'
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
TRUTH_DIR.mkdir(parents=True, exist_ok=True)


def new_record(experiment_id: str, input_desc: str, environment: str) -> dict[str, Any]:
    return {
        'experiment_id': experiment_id,
        'skill': SKILL,
        'input': input_desc,
        'environment': environment,
        'failure_injected': 'post-write tampering of truth registry entity JSON on disk',
        'expected_behavior': (
            'tampered entity should be rejected on read with checksum mismatch'
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


def http_get(path: str) -> tuple[int, str, int]:
    """GET a route (retrieve is GET-only; POST yields 405)."""
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


experiment_id = 'h06_audit_tampering'
record = new_record(experiment_id, 'truth registry read integrity after disk tampering', BASE_URL)
entity_id = 'h06_truth_probe'
entity_path = TRUTH_DIR / f'{entity_id}.json'

cleanup = True
try:
    if entity_path.exists():
        entity_path.unlink()

    payload = {
        'id': entity_id,
        'claim': 'tampering probe claim',
        'version': 1,
    }
    register_code, register_body, register_latency = http_request(payload, '/business/register')
    record['latency_ms'] = register_latency
    record['state_before']['register_status'] = register_code
    record['evidence'].append(f"registered truth entity {entity_id} with status {register_code}")

    if register_code != 200 or not entity_path.exists():
        record['verdict'] = 'fail'
        record['errors'].append(f"registration failed or file missing: {register_code} {register_body}")
    else:
        data = json.loads(entity_path.read_text())
        stored_checksum = data.get('checksum', '')
        record['state_before']['stored_checksum'] = stored_checksum
        record['evidence'].append(f"stored checksum: {stored_checksum}")

        # Case 1: tamper claim text but keep checksum unchanged.
        data['claim'] = 'tampered claim text'
        entity_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        case1_code, case1_body, case1_latency = http_get(f'/business/retrieve/{entity_id}')
        record['state_after']['case1_status'] = case1_code
        record['state_after']['case1_body_sha256'] = hashlib.sha256(case1_body.encode()).hexdigest()[:16]
        record['evidence'].append(
            f"case1 tamper-preserved-checksum status={case1_code}"
        )

        # Case 2: alter body so checksum no longer matches.
        data['claim'] = 'tampered claim text v2'
        data.pop('checksum', None)
        entity_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        case2_code, case2_body, case2_latency = http_get(f'/business/retrieve/{entity_id}')
        tamper_detected = case2_code == 409 or 'Checksum mismatch' in case2_body
        record['state_after']['case2_status'] = case2_code
        record['state_after']['case2_body_sha256'] = hashlib.sha256(case2_body.encode()).hexdigest()[:16]
        record['state_after']['tamper_detected'] = tamper_detected
        record['evidence'].append(
            f"case2 tamper-mismatched-checksum status={case2_code}; detected={tamper_detected}"
        )
        record['actual_behavior'] = (
            f"register={register_code}; "
            f"case1={case1_code}; "
            f"case2={case2_code}; "
            f"tamper_detected={tamper_detected}"
        )
        record['latency_ms'] = case2_latency
        record['verdict'] = 'pass' if tamper_detected else 'fail'
        if not tamper_detected:
            record['errors'].append('tampered truth entity was accepted after checksum mismatch')
except Exception as e:
    record['verdict'] = 'fail'
    record['errors'].append(str(e))
finally:
    if cleanup and entity_path.exists():
        entity_path.unlink()

path = save(record)
print(json.dumps({
    'experiment': experiment_id,
    'verdict': record['verdict'],
    'tamper_detected': record['state_after'].get('tamper_detected'),
    'artifact': str(path),
}, indent=2))
