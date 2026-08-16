#!/usr/bin/env python3
"""H07 Auto-healing — does the audit chain detect tampering AND recover?

The 2026-08-09 finding showed `verify_chain()` detected a tampered middle
record (valid=False at seq=3) but there was NO recovery path: deleting the
bad record and appending a new one left seq=4's prev_hash dangling, so the
chain stayed broken forever.

Fix (2026-08-09, issue #1): `AuditChain` now has `quarantine()` (explicit
compromise marking, never silent) and `repair()` (checkpoint cascade-rewrite
that re-anchors at the last verified record, recomputes the tail, and appends
an auditable "chain.repaired" event). This runner proves the FULL loop:

  append 5 -> tamper seq=3 -> verify detects -> quarantine marks ->
  repair re-anchors -> verify passes again

Verdict is `pass` only when tamper detection works AND the recovery path
restores a valid chain.

Standalone counterpart to the shared hygiene_runner's `h07_heal`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from msb_v3.uac.audit_chain import AuditChain, tamper

EVIDENCE_DIR = Path(os.environ.get('MSB_REPO', Path(__file__).resolve().parents[2])) / 'artifacts' / 'hygiene'
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL = os.environ.get('MSB_BASE_URL', 'http://127.0.0.1:8766')


def new_record() -> dict[str, Any]:
    return {
        'experiment_id': 'h07_auto_healing',
        'skill': 'self-healing',
        'input': 'tamper middle audit record then recover via quarantine + repair',
        'environment': BASE_URL,
        'failure_injected': 'SQLite UPDATE modified payload of seq=3 in audit chain DB',
        'expected_behavior': (
            'verify_chain detects the tamper; quarantine() marks the compromise '
            'explicitly; repair() cascade-rewrites the tail and appends an '
            'auditable chain.repaired event; verify_chain passes again'
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
        record['sequence'] = [
            {'step': 'baseline', 'records': baseline.get('record_count', 0), 'valid': baseline.get('valid', True)}
        ]

        # Append 5 records
        for i in range(5):
            chain.append('h07_probe', 'event', {'n': i})
        after5 = chain.verify_chain()
        record['sequence'].append({'step': 'after_5_appends', 'records': after5.get('record_count', 0), 'valid': after5.get('valid', True)})

        # Tamper with seq=3: rewrite its payload WITHOUT recomputing its hash
        # (defeating the append-only trigger the way an attacker would).
        tamper(
            db_path,
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

        # Recovery path: quarantine() marks the compromise, repair() re-anchors.
        quarantine_result = chain.quarantine()
        record['sequence'].append({
            'step': 'quarantine',
            'quarantined': quarantine_result.get('quarantined'),
            'broken_at_seq': quarantine_result.get('broken_at_seq'),
            'state': quarantine_result.get('state'),
        })
        # repair() is operator-gated (security-hardening #5): supply the
        # operator token from the environment so CI's throwaway token (and any
        # local one) matches settings.operator_token. Unset token = dev mode,
        # which repair() records explicitly and allows.
        repair_result = chain.repair(operator=os.environ.get("MSB_OPERATOR_TOKEN"))
        record['sequence'].append({
            'step': 'repair',
            'repaired': repair_result.get('repaired'),
            'broken_at_seq': repair_result.get('broken_at_seq'),
            'repaired_at_seq': repair_result.get('repaired_at_seq'),
        })
        after_repair = chain.verify_chain()
        heal_succeeded = after_repair.get('valid', False) is True
        record['sequence'].append({
            'step': 'after_repair_verify',
            'valid': after_repair.get('valid', False),
            'record_count': after_repair.get('record_count'),
        })

        # The repair event itself must be auditable (chain includes it).
        chain_tail = chain.get_chain()[-1]
        repair_event_auditable = (
            chain_tail.component == 'chain'
            and chain_tail.event_type == 'repaired'
            and heal_succeeded
        )

        record['state_after'] = {
            'tamper_detected': tamper_detected,
            'quarantined': quarantine_result.get('quarantined'),
            'repaired': repair_result.get('repaired'),
            'heal_succeeded': heal_succeeded,
            'repair_event_auditable': repair_event_auditable,
        }
        record['evidence'].extend([
            'verify_chain detects tampering at seq=3',
            'quarantine() records the compromise explicitly',
            'repair() cascade-rewrites tail anchored at last verified record',
            'chain.repaired event appended (recovery is auditable)',
            'verify_chain passes again after repair',
        ])

        record['actual_behavior'] = (
            f"tamper_detected={tamper_detected} quarantined={quarantine_result.get('quarantined')} "
            f"repaired={repair_result.get('repaired')} heal_succeeded={heal_succeeded} "
            f"repair_event_auditable={repair_event_auditable}"
        )
        if tamper_detected and heal_succeeded and repair_event_auditable:
            record['recovery'] = (
                'chain recovered: tamper detected, quarantine marked, repair '
                're-anchored tail with auditable event, verify passes'
            )
        else:
            record['recovery'] = 'recovery loop incomplete; see errors'
            if not tamper_detected:
                record['errors'].append('tampering was NOT detected at seq=3')
            if not quarantine_result.get('quarantined'):
                record['errors'].append('quarantine() did not mark the compromise')
            if not repair_result.get('repaired'):
                record['errors'].append('repair() did not re-anchor the chain')
            if not heal_succeeded:
                record['errors'].append('verify_chain still broken after repair')
            if not repair_event_auditable:
                record['errors'].append('repair event not auditable in the chain')
        record['verdict'] = 'pass' if (tamper_detected and heal_succeeded and repair_event_auditable) else 'fail'
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
