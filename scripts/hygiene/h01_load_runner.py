#!/usr/bin/env python3
"""H01 MCP load — concurrent HTTP requests against the live MCP bridge.

Fires bursts of 10/50/100 concurrent requests at /mcp/proxy and /mcp/tools,
records p50/p95/p99 latency, throughput, error rate and timeouts. Verdict is
`pass` only when zero errors and zero timeouts across all levels.

Standalone counterpart to the shared hygiene_runner's `h01_load`.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import time
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

LEVELS = (10, 50, 100)


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h01_mcp_load',
        'skill': 'performance-hygiene',
        'input': 'MCP HTTP requests from local Python clients',
        'environment': BASE_URL,
        'failure_injected': 'concurrency 10/50/100 bursts',
        'expected_behavior': 'all requests succeed, no errors, no timeouts',
        'actual_behavior': '',
        'latency': {'p50_ms': None, 'p95_ms': None, 'p99_ms': None},
        'throughput_rps': None,
        'error_rate': None,
        'timeout_count': 0,
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


def fire(n: int) -> tuple[int, int]:
    """Send one request. Returns (status_code, latency_ms); 0 status = timeout/transport."""
    path = '/mcp/proxy' if n % 3 else '/mcp/tools'
    payload = {'tool': 'metrics_json', 'args': {}} if path == '/mcp/proxy' else None
    url = f'{BASE_URL}{path}'
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    req = Request(url, data=data, headers={
        'x-mcp-secret': SECRET,
        'content-type': 'application/json',
        'accept': 'application/json',
    })
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=30) as resp:
            code = resp.status
    except HTTPError as e:
        code = e.code
    except Exception:
        code = 0  # timeout / transport failure
    return code, int((time.perf_counter() - t0) * 1000)


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    latencies: list[int] = []
    errors = 0
    timeouts = 0
    attempts = 0

    try:
        for level in LEVELS:
            with concurrent.futures.ThreadPoolExecutor(max_workers=level) as ex:
                futs = [ex.submit(fire, i) for i in range(level)]
                for f in concurrent.futures.as_completed(futs):
                    attempts += 1
                    code, latency = f.result()
                    latencies.append(latency)
                    if code == 0:
                        timeouts += 1
                    elif code != 200:
                        errors += 1

        latencies_sorted = sorted(latencies)

        def pct(p: float) -> int | None:
            if not latencies_sorted:
                return None
            idx = max(0, min(len(latencies_sorted) - 1, int(len(latencies_sorted) * p)))
            return latencies_sorted[idx]

        record['latency'] = {'p50_ms': pct(0.5), 'p95_ms': pct(0.95), 'p99_ms': pct(0.99)}
        total_ms = sum(latencies)
        record['throughput_rps'] = round(attempts / max(1, total_ms / 1000), 2)
        record['error_rate'] = round(errors / max(1, attempts), 4)
        record['timeout_count'] = timeouts
        record['state_after'] = {
            'attempts': attempts,
            'errors': errors,
            'timeouts': timeouts,
            'levels': list(LEVELS),
        }
        record['evidence'].append(
            f'bursts {list(LEVELS)}; attempts={attempts} errors={errors} timeouts={timeouts}'
        )
        record['actual_behavior'] = (
            f'attempts={attempts} errors={errors} timeouts={timeouts} '
            f'p95={pct(0.95)}ms'
        )
        record['verdict'] = 'pass' if errors == 0 and timeouts == 0 else 'fail'
        if errors or timeouts:
            record['errors'].append(f'errors={errors} timeouts={timeouts} under concurrent load')
            record['recovery'] = 'server remained reachable; failing requests need review'
        else:
            record['recovery'] = 'all requests succeeded under burst load'
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
        'attempts': attempts,
        'errors': errors,
        'timeouts': timeouts,
        'p95_ms': record['latency'].get('p95_ms'),
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
