"""Deterministic falsification checks — the "5 lines of shell" power.

The by-hand run's strongest lesson: a falsifiable claim's best check is
usually trivial (call-site count, stat mode, tracked-path coverage,
porcelain state, type inspection).  These checks are the external
adjudicator the doc says the recursion must terminate at.  No LLM, no
network — stdlib only.

M6 (``03_Inversion-Audit.md``): every CheckResult carries
``EvidenceLink``s — structured ``path:line:snippet`` pointers behind the
verdict, so downstream tooling (and the M7 human report) can consume the
evidence without parsing prose.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .claims import CheckSpec, Claim


@dataclass(frozen=True)
class EvidenceLink:
    """A machine-readable pointer to the evidence behind a check (M6).

    ``path`` is repo-relative where the check can know it (call sites,
    tracked files, results JSON) and absolute otherwise (the packaged
    corpus).  ``line`` and ``snippet`` are the first match location when
    the check scans file contents.
    """

    path: str
    line: int | None = None
    snippet: str | None = None


@dataclass(frozen=True)
class CheckResult:
    """Outcome of running one deterministic check."""

    ok: bool | None  # True = claim holds, False = falsified, None = couldn't run
    evidence: str
    check: str
    links: tuple[EvidenceLink, ...] = field(default_factory=tuple)


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if out.returncode != 0:
        return ""
    return out.stdout


def _tracked_files(root: Path) -> list[str]:
    tracked = _git(root, "ls-files", "-z")
    if tracked:
        return tracked.split("\x00")
    # No git worktree (the portability gate stages a .git-less copy, prunes
    # docs/, and the claim replay can run against arbitrary trees): fall back
    # to walking the tree, pruning VCS/venv/cache dirs so the scan stays
    # deterministic and mirrors what git would have tracked.
    excluded = {
        ".git", "__pycache__", ".pytest_cache", ".mypy_cache",
        ".venv", "venv", "node_modules", ".direnv",
    }
    rels: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in excluded)
        for fn in sorted(filenames):
            if fn.endswith((".pyc", ".pyo")):
                continue
            p = Path(dirpath) / fn
            if p.is_file() and not p.is_symlink():
                try:
                    rels.append(str(p.relative_to(root)))
                except ValueError:  # pragma: no cover - defensive
                    pass
    return rels


def _first_match_links(root: Path, rel: str, pattern: re.Pattern[str]) -> EvidenceLink | None:
    """First line of ``rel`` matching ``pattern`` as an EvidenceLink."""
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:  # pragma: no cover - defensive
        return None
    for lineno, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return EvidenceLink(path=rel, line=lineno, snippet=line.strip()[:120])
    return None


def check_call_sites(root: Path, symbol: str, max_count: int | None = None, min_count: int | None = None) -> CheckResult:
    """Count definition/reference sites of ``symbol`` across tracked sources.

    ``max_count``: claim holds when call sites are scarce (dead code probe).
    ``min_count``: claim holds when the symbol is actually called somewhere
    (C5 — a "shipped capability" must have call sites beyond its own def).
    """
    files = [p for p in _tracked_files(root) if p.endswith(".py")]
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    hits: list[str] = []
    links: list[EvidenceLink] = []
    for rel in files:
        link = _first_match_links(root, rel, pattern)
        if link is not None:
            hits.append(rel)
            links.append(link)
    n = len(hits)
    ok: bool | None
    if min_count is not None:
        ok = n >= min_count
        bound = f"min={min_count}"
    elif max_count is not None:
        ok = None if n == 0 else n <= max_count
        bound = f"max={max_count}"
    else:
        ok = None
        bound = "count"
    return CheckResult(
        ok=ok,
        evidence=f"call-site audit: {symbol!r} in {n} tracked file(s): {', '.join(hits) or 'none'}",
        check=f"call_sites({symbol}, {bound})",
        links=tuple(links),
    )


def check_file_mode(root: Path, path: str, expected: str) -> CheckResult:
    """Stat a file and compare its mode against an octal string like ``0600``."""
    target = root / path
    if not target.exists():
        return CheckResult(
            ok=None,
            evidence=f"{path}: file does not exist",
            check=f"stat({path})",
            links=(EvidenceLink(path=path),),
        )
    mode = stat.S_IMODE(target.stat().st_mode)
    actual = f"{mode:04o}"
    return CheckResult(
        ok=actual == expected,
        evidence=f"{path}: mode {actual} (expected {expected})",
        check=f"stat({path}) == {expected}",
        links=(EvidenceLink(path=path),),
    )


def check_tracked_secret(root: Path, pattern: str) -> CheckResult:
    """Scan tracked files for a secret-shaped pattern (the H4 blind spot)."""
    needle = re.compile(pattern)
    found: list[str] = []
    links: list[EvidenceLink] = []
    for rel in _tracked_files(root):
        p = root / rel
        if not p.is_file():
            continue
        link = _first_match_links(root, rel, needle)
        if link is not None:
            found.append(rel)
            links.append(link)
    if not found:
        return CheckResult(
            ok=True,
            evidence=f"secret scan: no tracked file matches {pattern!r}",
            check=f"tracked_secret({pattern})",
        )
    return CheckResult(
        ok=False,
        evidence=f"secret scan: {len(found)} tracked file(s) match {pattern!r}: {', '.join(found[:5])}",
        check=f"tracked_secret({pattern})",
        links=tuple(links),
    )


def check_scorecard_gate(root: Path, results_dir: str, round_file: str, model: str, baseline: str, margin: float = 0.05, min_speedup: float | None = None) -> CheckResult:
    """Fleet bake-off gate: specialist must stay within ``margin`` of baseline
    score AND (optionally) be ``min_speedup``x faster.

    Reads ``results/<round_file>`` (list of rows with ``model``, ``score``,
    ``mean_latency_s``).  This is the live claim's adjudicator — the same
    go/no-go rule the scorecard enforces.
    """
    rel = f"{results_dir}/{round_file}"
    path = root / rel
    if not path.exists():
        return CheckResult(ok=None, evidence=f"scorecard {round_file} not found at {path}", check=f"scorecard_gate({round_file})", links=(EvidenceLink(path=rel),))
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    base = next((r for r in rows if r.get("model") == baseline), None)
    cand = next((r for r in rows if r.get("model") == model), None)
    if base is None or cand is None:
        return CheckResult(ok=None, evidence=f"scorecard {round_file}: baseline {baseline!r} or model {model!r} missing", check=f"scorecard_gate({round_file})", links=(EvidenceLink(path=rel),))
    gap = cand.get("score", 0.0) - base.get("score", 0.0)
    speedup = None
    if min_speedup is not None and base.get("mean_latency_s") and cand.get("mean_latency_s"):
        speedup = base["mean_latency_s"] / cand["mean_latency_s"]
    ok = gap >= -margin and (speedup is None or speedup >= min_speedup)
    parts = [f"{model} score {cand.get('score')} vs baseline {base.get('score')} (gap {gap:+.3f}, margin {margin})"]
    if speedup is not None:
        parts.append(f"speedup {speedup:.2f}x (need {min_speedup}x)")
    return CheckResult(ok=ok, evidence="; ".join(parts), check=f"scorecard_gate({round_file})", links=(EvidenceLink(path=rel),))


def _porcelain_path(entry: str) -> str:
    """Strip the XY status columns and rename arrows from a porcelain line."""
    rest = entry[3:].strip()
    if " -> " in rest:
        rest = rest.split(" -> ")[-1]
    return rest


def check_porcelain(root: Path) -> CheckResult:
    """Working-tree state: staged-vs-unstaged blindness is the JOB-006 gap.

    Porcelain semantics: ``XY path`` where X is the index (staged) column and
    Y the worktree (unstaged) column.  ``??`` is untracked.  Staged-only is
    the healthy pre-commit state; unstaged edits or untracked files are the
    dirty states that a staged-vs-unstaged-blind check conflates.
    """
    out = _git(root, "status", "--porcelain")
    entries = [ln for ln in out.splitlines() if ln.strip()]
    if not entries:
        return CheckResult(ok=True, evidence="porcelain: clean", check="git status --porcelain")
    staged = [e for e in entries if e[0] not in (" ", "?")]
    unstaged = [e for e in entries if e[1] not in (" ", "?")]
    untracked = [e for e in entries if e[:2] == "??"]
    dirty = unstaged + untracked
    links = tuple(EvidenceLink(path=_porcelain_path(e)) for e in dirty)
    return CheckResult(
        ok=not dirty,
        evidence=(
            f"porcelain: {len(entries)} entr(ies) — staged {len(staged)}, "
            f"unstaged {len(unstaged)}, untracked {len(untracked)}"
        ),
        check="git status --porcelain",
        links=links,
    )


def check_round_score(
    root: Path,
    results_dir: str,
    round_file: str,
    model: str | None = None,
    field: str = "score",
    min_score: float = 0.9,
) -> CheckResult:
    """Round result gate: a named model's ``field`` value must be >= ``min_score``.

    The router gate for the fleet claim (M5): round0_setfit.json records
    the SetFit router at 0.964 CV accuracy, and the claim's condition is
    "router accuracy >= 0.90 on held-out routing data".
    """
    rel = f"{results_dir}/{round_file}"
    path = root / rel
    if not path.exists():
        return CheckResult(ok=None, evidence=f"round {round_file} not found at {path}", check=f"round_score({round_file})", links=(EvidenceLink(path=rel),))
    rows = json.loads(path.read_text(encoding="utf-8")).get("rows", [])
    row = next((r for r in rows if r.get("model") == model), None) if model else (rows[0] if rows else None)
    if row is None:
        return CheckResult(ok=None, evidence=f"round {round_file}: model {model!r} missing", check=f"round_score({round_file})", links=(EvidenceLink(path=rel),))
    value = row.get(field)
    if value is None:
        return CheckResult(ok=None, evidence=f"round {round_file}: field {field!r} missing", check=f"round_score({round_file})", links=(EvidenceLink(path=rel),))
    ok = value >= min_score
    return CheckResult(
        ok=ok,
        evidence=f"round {round_file}: {model or 'first row'} {field} {value} (need >= {min_score})",
        check=f"round_score({round_file})",
        links=(EvidenceLink(path=rel),),
    )


def check_corpus_replay(
    root: Path,
    corpus_path: str | None = None,
    min_pedr: float = 0.95,
    require_decision: str = "VALIDATED",
) -> CheckResult:
    """The self-claim's adjudicator (M1): replay the by-hand corpus and
    require the recorded verdict to reproduce.

    The engine runs on itself: the deterministic check for the claim "the
    engine reproduces the by-hand verdict" IS the engine replaying its own
    corpus.  If the corpus regresses, this check fails and the engine
    escalates its own validity claim.  Import is deferred to avoid the
    engine<->checks import cycle.
    """
    from .engine import load_corpus, run_replay  # deferred: breaks the cycle

    path = Path(corpus_path) if corpus_path else Path(__file__).parent / "corpus" / "byhand_21.json"
    link = EvidenceLink(path=str(path))
    if not path.exists():
        return CheckResult(ok=None, evidence=f"corpus {path} not found", check=f"corpus_replay({path.name})", links=(link,))
    try:
        corpus = load_corpus(path)
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(ok=None, evidence=f"corpus {path.name} unreadable: {exc}", check=f"corpus_replay({path.name})", links=(link,))
    score = run_replay(corpus)  # recorded-routing replay: reproduces the by-hand numbers
    ok = score.pedr >= min_pedr and score.decision == require_decision
    return CheckResult(
        ok=ok,
        evidence=(
            f"replay of {path.name}: PEDR {score.pedr:.3f} ({score.predicted_failures}/{score.actual_failures}), "
            f"FP assertion {score.fp_rate_assertion:.3f}, decision {score.decision} "
            f"(need PEDR >= {min_pedr}, decision {require_decision})"
        ),
        check=f"corpus_replay({path.name})",
        links=(link,),
    )


def check_claims_valid(root: Path, claims_dir: str) -> CheckResult:
    """Schema-conformance gate for a directory of authored claims (M8).

    Every ``*.json`` in ``claims_dir`` (underscore-prefixed files excluded —
    that's the ``_TEMPLATE.json`` convention) must parse through
    ``Claim.from_dict``.  This is the authoring hook: claims live where they
    are authored (the vault), and the engine's own schema — defined in
    ``claims.py`` — is the adjudicator, so a malformed claim fails fast
    instead of silently producing a NOTE verdict.
    """
    base = root / claims_dir
    if not base.exists():
        return CheckResult(
            ok=None,
            evidence=f"claims dir {claims_dir!r} not found at {base}",
            check=f"claims_valid({claims_dir})",
            links=(EvidenceLink(path=claims_dir),),
        )
    files = sorted(f for f in base.glob("*.json") if not f.name.startswith("_"))
    bad: list[str] = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            Claim.from_dict(data)
        except Exception as exc:
            bad.append(f"{f.name}: {exc}")
    detail = f"{len(files)} claim(s) in {claims_dir}, {len(bad)} invalid"
    if bad:
        detail += ": " + "; ".join(bad[:5])
    return CheckResult(
        ok=not bad,
        evidence=f"claims_valid: {detail}",
        check=f"claims_valid({claims_dir})",
        links=tuple(EvidenceLink(path=f"{claims_dir}/{f.name}") for f in files),
    )


def check_type_flow(root: Path, call: str) -> CheckResult:
    """Inspect the types flowing into a call site (D1: list-as-int => 0)."""
    files = [p for p in _tracked_files(root) if p.endswith(".py")]
    pattern = re.compile(rf"\b{re.escape(call)}\b")
    links: list[EvidenceLink] = []
    for rel in files:
        link = _first_match_links(root, rel, pattern)
        if link is not None:
            links.append(link)
    return CheckResult(
        ok=None,
        evidence=f"type-flow probe: {call!r} referenced in {len(links)} file(s): {', '.join(link.path for link in links) or 'none'} — inspect the actual argument types",
        check=f"type_flow({call})",
        links=tuple(links),
    )


_REGISTRY: dict[str, Callable[..., CheckResult]] = {
    "call_sites": lambda root, **kw: check_call_sites(root, **kw),
    "file_mode": lambda root, **kw: check_file_mode(root, **kw),
    "tracked_secret": lambda root, **kw: check_tracked_secret(root, **kw),
    "porcelain": lambda root: check_porcelain(root),
    "type_flow": lambda root, **kw: check_type_flow(root, **kw),
    "scorecard_gate": lambda root, **kw: check_scorecard_gate(root, **kw),
    "round_score": lambda root, **kw: check_round_score(root, **kw),
    "corpus_replay": lambda root, **kw: check_corpus_replay(root, **kw),
    "claims_valid": lambda root, **kw: check_claims_valid(root, **kw),
}


def run_check(spec: CheckSpec, root: Path) -> CheckResult:
    """Dispatch a CheckSpec to its executor."""
    fn = _REGISTRY.get(spec.kind)
    if fn is None:
        return CheckResult(
            ok=None,
            evidence=f"no executor for check kind {spec.kind!r}",
            check=spec.kind,
        )
    try:
        return fn(root=root, **spec.params)
    except Exception as exc:  # pragma: no cover - defensive
        return CheckResult(ok=None, evidence=f"check errored: {exc}", check=spec.kind)
