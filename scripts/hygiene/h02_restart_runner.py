#!/usr/bin/env python3
"""H02 Restart hygiene — declared BLOCKED pending a real stop/start harness.

Why blocked (not pass):
  This runner's core probe is write -> hash -> drop in-memory handle ->
  re-hydrate from disk -> re-hash. That proves the *filesystem* round-trips
  bytes, i.e. "the filesystem works". It does NOT prove MSB survives a real
  process restart: no process is killed, no supervisor restarts it, and the
  API layer is not re-initialized against the on-disk state. A genuine restart
  experiment requires orchestrating scripts/stop.sh + scripts/start.sh around a
  state write and verifying the API-reported state survived — out of scope for
  a deterministic read-only runner and therefore honestly recorded as blocked.

The filesystem probe below is kept as *side evidence* only (what the current
mechanism can prove), never as a pass.

Standalone counterpart to the shared hygiene_runner's blocked `h02_restart`.
"""

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any
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

TRUTH_DIR = REPO / 'var' / 'truth'
GRAPH_DIR = REPO / 'var' / 'graph'
PROBE_TRUTH = TRUTH_DIR / 'probe_entity.json'
PROBE_GRAPH = GRAPH_DIR / 'probe_session.json'


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h02_restart_hygiene',
        'skill': 'state-hygiene',
        'input': 'filesystem-backed truth/graph persistence across simulated restart',
        'environment': BASE_URL,
        'failure_injected': (
            'simulated process kill by deleting in-memory probe file then '
            'restoring from disk'
        ),
        'expected_behavior': (
            'filesystem-backed state remains intact after simulated restart; '
            'reload yields same content'
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def http_get(path: str) -> int:
    """GET a URL and return the HTTP status (0 on transport failure)."""
    req = Request(f"{BASE_URL}{path}", headers={
        'x-mcp-secret': SECRET,
        'accept': 'application/json',
    })
    try:
        with urlopen(req, timeout=20) as resp:
            return resp.status
    except HTTPError as e:
        return e.code
    except URLError:
        return 0


def main() -> None:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    # --- state_before: what exists prior to the probe -----------------------
    record['state_before'] = {
        'truth_exists': PROBE_TRUTH.exists(),
        'graph_exists': PROBE_GRAPH.exists(),
        'truth_dir_exists': TRUTH_DIR.exists(),
        'graph_dir_exists': GRAPH_DIR.exists(),
    }

    try:
        TRUTH_DIR.mkdir(parents=True, exist_ok=True)
        GRAPH_DIR.mkdir(parents=True, exist_ok=True)

        truth_payload = {
            'id': 'probe_entity',
            'claim': 'restart-hygiene probe entity',
            'committed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        graph_payload = {
            'session': 'probe_session',
            'nodes': ['a', 'b', 'c'],
            'edges': [['a', 'b'], ['b', 'c']],
            'committed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        }

        # Commit to disk, then hash the committed bytes.
        PROBE_TRUTH.write_text(json.dumps(truth_payload, indent=2), encoding='utf-8')
        PROBE_GRAPH.write_text(json.dumps(graph_payload, indent=2), encoding='utf-8')
        truth_hash_before = sha256_file(PROBE_TRUTH)
        graph_hash_before = sha256_file(PROBE_GRAPH)
        record['evidence'].append('wrote probe files to var/truth and var/graph')

        # Simulate the process kill: drop the in-memory objects entirely, so the
        # only surviving copy is on disk. A restart can rely on nothing else.
        del truth_payload, graph_payload

        # "Restart": rehydrate purely from disk and re-hash.
        _ = json.loads(PROBE_TRUTH.read_text())
        _ = json.loads(PROBE_GRAPH.read_text())
        truth_hash_after = sha256_file(PROBE_TRUTH)
        graph_hash_after = sha256_file(PROBE_GRAPH)
        record['evidence'].append('reloaded files after simulated restart')

        # Side evidence: try to reload the probe through the live API. Not
        # registered via the API, so a 404 here is expected and non-fatal.
        graph_reload_status = http_get('/graph/probe_session')
        truth_reload_status = http_get('/business/retrieve/probe_entity')

        record['state_after'] = {
            'probe_graph_exists': PROBE_GRAPH.exists(),
            'probe_truth_exists': PROBE_TRUTH.exists(),
            'graph_hash_before': graph_hash_before,
            'graph_hash_after': graph_hash_after,
            'truth_hash_before': truth_hash_before,
            'truth_hash_after': truth_hash_after,
            'graph_reload_status': graph_reload_status,
            'truth_reload_status': truth_reload_status,
        }
        record['evidence'].append(f'probe_graph path: {PROBE_GRAPH}')
        record['evidence'].append(f'probe_truth path: {PROBE_TRUTH}')

        graph_intact = graph_hash_before == graph_hash_after
        truth_intact = truth_hash_before == truth_hash_after
        persisted = PROBE_GRAPH.exists() and PROBE_TRUTH.exists()
        fs_roundtrip_ok = graph_intact and truth_intact and persisted

        record['actual_behavior'] = (
            f'fs_roundtrip_ok={fs_roundtrip_ok}; '
            f'graph_intact={graph_intact}; truth_intact={truth_intact}; '
            f'files_persisted={persisted}; '
            f'graph_reload={graph_reload_status}; truth_reload={truth_reload_status}'
        )
        record['recovery'] = (
            'BLOCKED: filesystem round-trip intact, but a real process restart '
            'was not performed; restart survival remains unverified'
        )
        record['verdict'] = 'blocked'
        record['errors'].append(
            'near-tautological by design: write/hash/read-back cannot prove '
            'restart survival; requires real stop/start orchestration'
        )
    except Exception as e:  # pragma: no cover - defensive
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )
        # Clean up probe files so repeated runs start from a known state.
        for p in (PROBE_TRUTH, PROBE_GRAPH):
            try:
                if p.exists():
                    p.unlink()
            except OSError:
                pass

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'artifact': str(path),
    }, indent=2))


if __name__ == '__main__':
    main()
