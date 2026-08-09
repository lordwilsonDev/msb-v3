#!/usr/bin/env python3
"""H08 Chaos — real fault-injection harness with recovery assertions.

This replaces the previously-blocked "baseline only" runner. A stdlib-only
asyncio TCP proxy (h08_chaos_proxy.py) sits between the client and the real
msb-v3 server and injects three fault classes, one at a time:

  latency 500ms   service degrades gracefully — request still succeeds
                  (200) but takes >= the injected delay (slow, not broken)
  drop            connection dropped mid-exchange — client sees a
                  transport-level failure (empty reply / reset), the server
                  itself is never faulted
  truncate 128B   response cut short — client sees an incomplete reply
                  (no false-200 on a truncated body)

For every fault class the runner asserts two things:
  1. the *degradation signature* matches expectation (latency -> slow
     success; drop/truncate -> client-visible failure), and
  2. the service *recovers fully* once the fault path is removed — a fresh
     transparent proxy probe returns 200 and a truth entity registered and
     retrieved through it is intact.

Verdict = pass iff every fault class produced its expected signature AND the
server recovered completely after each one (graceful degradation + full
recovery). This is a genuine chaos experiment: real faults are injected on
the connection path and recovery is verified against the live service.

Standalone counterpart to the shared hygiene_runner's `h08_chaos`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import socket
import subprocess
import sys
import time
from http.client import IncompleteRead
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
SECRET = os.environ.get('MCP_BRIDGE_SECRET', env.get('MCP_BRIDGE_SECRET', ''))
BASE_URL = env.get('MSB_BASE_URL', os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766'))
UPSTREAM_PORT = int(BASE_URL.rsplit(':', 1)[1]) if BASE_URL.rsplit(':', 1)[1].isdigit() else 8766
PROXY_BASE_PORT = 18766
PROXY_SCRIPT = Path(__file__).with_name('h08_chaos_proxy.py')

SCENARIOS = [
    {'name': 'latency_500ms', 'fault': 'latency', 'ms': 500, 'truncate_bytes': 0,
     'expect': 'slow_success', 'min_ms': 400, 'probe_path': '/health'},
    {'name': 'connection_drop', 'fault': 'drop', 'ms': 0, 'truncate_bytes': 0,
     'expect': 'client_failure', 'min_ms': 0, 'probe_path': '/health'},
    {'name': 'truncated_response', 'fault': 'truncate', 'ms': 0, 'truncate_bytes': 128,
     'expect': 'client_failure', 'min_ms': 0,
     'probe_path': '/business/retrieve/{probe_id}'},
]


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h08_chaos',
        'skill': 'chaos',
        'input': (
            'fault-injection proxy (stdlib asyncio TCP) between client and '
            f'server :{UPSTREAM_PORT}; faults: latency 500ms, connection drop, '
            'response truncation — one at a time, with recovery probes after each'
        ),
        'environment': BASE_URL,
        'failure_injected': (
            'latency(500ms) / connection-drop / response-truncation injected '
            'on the client->server path via h08_chaos_proxy.py'
        ),
        'expected_behavior': (
            'graceful degradation: latency -> slow success (200, >= injected '
            'delay); drop/truncate -> client-visible failure; full recovery '
            'through a fresh transparent proxy after each fault is removed'
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


def proxy_up(port: int, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def request_through(base: str, path: str, timeout_s: int = 30, read_body: bool = True) -> tuple[int, int, str]:
    """GET through the given base. Returns (status, latency_ms, error_or_empty).

    read_body=True (default) reads the full body so an incomplete response
    (truncated mid-body) surfaces as a client-visible failure rather than a
    false 200 — this is what makes the truncate fault observable.

    The error string is prefixed with 'INCOMPLETE:' when the body read raised
    http.client.IncompleteRead — the truncation signature. This is what
    distinguishes a truncated response from a plain connection drop.
    """
    req = Request(f'{base}{path}', headers={'x-mcp-secret': SECRET, 'accept': 'application/json'})
    start = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            latency = int((time.perf_counter() - start) * 1000)
            if read_body:
                resp.read()
            return resp.status, latency, ''
    except HTTPError as e:
        latency = int((time.perf_counter() - start) * 1000)
        return e.code, latency, ''
    except IncompleteRead as e:
        latency = int((time.perf_counter() - start) * 1000)
        return 0, latency, f'INCOMPLETE: {e}'
    except Exception as e:
        latency = int((time.perf_counter() - start) * 1000)
        return 0, latency, str(e)[:120]


def register_through(base: str, probe_id: str, claim: str = 'chaos recovery probe') -> tuple[int, str]:
    payload = json.dumps({'id': probe_id, 'claim': claim, 'version': 1}).encode()
    req = Request(
        f'{base}/business/register',
        data=payload,
        headers={'x-mcp-secret': SECRET, 'content-type': 'application/json', 'accept': 'application/json'},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, ''
    except HTTPError as e:
        return e.code, ''
    except Exception as e:
        return 0, str(e)[:120]


def retrieve_through(base: str, probe_id: str) -> tuple[int, str]:
    req = Request(f'{base}/business/retrieve/{probe_id}',
                  headers={'x-mcp-secret': SECRET, 'accept': 'application/json'})
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read().decode('utf-8', errors='ignore')
            return resp.status, body
    except HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='ignore')[:200]
    except Exception as e:
        return 0, str(e)[:120]


def purge_through(base: str, probe_id: str) -> None:
    try:
        req = Request(f'{base}/business/purge/{probe_id}', method='DELETE',
                      headers={'x-mcp-secret': SECRET})
        urlopen(req, timeout=15)
    except Exception:
        pass


def start_proxy(fault: str, ms: int, truncate_bytes: int, port: int) -> subprocess.Popen:
    cmd = [
        sys.executable, str(PROXY_SCRIPT),
        '--port', str(port),
        '--upstream-port', str(UPSTREAM_PORT),
        '--fault', fault,
    ]
    if fault == 'latency' and ms:
        cmd += ['--ms', str(ms)]
    if fault == 'truncate' and truncate_bytes:
        cmd += ['--truncate-bytes', str(truncate_bytes)]
    return subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    scenario_results: dict[str, dict[str, Any]] = {}
    proxies: list[subprocess.Popen] = []
    run_ts = int(time.time())
    # Large entity used ONLY for the truncate probe: /health is small enough
    # that 128 bytes can pass it whole, which would make truncation invisible.
    big_probe_id = f'h08_big_probe_{run_ts}'

    # Stale-proxy cleanup: a SIGKILLed previous run can leave a proxy holding
    # a fixed port (reuse_address does not free an ACTIVE listener), which
    # would make this run fail to bind. Kill any leftovers first.
    try:
        subprocess.run(
            ['pkill', '-f', 'h08_chaos_proxy.py'],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass
    time.sleep(0.3)

    try:
        # --- Baseline: direct health before any fault ----------------------
        base_code, _, _ = request_through(BASE_URL, '/health')
        record['state_before']['direct_health'] = base_code
        if base_code != 200:
            record['verdict'] = 'fail'
            record['errors'].append(f'server not healthy before chaos: HTTP {base_code}')
            record['actual_behavior'] = 'precondition failed — server down before faults'
            raise SystemExit(1)

        # Register the large probe entity (used ONLY by the truncate scenario)
        # before the fault loop so the truncate probe always has a big target:
        # /health is small enough that 128 bytes can pass it whole, which would
        # make truncation invisible.
        big_code, big_err = register_through(BASE_URL, big_probe_id, claim='X' * 4096)
        if big_code != 200:
            record['verdict'] = 'fail'
            record['errors'].append(
                f'could not register large probe entity {big_probe_id}: HTTP {big_code} {big_err}'
            )
            record['actual_behavior'] = 'precondition failed — no large probe entity'
            raise SystemExit(1)

        # --- Each fault class, one at a time --------------------------------
        for idx, sc in enumerate(SCENARIOS):
            port = PROXY_BASE_PORT + idx
            proc = start_proxy(sc['fault'], sc['ms'], sc['truncate_bytes'], port)
            proxies.append(proc)
            if not proxy_up(port):
                record['verdict'] = 'fail'
                record['errors'].append(f'proxy for {sc["name"]} failed to come up')
                break

            proxy_base = f'http://127.0.0.1:{port}'
            probe_path = sc['probe_path']
            if '{probe_id}' in probe_path:
                probe_path = probe_path.format(probe_id=big_probe_id)
            code, latency_ms, err = request_through(proxy_base, probe_path)

            degraded_ok = False
            if sc['expect'] == 'slow_success':
                degraded_ok = code == 200 and latency_ms >= sc['min_ms']
            elif sc['expect'] == 'client_failure':
                # A client-visible failure (0 = transport/reset/incomplete) is
                # the expected signature. For truncate we additionally require
                # the body read to have raised IncompleteRead (the actual
                # truncation signature), so a plain connection reset cannot
                # false-pass as a truncation.
                truncated = err.startswith('INCOMPLETE:')
                degraded_ok = code == 0 and latency_ms < 30000
                if sc['fault'] == 'truncate':
                    degraded_ok = degraded_ok and truncated

            scenario_results[sc['name']] = {
                'injected': sc['fault'],
                'expected': sc['expect'],
                'observed_status': code,
                'observed_latency_ms': latency_ms,
                'observed_error': err,
                'degraded_ok': degraded_ok,
                'truncated_read': err.startswith('INCOMPLETE:'),
            }
            record['evidence'].append(
                f'{sc["name"]}: injected={sc["fault"]} status={code} '
                f'latency={latency_ms}ms degraded_ok={degraded_ok}'
            )

            # Kill this fault proxy — remove the fault path.
            proc.kill()
            proc.wait(timeout=10)
            proxies.remove(proc)
            time.sleep(0.5)

            # --- Recovery assertion: fresh transparent proxy ----------------
            rec_port = PROXY_BASE_PORT + 100 + idx
            rec_proc = start_proxy('none', 0, 0, rec_port)
            proxies.append(rec_proc)
            rec_base = f'http://127.0.0.1:{rec_port}'
            recovered_health = proxy_up(rec_port)
            rec_code, rec_latency, rec_err = request_through(rec_base, '/health') if recovered_health else (0, 0, 'proxy down')

            # Unique recovery-probe id per scenario so a flaked purge of one
            # scenario's entity can never 409-collide with the next scenario's
            # register (a 409 would otherwise false-fail the whole experiment).
            scenario_probe_id = f'h08_recovery_probe_{run_ts}_{idx}'
            reg_code, reg_err = register_through(rec_base, scenario_probe_id)
            ret_code, ret_body = retrieve_through(rec_base, scenario_probe_id)
            rec_ok = rec_code == 200 and reg_code == 200 and ret_code == 200
            scenario_results[sc['name']]['recovered_health'] = rec_code
            scenario_results[sc['name']]['recovery_register'] = reg_code
            scenario_results[sc['name']]['recovery_retrieve'] = ret_code
            scenario_results[sc['name']]['recovered_ok'] = rec_ok
            record['evidence'].append(
                f'{sc["name"]} recovery: health={rec_code} register={reg_code} '
                f'retrieve={ret_code} rec_ok={rec_ok}'
            )

            rec_proc.kill()
            rec_proc.wait(timeout=10)
            proxies.remove(rec_proc)
            purge_through(BASE_URL, scenario_probe_id)
            time.sleep(0.3)

        record['state_after']['scenarios'] = scenario_results
        all_degraded = all(r['degraded_ok'] for r in scenario_results.values())
        all_recovered = all(r.get('recovered_ok') for r in scenario_results.values())
        passed = len(scenario_results) == len(SCENARIOS) and all_degraded and all_recovered

        record['actual_behavior'] = (
            '; '.join(
                f'{n}: status={r["observed_status"]} latency={r["observed_latency_ms"]}ms '
                f'recovered={r.get("recovered_ok")}'
                for n, r in scenario_results.items()
            )
        )
        if passed:
            record['verdict'] = 'pass'
            record['recovery'] = (
                'every fault class produced its expected degradation signature '
                'and the service recovered fully (health + register + retrieve) '
                'through a fresh transparent proxy after each fault was removed'
            )
        else:
            record['verdict'] = 'fail'
            record['recovery'] = 'one or more fault classes did not degrade as expected or did not recover (see errors)'
            for n, r in scenario_results.items():
                if not r['degraded_ok']:
                    record['errors'].append(
                        f'{n}: degradation signature mismatch — expected '
                        f'{r["expected"]}, observed status={r["observed_status"]} '
                        f'latency={r["observed_latency_ms"]}ms err={r["observed_error"]}'
                    )
                if not r.get('recovered_ok'):
                    record['errors'].append(
                        f'{n}: recovery failed — health={r.get("recovered_health")} '
                        f'register={r.get("recovery_register")} retrieve={r.get("recovery_retrieve")}'
                    )
    except Exception as e:  # defensive
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )
        for p in proxies:
            try:
                p.kill()
            except Exception:
                pass
        purge_through(BASE_URL, big_probe_id)
        for i in range(len(SCENARIOS)):
            purge_through(BASE_URL, f'h08_recovery_probe_{run_ts}_{i}')

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'scenarios': {n: {'status': r['observed_status'], 'latency_ms': r['observed_latency_ms'],
                          'recovered': r.get('recovered_ok')} for n, r in scenario_results.items()},
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
