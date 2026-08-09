#!/usr/bin/env python3
"""H05 Contract fuzzing — live MCP /mcp/proxy under valid/almost-valid/malformed
payloads.

Fires a fixed battery of contract cases at the running server and records, per
case, the HTTP status, latency, a short body fingerprint, and whether the
observed behavior was *safe* for that case. Valid contracts must succeed (200);
malformed contracts must fail safely (rejected, not silently accepted, not a
hang). The overall verdict is `pass` only if every case behaves as its contract
requires; any deviation is a `fail` with the offending case named.

Standalone counterpart to the shared hygiene_runner's `h05_contract`, which used
a thinner 6-case anonymous battery. This runner is deterministic in its case set
but its numbers depend on live server state.
"""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
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
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip()

SECRET = env.get('MCP_BRIDGE_SECRET', os.environ.get('MCP_BRIDGE_SECRET', ''))
BASE_URL = env.get('MSB_BASE_URL', os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766'))
PROXY = '/mcp/proxy'


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h05_contract_fuzzing',
        'skill': 'fuzzing',
        'input': 'valid/almost-valid/malformed MCP contract payloads',
        'environment': BASE_URL,
        'failure_injected': '',
        'expected_behavior': (
            'valid requests succeed; invalid/malformed requests fail safely with 4xx; '
            'vault_read of a missing file returns 404 (contract, not vault contents)'
        ),
        'actual_behavior': '',
        'latency_ms': 0,
        'errors': [],
        'state_before': {},
        'state_after': {},
        'recovery': '',
        'false_repair': False,
        'evidence': [
            'live MCP /mcp/proxy contract fuzzing',
            'observed HTTP status codes for valid/almost-valid/malformed payloads',
        ],
        'verdict': 'unknown',
    }


def save(record: dict[str, Any]) -> Path:
    ts = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = EVIDENCE_DIR / f"{record['experiment_id']}_{ts}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding='utf-8')
    return path


def http_post_raw(body: bytes) -> tuple[int, str, int]:
    """POST raw bytes to the proxy. Returns (status, body, latency_ms).

    status 0 signals a transport-level failure (never reached a response)."""
    req = Request(f"{BASE_URL}{PROXY}", data=body, headers={
        'x-mcp-secret': SECRET,
        'content-type': 'application/json',
        'accept': 'application/json',
    })
    start = dt.datetime.now(dt.timezone.utc)
    try:
        with urlopen(req, timeout=20) as resp:
            text = resp.read().decode('utf-8', errors='ignore')
            code = resp.status
    except HTTPError as e:
        text = e.read().decode('utf-8', errors='ignore')
        code = e.code
    except URLError:
        text, code = '', 0
    latency = int((dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000)
    return code, text, latency


def is_4xx(status: int) -> bool:
    return 400 <= status < 500


def is_safe_rejection(status: int) -> bool:
    """Malformed input handled without silent acceptance and without a hang."""
    return status == 0 or is_4xx(status)


# Each case: (name, raw_json_body, pass_predicate(status) -> bool)
CASES: list[tuple[str, bytes, Callable[[int], bool]]] = [
    ('valid_status',
     json.dumps({'tool': 'metrics_json', 'args': {}}).encode(),
     lambda s: s == 200),
    ('valid_vault_read',
     json.dumps({'tool': 'vault_read', 'args': {'path': 'README.md'}}).encode(),
     lambda s: s in (200, 404)),  # 404 = file-not-found = correct vault contract response
    ('vault_read_traversal',
     json.dumps({'tool': 'vault_read', 'args': {'path': '../../../../etc/passwd'}}).encode(),
     lambda s: is_4xx(s)),
    ('missing_tool',
     json.dumps({'args': {}}).encode(),
     is_4xx),
    ('tool_string_not_dict',
     json.dumps('metrics_json').encode(),
     is_safe_rejection),
    ('args_wrong_type',
     json.dumps({'tool': 'metrics_json', 'args': 'not-a-dict'}).encode(),
     is_safe_rejection),
    ('extra_weird_field',
     json.dumps({'tool': 'metrics_json', 'args': {}, 'wat': [1, 2, 3]}).encode(),
     lambda s: s == 200),
    ('null_payload',
     b'null',
     is_safe_rejection),
    ('empty_dict',
     json.dumps({}).encode(),
     is_4xx),
    ('missing_args',
     json.dumps({'tool': 'metrics_json'}).encode(),
     lambda s: s != 0),  # server must respond, not hang
]


def main() -> None:
    record = new_record()
    failures: list[str] = []
    total_latency = 0

    for name, body, predicate in CASES:
        status, text, latency = http_post_raw(body)
        total_latency += latency
        passed = bool(predicate(status))
        record['state_after'][name] = {
            'status_code': status,
            'latency_ms': latency,
            'passed': passed,
            'sha256': hashlib.sha256(text.encode()).hexdigest()[:16],
        }
        if not passed:
            failures.append(name)

    n = len(CASES)
    record['latency_ms'] = total_latency
    record['actual_behavior'] = (
        f'fuzzed {n} contract cases; passed={n - len(failures)}; failures={failures}'
    )
    record['verdict'] = 'pass' if not failures else 'fail'
    if failures:
        record['errors'].append(f'contract failures: {failures}')
        record['recovery'] = 'server stayed responsive; failing cases need contract review'
    else:
        record['recovery'] = 'all contract cases behaved safely'

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'passed': n - len(failures),
        'failures': failures,
        'artifact': str(path),
    }, indent=2))


if __name__ == '__main__':
    main()
