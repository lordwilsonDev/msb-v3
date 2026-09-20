#!/usr/bin/env python3
"""Identity-shadow runtime probe — produce the records K22 criterion 1 reads.

Criterion 1 ("every live surface carries an actor") is evaluated on records the
RUNNING system wrote (``origin=runtime``). The suite can only ever write
``origin=test`` records, so before this probe existed the criterion could not be
measured at all — it read INSUFFICIENT DATA by construction, and a green suite
stood in for a measurement nobody had taken.

This probe health-checks the live server, exercises each live surface over HTTP,
and reports what the corpus gained. It is the only thing in the repo that
produces runtime-origin evidence on demand.

It observes; it does not judge. The criterion verdict stays with
``python -m msb_v3.governance identity-status`` (one judge, one report), and
``--strict`` exists only for a caller that wants the probe itself to gate.

The probe writes NOTHING itself: every record it reports was written by the
server under test. The corpus is read through ``load_corpus``, which — unlike
the recorder — cannot create the directory it reports on.

Usage:
    probe_identity_shadow_runtime.py [--base-url URL] [--surface chat]
                                     [--surface mcp-bridge] [--strict] [--json]
    probe_identity_shadow_runtime.py --self-test   # server-free logic check

Exit codes:
    0 = the exercise ran and the corpus was re-read (see the per-surface tally)
    1 = --strict, and a selected live surface produced no new runtime record
    2 = harness misuse: no secret, unreachable server, refused auth, or an
        unknown surface name
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from msb_v3.governance.identity_shadow import (  # noqa: E402
    DEFAULT_IDENTITY_SHADOW_ROOT,
    IDENTITY_SHADOW_FILE,
    LIVE_SURFACES,
    ORIGIN_RUNTIME,
    load_corpus,
)

DEFAULT_BASE_URL = os.getenv("MSB_BASE_URL", "http://127.0.0.1:8766").rstrip("/")

# The shadow root is CWD-relative (identity_shadow.DEFAULT_IDENTITY_SHADOW_ROOT),
# so the server must run with its working directory at the repo root. This probe
# anchors the same path to the repo instead of to wherever it was invoked from,
# and says so if nothing turns up.
DEFAULT_CORPUS = REPO_ROOT / DEFAULT_IDENTITY_SHADOW_ROOT / IDENTITY_SHADOW_FILE

PROBE_SESSION = "identity-shadow-probe"

# The bridge's governed tools are the 5 vault mutations and every one of them
# requires `vault.write`; the identity observation point sits AFTER the
# capability gate, so a bridge record exists only for a call the perimeter
# already authorised — which then really executes. So the probe writes exactly
# one clearly labelled note, always the same path, so a re-run overwrites rather
# than accumulates.
PROBE_VAULT_NOTE = "99_Meta/identity-shadow-probe.md"

# The chat exercise asks the model to call a READ-ONLY tool: `vault_lint`
# requires no capability, so it passes the perimeter on the chat surface (which
# grants none) and executes for real. Pointing this at a mutation would be
# denied before the observation point and record nothing at all.
PROBE_CHAT_TOOL: Dict[str, Any] = {
    "type": "function",
    "name": "vault_lint",
    "description": "Scan the vault for frontmatter and cross-link hygiene issues (read-only)",
    "parameters": {"type": "object", "properties": {}},
}
PROBE_CHAT_QUERY = (
    "Call the vault_lint tool now, with no arguments, then report its result. "
    "You must call the tool; do not answer from memory."
)


# ---------------------------------------------------------------------------
# HTTP (stdlib only, same convention as scripts/probe_conversation_e2e.py)
# ---------------------------------------------------------------------------


def _maybe_json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def _request(
    method: str,
    url: str,
    secret: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    timeout: int = 30,
) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"x-mcp-secret": secret}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _maybe_json(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, _maybe_json(exc.read().decode("utf-8", "replace"))
    except OSError as exc:
        raise ConnectionError(f"{method} {url} failed: {exc}") from exc


def _load_secret(explicit: Optional[str]) -> str:
    """Explicit flag, then the environment, then the repo's .env (as run.sh does)."""
    if explicit:
        return explicit.strip()
    env = os.getenv("MCP_BRIDGE_SECRET", "").strip()
    if env:
        return env
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("MCP_BRIDGE_SECRET="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ---------------------------------------------------------------------------
# The exercises — one per live surface, both real HTTP calls
# ---------------------------------------------------------------------------


def _exercise_chat(base_url: str, secret: str) -> Dict[str, Any]:
    payload = {"query": PROBE_CHAT_QUERY, "session": PROBE_SESSION, "tools": [PROBE_CHAT_TOOL]}
    status, body = _request("POST", f"{base_url}/chat", secret, payload, timeout=300)
    reply = ""
    harness = ""
    if isinstance(body, dict):
        reply = str((body.get("payload") or {}).get("text") or "")
        # ok/event say whether the SURFACE answered or degraded. A record can
        # exist while the reply is a fallback (the tool call can succeed and a
        # later round-trip fail), so the reply alone under-reports the state.
        if "ok" in body or body.get("event"):
            harness = f"ok={body.get('ok')} event={body.get('event')}"
            if body.get("error"):
                harness += f" error={body.get('error')}"
    evidence = f"model reply: {reply[:200]!r}" if reply else f"response: {str(body)[:200]}"
    if harness:
        evidence = f"{harness}; {evidence}"
    return {"surface": "chat", "status": status, "evidence": evidence, "blocked": ""}


def _exercise_bridge(base_url: str, secret: str) -> Dict[str, Any]:
    note = (
        "# identity-shadow probe note\n\n"
        "Written by scripts/probe_identity_shadow_runtime.py to produce a\n"
        "runtime-origin identity-shadow record on the mcp-bridge surface. The\n"
        "observation point sits after the capability gate, so a record exists only\n"
        "for a call the perimeter authorised and which then executed. Safe to\n"
        "delete; the probe overwrites this path rather than accumulating.\n"
    )
    payload = {
        "tool": "vault_write",
        "args": {"path": PROBE_VAULT_NOTE, "content": note},
    }
    status, body = _request("POST", f"{base_url}/mcp/proxy", secret, payload, timeout=120)
    governed = ""
    if isinstance(body, dict):
        result = body.get("result")
        if isinstance(result, dict):
            governed = str(result.get("governed") or "")
    evidence = f"governed outcome: {governed!r}" if governed else f"response: {str(body)[:220]}"
    # Machine-readable reason, so a caller (the daily heartbeat) can tell
    # "refused by the capability gate" from "no idea why" WITHOUT re-parsing
    # prose. Those two need opposite responses: the first is a declared-off
    # state when no grant is configured, the second is a real miss.
    blocked = "capability-denied" if _is_capability_denial(governed) else ""
    return {"surface": "mcp-bridge", "status": status, "evidence": evidence, "blocked": blocked}


def _is_capability_denial(governed: str) -> bool:
    """Did runtime.py refuse this call at the capability gate?

    The gate's own wording (tools/runtime.py) is
    ``[denied] tool <id> requires capabilities: <ids>``. Matching that shape
    rather than the absence of a record is the point: "refused before the
    recorder ran" and "the recorder ran and wrote nothing" are different
    findings and must not be collapsed.
    """
    return governed.startswith("[denied]") and "requires capabilities:" in governed


_EXERCISES = {"chat": _exercise_chat, "mcp-bridge": _exercise_bridge}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _tally(surface: str, new_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [r for r in new_records if r.get("surface") == surface]
    with_actor = [r for r in rows if r.get("actor_supplied")]
    return {
        "surface": surface,
        "records": len(rows),
        "with_actor": len(with_actor),
        "actor_ids": sorted({str(r.get("actor_id")) for r in with_actor if r.get("actor_id")}),
        "states": sorted({str(r.get("kernel_state")) for r in rows}),
        "origins": sorted({str(r.get("origin")) for r in rows}),
        # Filled from the exercise, not the corpus: a surface can be refused
        # BEFORE any record exists, which is exactly the bridge's case when its
        # capability grant is withdrawn. See _is_capability_denial.
        "blocked": "",
    }


def _diagnose(surface: str, tally: Dict[str, Any], exercise: Dict[str, Any]) -> List[str]:
    """Why a surface produced no record. Stated as the mechanism, not a guess."""
    if tally["records"]:
        return []
    if surface == "mcp-bridge":
        return [
            "the bridge's governed path is reached only by the 5 vault mutations, all of",
            "which require 'vault.write', and the identity observation point sits AFTER the",
            "capability gate. So a bridge record needs MSB_MCP_GRANTED_CAPABILITIES to cover",
            "that capability — and with no grant, NO bridge call can be observed at all.",
            f"blocked={tally.get('blocked') or 'unknown'}; the bridge's own response:",
            f"    {exercise['evidence']}",
        ]
    return [
        "no governed call reached the perimeter. In this probe's runs that meant the model",
        "replied in prose instead of calling the tool — which happens when the tool spec",
        "never arrives in the shape the backend honours (see local_ai/ollama.py).",
        f"    {exercise['evidence']}",
    ]


def self_test() -> int:
    """Server-free, zero-spend checks that the probe's own logic is not vacuous.

    Same contract as ``probe_conversation_e2e.self_test``: a broken probe must
    fail loudly rather than quietly report zero records, because "zero records"
    is a legitimate finding about the surfaces and the two must not look alike.
    """
    # 1. Every live surface must have an exercise. Adding one to LIVE_SURFACES
    #    without teaching the probe about it would leave that surface
    #    permanently unmeasurable while the probe still reported success.
    assert set(_EXERCISES) == set(LIVE_SURFACES), (
        f"probe exercises {sorted(_EXERCISES)}, live surfaces are {sorted(LIVE_SURFACES)}"
    )

    # 2. The tally must count only its own surface and must not read "an actor
    #    was asserted" as "an actor resolved". (An earlier tally in this lane
    #    silently dropped 44 of 56 records; this pins that class of bug.)
    rows = [
        {"surface": "chat", "actor_supplied": True, "actor_id": "a", "kernel_state": "REJECTED", "origin": ORIGIN_RUNTIME},
        {"surface": "chat", "actor_supplied": False, "kernel_state": "REJECTED", "origin": ORIGIN_RUNTIME},
        {"surface": "mcp-bridge", "actor_supplied": True, "actor_id": "b", "kernel_state": "REJECTED", "origin": ORIGIN_RUNTIME},
        {"surface": "governed-loop", "actor_supplied": True, "actor_id": "c", "kernel_state": "ALLOW", "origin": ORIGIN_RUNTIME},
    ]
    chat = _tally("chat", rows)
    assert chat["records"] == 2, chat
    assert chat["with_actor"] == 1, chat
    assert chat["actor_ids"] == ["a"], chat
    #    and a non-live entry path must never be folded into a live surface's
    #    tally (nor dropped from the corpus by these helpers)
    assert _tally("governed-loop", rows)["records"] == 1
    live_total = sum(_tally(s, rows)["records"] for s in LIVE_SURFACES)
    assert live_total == 3 < len(rows), f"live tallies swallowed a non-live record: {live_total} of {len(rows)}"

    # 3. The diagnosis must work in both directions: silent when the surface
    #    produced records, specific when it did not.
    exercise = {"surface": "chat", "status": 200, "evidence": "e"}
    assert _diagnose("chat", chat, exercise) == []
    for surface in LIVE_SURFACES:
        empty = _tally(surface, [])
        text = _diagnose(surface, empty, exercise)
        assert text, f"{surface}: no diagnosis for an empty run"
        assert len(" ".join(text)) > 80, f"{surface}: diagnosis too thin to act on"

    # 4. The corpus path must be anchored to the repo. The shadow root is
    #    CWD-relative, so a probe trusting it directly would read a different
    #    corpus depending on where it was invoked — and report zero forever.
    assert DEFAULT_CORPUS.is_absolute(), DEFAULT_CORPUS
    assert DEFAULT_CORPUS.parent == REPO_ROOT / DEFAULT_IDENTITY_SHADOW_ROOT, DEFAULT_CORPUS

    # 5. The chat exercise must speak /chat's FLAT contract: the endpoint
    #    rejects the nested form with 422, so sending it would fail for a
    #    reason that has nothing to do with what the probe measures.
    assert PROBE_CHAT_TOOL.get("name") == "vault_lint", PROBE_CHAT_TOOL
    assert "function" not in PROBE_CHAT_TOOL, PROBE_CHAT_TOOL

    # 6. Secret precedence: an explicit flag outranks the environment.
    assert _load_secret("explicit") == "explicit"

    # 7. The block reason must classify the gate's ACTUAL wording, in both
    #    directions and without swallowing other brakes: "refused at the
    #    capability gate" and "refused for some other reason" drive opposite
    #    responses in the daily heartbeat (expected silence vs a real miss).
    assert _is_capability_denial("[denied] tool vault_write requires capabilities: vault.write")
    assert not _is_capability_denial(""), "an empty outcome is not a capability denial"
    assert not _is_capability_denial("[denied] killswitch engaged"), "a different brake must not read as the gate"
    assert not _is_capability_denial("wrote 99_Meta/identity-shadow-probe.md"), "success must not read as denial"
    assert "blocked" in _tally("mcp-bridge", []), "the tally must expose the block reason"
    return 0


def _print_report(
    base_url: str,
    corpus_path: Path,
    before: Dict[str, Any],
    after: Dict[str, Any],
    exercises: Dict[str, Dict[str, Any]],
    tallies: Dict[str, Dict[str, Any]],
) -> None:
    print(f"server        : {base_url}")
    print(f"corpus        : {corpus_path}")
    print(f"records       : {len(before['records'])} -> {len(after['records'])}")
    if after.get("malformed"):
        print(f"malformed     : {after['malformed']} (counted, not dropped)")
    print("")
    print(f"{'surface':12} {'new':>4} {'with actor':>11}  actors / states")
    for surface in exercises:
        t = tallies[surface]
        detail = ", ".join(t["actor_ids"]) if t["actor_ids"] else "-"
        states = ", ".join(t["states"]) if t["states"] else "-"
        print(f"{surface:12} {t['records']:>4} {t['with_actor']:>11}  {detail} / {states}")
    print("")
    for surface, exercise in exercises.items():
        print(f"[{surface}] HTTP {exercise['status']} — {exercise['evidence']}")
        for line in _diagnose(surface, tallies[surface], exercise):
            print(f"    {line}")
        if tallies[surface]["records"]:
            origins = ", ".join(tallies[surface]["origins"])
            verdict = "runtime-origin evidence" if ORIGIN_RUNTIME in tallies[surface]["origins"] else "NOT runtime-origin"
            print(f"    {tallies[surface]['records']} new record(s) for this surface ({origins}) — {verdict}")
        print("")
    if not any(t["records"] for t in tallies.values()):
        print(
            "No records were written. If the server IS running, check that its working\n"
            "directory is the repo root: the shadow root is CWD-relative, so a server\n"
            "started from elsewhere writes to a corpus this probe never reads."
        )
        print("")
    print("Criterion 1 is judged by: python -m msb_v3.governance identity-status")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--secret", default=None, help="x-mcp-secret (default: env, then .env)")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument(
        "--surface",
        action="append",
        choices=list(LIVE_SURFACES),
        help=f"repeatable; default all of {list(LIVE_SURFACES)}",
    )
    parser.add_argument("--strict", action="store_true", help="exit 1 if a surface produced no record")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--self-test", action="store_true", help="run the server-free self-test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            return self_test()
        except AssertionError as exc:
            print(f"self-test FAILED: {exc}", file=sys.stderr)
            return 1

    surfaces = tuple(args.surface) if args.surface else tuple(LIVE_SURFACES)

    secret = _load_secret(args.secret)
    if not secret:
        print("no MCP_BRIDGE_SECRET (flag, environment, or .env) — cannot authenticate", file=sys.stderr)
        return 2

    try:
        status, _ = _request("GET", f"{args.base_url}/health", secret, timeout=15)
    except ConnectionError as exc:
        print(f"server not reachable: {exc}", file=sys.stderr)
        return 2
    if status != 200:
        print(f"health check returned HTTP {status} — refusing to measure a server that is not up", file=sys.stderr)
        return 2

    before = load_corpus(args.corpus)

    exercises: Dict[str, Dict[str, Any]] = {}
    for surface in surfaces:
        exercises[surface] = _EXERCISES[surface](args.base_url, secret)

    after = load_corpus(args.corpus)
    new_records = after["records"][len(before["records"]):]
    tallies = {surface: _tally(surface, new_records) for surface in surfaces}
    for surface in surfaces:
        tallies[surface]["blocked"] = exercises[surface].get("blocked", "")

    if args.as_json:
        print(
            json.dumps(
                {
                    "server": args.base_url,
                    "corpus": str(args.corpus),
                    "records_before": len(before["records"]),
                    "records_after": len(after["records"]),
                    "new_records": len(new_records),
                    "surfaces": tallies,
                    "exercises": exercises,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        _print_report(args.base_url, args.corpus, before, after, exercises, tallies)

    if args.strict and any(t["records"] == 0 for t in tallies.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
