#!/usr/bin/env python3
"""H08 Chaos — declared BLOCKED pending an env-specific chaos harness.

Why blocked (not pass, not fail):
  Genuine chaos engineering (random latency injection, partial failures,
  dependency drops, process-level faults) requires a harness that sits between
  the client and the server — e.g. a proxy that injects latency/errors, or
  env-specific fault injection (chaos-mesh, toxiproxy, or a custom proxy).
  That harness is environment-specific and is not present in this repo's
  runtime. Recording a baseline without that harness and calling it a chaos
  test would be a false result. The honest verdict is therefore `blocked`,
  with the baseline recorded as side evidence for future harness work.

Standalone counterpart to the shared hygiene_runner's blocked `h08_chaos`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

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


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h08_chaos',
        'skill': 'chaos',
        'input': 'random latency, partial failures, dependency drops',
        'environment': BASE_URL,
        'failure_injected': 'requires external chaos proxy; not present — baseline only',
        'expected_behavior': 'service degrades gracefully under injected faults',
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


def simple_get(path: str) -> tuple[int, int]:
    url = f'{BASE_URL}{path}'
    req = Request(url, headers={'x-mcp-secret': SECRET, 'accept': 'application/json'})
    start = dt.datetime.now(dt.timezone.utc)
    try:
        with urlopen(req, timeout=20) as resp:
            code = resp.status
    except HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    latency = int((dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000)
    return code, latency


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)
    try:
        health_code, health_latency = simple_get('/health')
        ready_code, ready_latency = simple_get('/ready')
        record['state_after'] = {
            'health_status': health_code,
            'health_latency_ms': health_latency,
            'ready_status': ready_code,
            'ready_latency_ms': ready_latency,
        }
        record['evidence'].append(f'baseline /health status={health_code} latency={health_latency}ms')
        record['evidence'].append(f'baseline /ready status={ready_code} latency={ready_latency}ms')
        record['actual_behavior'] = (
            f'baseline_only health={health_code} ready={ready_code}; '
            'no fault injection performed (no chaos harness present)'
        )
        record['recovery'] = (
            'BLOCKED: baseline recorded; chaos requires an env-specific '
            'injection harness (proxy/fault tooling) that is not installed'
        )
        record['verdict'] = 'blocked'
        record['errors'].append(
            'chaos experiment not run: requires external latency/error injection '
            'harness; baseline recorded as side evidence instead'
        )
    except Exception as e:
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'baseline_health': record['state_after'].get('health_status'),
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 0  # blocked is a non-failing non-pass


if __name__ == '__main__':
    raise SystemExit(main())
