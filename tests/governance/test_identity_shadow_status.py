"""K22 exit-criteria status from the identity shadow corpus.

Two properties matter more than the formatting:

1. **Read-only.** A status command must not be able to change the filesystem it
   is reporting on. `IdentityShadowRecorder` mkdirs its root on construction, so
   reusing it here would mean the report creates the corpus it describes. The
   test pins that `load_corpus` never does.
2. **No self-congratulation.** Where a criterion turns on human judgement the
   status must say so rather than report MET, and no status anywhere may read as
   GREEN (blueprint §14 — the researcher is the Evidence Authority).
3. **Evidence is counted by origin.** Criterion 1 is a property of the running
   system. Test traffic can prove the wiring and can never prove the deployment,
   so the two are counted apart and a corpus that holds only test records
   reports INSUFFICIENT DATA rather than a verdict. Several tests below pin that
   separation, including the case that motivated it: a corpus of passing test
   traffic used to read as a measurement it was not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from msb_v3.governance.identity_shadow import (
    LIVE_SURFACES,
    ORIGIN_RUNTIME,
    ORIGIN_TEST,
    ORIGIN_UNRECORDED,
    STATUS_INSUFFICIENT,
    STATUS_JUDGEMENT,
    STATUS_MET,
    STATUS_NOT_MET,
    SURFACE_IN_PROCESS,
    k22_status,
    load_corpus,
)

REPO = Path(__file__).resolve().parents[2]
SURFACES = (*LIVE_SURFACES,)


def _rec(
    surface: str = "chat",
    *,
    state: str = "ALLOW",
    actor: str | None = None,
    reason: str = "actor resolved; identity guards clear",
    tool: str = "vault_read",
    ts: float = 1.0,
    candidate: str | None = None,
    candidate_found: bool = False,
    origin: str = ORIGIN_RUNTIME,
) -> dict:
    """One corpus record. Origin defaults to ``runtime``: the default fixture is
    field evidence, and tests that want test traffic say so explicitly."""
    return {
        "ts": ts,
        "surface": surface,
        "origin": origin,
        "tool_id": tool,
        "kernel_state": state,
        "reason": reason,
        "actor_id": actor,
        "actor_supplied": actor is not None,
        "candidate_agent_id": candidate,
        "candidate_found": candidate_found,
        "enforcement": "shadow",
    }


def _corpus(records: list[dict]) -> dict:
    return {"path": "(test)", "exists": True, "records": records, "malformed": 0}


def _crit(status: dict, cid: int) -> dict:
    return next(c for c in status["criteria"] if c["id"] == cid)


# --- read-only guarantee ---------------------------------------------------


def test_load_corpus_does_not_create_the_directory(tmp_path):
    """The load-bearing property: reporting must not build what it reports on."""
    missing_root = tmp_path / "governance-shadow"
    target = missing_root / "identity.jsonl"

    corpus = load_corpus(target)

    assert corpus["exists"] is False
    assert corpus["records"] == []
    assert not missing_root.exists(), "load_corpus created the corpus directory"


def test_load_corpus_reads_records_and_counts_torn_lines(tmp_path):
    path = tmp_path / "identity.jsonl"
    path.write_text(
        json.dumps(_rec()) + "\n"
        + "\n"                       # blank line: ignored, not malformed
        + '{"ts": 2.0, "surface": "chat"' + "\n"   # torn write: counted
        + json.dumps(_rec(surface="mcp-bridge")) + "\n",
        encoding="utf-8",
    )

    corpus = load_corpus(path)

    assert corpus["exists"] is True
    assert len(corpus["records"]) == 2
    assert corpus["malformed"] == 1  # dropped silently would understate the corpus


# --- criterion 1 -----------------------------------------------------------


def test_criterion_1_insufficient_when_there_is_no_corpus():
    status = k22_status(_corpus([]))
    assert _crit(status, 1)["status"] == STATUS_INSUFFICIENT
    assert status["corpus"]["records"] == 0


def test_criterion_1_not_met_and_names_the_surface_that_is_missing():
    status = k22_status(_corpus([_rec("chat")] + [_rec("mcp-bridge") for _ in range(3)]))
    crit = _crit(status, 1)
    assert crit["status"] == STATUS_NOT_MET
    assert any("mcp-bridge: 3 runtime records, none carrying an actor" in d for d in crit["detail"])


def test_criterion_1_is_insufficient_when_the_corpus_is_test_traffic_only():
    """The correction this job exists for. A live-surface criterion is a property
    of the running system; a corpus of passing test records proves the wiring and
    says nothing about the deployment. It must not report a verdict."""
    records = [_rec("chat", actor="chat.agent", origin=ORIGIN_TEST),
               _rec("mcp-bridge", actor="bridge.agent", origin=ORIGIN_TEST)]
    status = k22_status(_corpus(records))
    crit = _crit(status, 1)
    assert crit["status"] == STATUS_INSUFFICIENT
    assert status["runtime_records"] == 0 and status["test_records"] == 2
    assert any("has NOT been measured" in d for d in crit["detail"])
    # the mechanism evidence is still shown, labelled as what it is
    mechanism = [d for d in crit["detail"] if "mechanism evidence only" in d]
    assert len(mechanism) == len(SURFACES)
    assert any("mcp-bridge" in d for d in mechanism)
    assert all("not field measurement" in d for d in mechanism)


def test_records_with_no_origin_are_not_assumed_to_be_runtime():
    """Records written before the field existed. Assuming either side would turn
    unlabelled evidence into a measurement."""
    rec = _rec("chat", actor="chat.agent")
    del rec["origin"]
    status = k22_status(_corpus([rec]))
    assert status["origins"] == {ORIGIN_UNRECORDED: 1}
    assert status["runtime_records"] == 0
    assert _crit(status, 1)["status"] == STATUS_INSUFFICIENT


def test_criterion_1_met_only_when_every_live_surface_carries_an_actor():
    records = [_rec("chat", actor="chat.agent"), _rec("mcp-bridge", actor="bridge.agent")]
    crit = _crit(k22_status(_corpus(records)), 1)
    assert crit["status"] == STATUS_MET
    assert sum(1 for d in crit["detail"] if "carry an actor" in d) == len(SURFACES)


# --- reconciliation: no record may be silently dropped ----------------------


def test_every_record_is_reconciled_and_none_are_dropped():
    """The live-surface tally must not quietly omit the rest of the corpus.

    Most records never arrive through a live surface. A report that showed only
    the two live-surface lines would sum to 5 against a stated total of 19 and
    leave the gap unexplained.
    """
    records = (
        [_rec("chat") for _ in range(2)]
        + [_rec("mcp-bridge")] * 3
        + [_rec("moie")] * 5
        + [_rec(SURFACE_IN_PROCESS)] * 4
        + [_rec("unknown")] * 5
    )
    status = k22_status(_corpus(records))

    live = sum(v["records"] for v in status["surfaces"].values())
    assert live + status["non_live"]["count"] == status["corpus"]["records"] == 19
    assert status["non_live"]["by_surface"] == {"moie": 5, SURFACE_IN_PROCESS: 4, "unknown": 5}
    # legacy unattributed and declared in-process are different things
    assert status["non_live"]["declared_in_process"] == 9
    assert status["non_live"]["legacy_unattributed"] == 5


def test_criterion_1_discloses_the_non_live_paths_and_the_legacy_bucket():
    records = [_rec("chat", actor="chat.agent"), _rec("mcp-bridge", actor="bridge.agent")]
    crit = _crit(k22_status(_corpus(records)), 1)
    assert crit["status"] == STATUS_MET
    assert not any("outside criterion 1's scope" in d for d in crit["detail"])

    crit = _crit(k22_status(_corpus(records + [_rec("moie")] * 3 + [_rec("unknown")] * 2)), 1)
    assert crit["status"] == STATUS_MET  # live surfaces still pass; this is a disclosure, not a veto
    scope = next(d for d in crit["detail"] if "outside criterion 1's scope" in d)
    assert "moie=3" in scope
    legacy = next(d for d in crit["detail"] if "legacy unattributed" in d)
    assert "2 record(s)" in legacy


def test_a_missing_surface_key_lands_in_the_legacy_bucket():
    rec = _rec("chat")
    del rec["surface"]
    status = k22_status(_corpus([rec]))
    assert status["non_live"]["count"] == 1
    assert status["non_live"]["by_surface"] == {"unknown": 1}
    assert status["non_live"]["legacy_unattributed"] == 1
    assert status["non_live"]["declared_in_process"] == 0


def test_the_surface_fallback_is_not_reported_as_a_candidate_probe():
    """`candidate_agent_id` falls back to the surface name, so an unattributed
    call probes the literal string "unknown". That is not a finding. Neither is
    "in-process", which is the runtime default and names no principal at all."""
    crit = _crit(k22_status(_corpus([_rec("unknown", candidate="unknown")])), 4)
    assert not any("unknown=not registered" in d for d in crit["detail"])
    assert any("no real candidate probe recorded" in d for d in crit["detail"])

    crit = _crit(k22_status(_corpus([_rec(SURFACE_IN_PROCESS, candidate=SURFACE_IN_PROCESS)])), 4)
    assert not any(f"{SURFACE_IN_PROCESS}=not registered" in d for d in crit["detail"])
    assert any("no real candidate probe recorded" in d for d in crit["detail"])


def test_a_real_candidate_probe_is_still_reported():
    records = [_rec("unknown", candidate="unknown"), _rec("chat", candidate="chat.agent", candidate_found=True)]
    crit = _crit(k22_status(_corpus(records)), 4)
    assert any("chat.agent=registered" in d for d in crit["detail"])
    assert not any("unknown=not registered" in d for d in crit["detail"])


# --- criterion 2 -----------------------------------------------------------


def test_criterion_2_is_a_judgement_not_a_verdict():
    status = k22_status(_corpus([_rec(state="REJECTED", reason="no actor supplied by the caller")] * 4))
    crit = _crit(status, 2)
    assert crit["status"] == STATUS_JUDGEMENT
    assert status["refusals"]["rate"] == 1.0
    assert status["refusals"]["causes"] == {"no actor supplied by the caller": 4}


def test_refusal_rate_none_when_there_is_nothing_to_measure():
    status = k22_status(_corpus([]))
    assert status["refusals"]["rate"] is None
    assert _crit(status, 2)["status"] == STATUS_INSUFFICIENT


# --- criterion 3 -----------------------------------------------------------


def test_criterion_3_untested_until_something_carries_an_actor():
    crit = _crit(k22_status(_corpus([_rec()])), 3)
    assert crit["status"] == STATUS_INSUFFICIENT
    assert any("untested" in d for d in crit["detail"])


def test_criterion_3_is_scoped_to_in_process_callers():
    """Criterion 3 is about in-process callers, so a live surface's evidence does
    not answer it — even when that evidence is a clean run."""
    live_only = [_rec("chat", actor="chat.agent"), _rec("mcp-bridge", actor="bridge.agent")]
    crit = _crit(k22_status(_corpus(live_only)), 3)
    assert crit["status"] == STATUS_INSUFFICIENT
    assert any("runtime-origin in-process call carried an actor" in d for d in crit["detail"])


def test_criterion_3_met_when_no_actor_carrying_in_process_call_was_refused():
    records = [_rec("moie", actor="moie.agent"), _rec("factory", actor="factory.agent")]
    crit = _crit(k22_status(_corpus(records)), 3)
    assert crit["status"] == STATUS_MET
    assert any("corpus-scoped" in d for d in crit["detail"])


def test_criterion_3_not_met_and_names_the_false_rejection():
    records = [
        _rec("moie", actor="moie.agent"),
        _rec("moie", state="DENIED", actor="moie.agent", reason="tenant mismatch", tool="vault_write"),
    ]
    status = k22_status(_corpus(records))
    crit = _crit(status, 3)
    assert crit["status"] == STATUS_NOT_MET
    assert status["refusals"]["with_actor"] == 1
    assert any("vault_write" in d for d in crit["detail"])


def test_criterion_3_does_not_count_test_traffic_as_a_measurement():
    records = [_rec("moie", actor="moie.agent", origin=ORIGIN_TEST)]
    crit = _crit(k22_status(_corpus(records)), 3)
    assert crit["status"] == STATUS_INSUFFICIENT
    assert any("mechanism evidence only" in d for d in crit["detail"])


# --- criterion 4 -----------------------------------------------------------


def test_criterion_4_met_when_a_surface_identity_resolves():
    status = k22_status(_corpus([_rec("chat", actor="chat.agent")]))
    crit = _crit(status, 4)
    assert crit["status"] == STATUS_MET
    assert status["resolved_actors"] == ["chat.agent"]
    # the existence half is mechanical; the grant half is not
    assert "grant" in crit["open_judgement"]


def test_criterion_4_not_met_when_the_actor_never_resolves():
    records = [_rec("chat", state="REJECTED", actor="never.registered", reason="unknown actor: never.registered")]
    crit = _crit(k22_status(_corpus(records)), 4)
    assert crit["status"] == STATUS_NOT_MET
    assert any("never resolved" in d for d in crit["detail"])


def test_criterion_4_reports_the_candidate_probe():
    records = [_rec("chat", state="REJECTED", reason="no actor supplied", candidate="chat", candidate_found=False)]
    crit = _crit(k22_status(_corpus(records)), 4)
    assert any("candidate probes" in d and "chat=not registered" in d for d in crit["detail"])


# --- the whole report ------------------------------------------------------


def test_no_status_anywhere_reads_as_green():
    records = [_rec("chat", actor="chat.agent"), _rec("mcp-bridge", actor="bridge.agent")]
    status = k22_status(_corpus(records), coverage={"declared_by_tools": [], "unknown_tier": []})
    allowed = {STATUS_MET, STATUS_NOT_MET, STATUS_INSUFFICIENT, STATUS_JUDGEMENT}
    for crit in status["criteria"]:
        assert crit["status"] in allowed
    blob = json.dumps(status).upper()
    assert "GREEN" not in blob and "FROZEN" not in blob
    assert "not a gate" in status["note"].lower()


def test_cli_emits_json_and_flips_nothing(tmp_path):
    corpus_path = tmp_path / "identity.jsonl"
    corpus_path.write_text(json.dumps(_rec("chat", actor="chat.agent")) + "\n", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    proc = subprocess.run(
        [sys.executable, "-m", "msb_v3.governance", "identity-status", "--json", "--path", str(corpus_path)],
        cwd=REPO,
        env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["corpus"]["records"] == 1
    assert payload["resolved_actors"] == ["chat.agent"]
    assert "non_live" in payload
    assert "origins" in payload
    # read-only: the command created nothing next to the corpus
    assert sorted(p.name for p in tmp_path.iterdir()) == before
