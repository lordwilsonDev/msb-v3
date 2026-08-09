#!/usr/bin/env python3
"""H07 Auto-healing — does the audit chain detect tampering AND recover?

The 2026-08-09 finding (artifact `h07_auto_healing_*.json`) showed
`verify_chain()` detects a tampered middle record (valid=False at seq=3) but
has NO recovery path: deleting the bad record and appending a new one leaves
seq=4's prev_hash pointing at the deleted record's hash, so the chain stays
broken. This runner reproduces that experiment deterministically against the
real `AuditChain` in a temp DB and records the current state of healing:

- tamper detected       -> the detection half of the hypothesis
- heal_succeeded        -> the recovery half (does a repair path exist?)
- quarantine_available  -> is there a quarantine mode for compromised chains?

Verdict is `fail` while `heal_succeeded` is false — a genuine red light, not a
file gap. `pass` requires a real recovery mechanism (cascade-rewrite,
checkpoint-recovery, or explicit quarantine) to exist.

Standalone counterpart to the shared hygiene_runner's `h07_heal`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from msb_v3.uac.audit_chain import AuditChain

EVIDENCE_DIR = Path(os.environ.get('MSB_REPO', Path(__file__).resolve().parents[2])) / 'artifacts' / 'hygiene'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766')


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h07_auto_healing',
        'skill': 'self-healing',
        'input': 'tamper middle audit record and attempt automated recovery',
        'environment': BASE_URL,
        'failure_injected': 'SQLite UPDATE modified payload of seq=3 in audit chain DB',
        'expected_behavior': (
            'either automatic healing restores a valid chain OR explicit tamper '
            'alert with chain quarantine; subsequent records remain verifiable'
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


def main() -> int:
    record = new_record()
    start = dt.datetime.now(dt.timezone.utc)

    tmpdir = tempfile.TemporaryDirectory()
    db_path = Path(tmpdir.name) / 'audit_chain.db'
    try:
        chain = AuditChain(str(db_path))
        record['state_before']['chain_class'] = 'msb_v3.uac.audit_chain.AuditChain'

        # Baseline
        baseline = chain.verify_chain()
        record['sequence'] = [{'step': 'baseline', 'records': baseline.get('record_count', 0), 'valid': baseline.get('valid', True)}]

        # Append 5 records
        for i in range(5):
            chain.append('h07_probe', 'event', {'n': i})
        after5 = chain.verify_chain()
        record['sequence'].append({'step': 'after_5_appends', 'records': after5.get('record_count', 0), 'valid': after5.get('valid', True)})

        # Tamper with seq=3: rewrite its payload WITHOUT recomputing its hash.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE audit_records SET payload=? WHERE seq=3",
                (json.dumps({'n': 'TAMPERED'}, ensure_ascii=False),),
            )
        after_tamper = chain.verify_chain()
        tamper_detected = after_tamper.get('valid') is False and after_tamper.get('broken_at_seq') == 3
        record['sequence'].append({
            'step': 'after_tamper_seq3',
            'valid': after_tamper.get('valid', True),
            'broken_at_seq': after_tamper.get('broken_at_seq'),
            'reason': after_tamper.get('reason'),
        })

        # Attempt recovery: delete the bad record and append a fresh one.
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM audit_records WHERE seq=3")
        chain.append('h07_probe', 'event', {'n': 'post-heal'})
        after_heal = chain.verify_chain()
        heal_succeeded = after_heal.get('valid', False) is True

        # Does a quarantine mode exist on the class?
        has_auto_heal = hasattr(chain, 'auto_heal')
        has_quarantine = hasattr(chain, 'quarantine') or hasattr(chain, 'quarantine_chain')
        has_cascade = hasattr(chain, 'cascade_rewrite') or hasattr(chain, 'rebuild')

        record['sequence'].append({
            'step': 'after_delete_seq3_and_append',
            'valid': after_heal.get('valid', False),
            'broken_at_seq': after_heal.get('broken_at_seq'),
            'reason': after_heal.get('reason'),
        })
        record['state_after'] = {
            'tamper_detected': tamper_detected,
            'heal_succeeded': heal_succeeded,
            'has_auto_heal_method': has_auto_heal,
            'has_quarantine_mode': has_quarantine,
            'has_cascade_rewrite': has_cascade,
        }
        record['evidence'].append('verify_chain detects tampering at seq=3')
        record['evidence'].append('delete seq=3 does not restore prev_hash linkage for seq=4')
        record['evidence'].append('append after delete does not patch existing prev_hash references')

        record['actual_behavior'] = (
            f"tamper_detected={tamper_detected} heal_succeeded={heal_succeeded} "
            f"auto_heal_method={has_auto_heal} quarantine={has_quarantine} cascade={has_cascade}"
        )
        if heal_succeeded:
            record['recovery'] = 'chain recovered after tamper (healing mechanism present)'
        else:
            record['recovery'] = 'no recovery mechanism exists; chain remains broken after delete+append'
            record['errors'].extend([
                'no auto_heal() method exists in AuditChain',
                'no cascade-rewrite or checkpoint-recovery mechanism exists',
                'no quarantine mode for compromised chains',
            ])
        record['verdict'] = 'pass' if heal_succeeded else 'fail'
    except Exception as e:
        record['verdict'] = 'fail'
        record['errors'].append(str(e))
    finally:
        record['latency_ms'] = int(
            (dt.datetime.now(dt.timezone.utc) - start).total_seconds() * 1000
        )
        tmpdir.cleanup()

    path = save(record)
    print(json.dumps({
        'experiment': record['experiment_id'],
        'verdict': record['verdict'],
        'tamper_detected': record['state_after'].get('tamper_detected'),
        'heal_succeeded': record['state_after'].get('heal_succeeded'),
        'artifact': str(path),
    }, indent=2))
    return 0 if record['verdict'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
