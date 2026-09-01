"""M6/M7 closures: machine-readable evidence links + the human read-path.

M6 — every CheckResult carries ``EvidenceLink``s (path:line:snippet)
behind its verdict, and ``save_result`` persists them, so downstream
tooling can consume evidence without parsing prose.
M7 — ``render_report`` turns a run into actionable markdown: CHECK
findings get an explicit investigation path (where to look, via the M6
links), CONFLICTING verdicts get both sides of the evidence, and each
verdict gets its routing guidance.
"""

from __future__ import annotations

import json
from pathlib import Path

from msb_v3.wrongness.checks import (
    check_call_sites,
    check_porcelain,
    check_tracked_secret,
)
from msb_v3.wrongness.claims import CheckSpec, Claim
from msb_v3.wrongness.engine import WrongnessEngine, save_result
from msb_v3.wrongness.report import render_report


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _commit_all(repo: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "wip"], cwd=repo, check=True, capture_output=True)


# --- M6: evidence links ------------------------------------------------------


def test_call_sites_links_carry_path_line_snippet(temp_repo: Path) -> None:
    _write(temp_repo, "src/a.py", "def f():\n    return 1\n")
    _write(temp_repo, "src/b.py", "import os\n\nfrom a import f\nx = f()\n")
    _commit_all(temp_repo)
    res = check_call_sites(temp_repo, "f", min_count=2)
    assert res.ok is True
    by_path = {link.path: link for link in res.links}
    assert "src/a.py" in by_path and by_path["src/a.py"].line == 1
    assert "src/b.py" in by_path and by_path["src/b.py"].line == 3  # the import line
    assert "from a import f" in (by_path["src/b.py"].snippet or "")


def test_tracked_secret_links_point_at_the_leak_line(temp_repo: Path) -> None:
    _write(temp_repo, "settings.json", '{\n  "key": "tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"\n}\n')
    _commit_all(temp_repo)
    res = check_tracked_secret(temp_repo, r"tvly-[A-Za-z0-9_-]{20,}")
    assert res.ok is False
    assert len(res.links) == 1
    link = res.links[0]
    assert link.path == "settings.json"
    assert link.line == 2
    assert "tvly-dev" in (link.snippet or "")


def test_porcelain_links_list_dirty_paths(temp_repo: Path) -> None:
    _write(temp_repo, "a.txt", "x\n")
    _commit_all(temp_repo)
    (temp_repo / "a.txt").write_text("y\n", encoding="utf-8")
    _write(temp_repo, "new.txt", "n\n")
    res = check_porcelain(temp_repo)
    assert res.ok is False
    paths = {link.path for link in res.links}
    assert "a.txt" in paths  # unstaged edit
    assert "new.txt" in paths  # untracked


def test_save_result_persists_links(temp_repo: Path) -> None:
    _write(temp_repo, "settings.json", '{"key": "tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}\n')
    _commit_all(temp_repo)
    claim = Claim(
        id="t-secret",
        statement="no plaintext secrets are tracked",
        domain="test",
        checks=(CheckSpec("tracked_secret", {"pattern": r"tvly-[A-Za-z0-9_-]{20,}"}),),
    )
    result = WrongnessEngine(temp_repo).run(claim)
    out = temp_repo / "out.json"
    save_result(result, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["verdict"] == "ESCALATE"
    links = data["checks"][0]["links"]
    assert len(links) == 1
    assert links[0]["path"] == "settings.json"
    assert links[0]["line"] == 1


# --- M7: human read-path -----------------------------------------------------


def test_report_investigation_path_lists_inconclusive_evidence(temp_repo: Path) -> None:
    """A CHECK verdict (inconclusive check) must point the human somewhere."""
    claim = Claim(
        id="t-inconclusive",
        statement=".env is 0600",
        domain="test",
        consequence="high",
        checks=(CheckSpec("file_mode", {"path": "missing.env", "expected": "0600"}),),
    )
    result = WrongnessEngine(temp_repo).run(claim)
    assert result.verdict == "CHECK"
    text = render_report(result, repo_root=str(temp_repo))
    assert "Investigation path (CHECK findings)" in text
    assert "look at" in text
    assert "missing.env" in text  # the M6 link drives the read-path
    assert "route below escalation" in text  # CHECK guidance: don't block


def test_report_conflicting_verdict_presents_both_sides(temp_repo: Path) -> None:
    """CONFLICTING: supporting evidence stands AND a check refutes."""
    _write(temp_repo, "x", "1\n")
    (temp_repo / "x").chmod(0o644)
    claim = Claim(
        id="t-conflict",
        statement="x is locked down",
        domain="test",
        consequence="medium",
        supporting_evidence=("the locking PR merged and shipped",),
        checks=(CheckSpec("file_mode", {"path": "x", "expected": "0600"}),),
    )
    result = WrongnessEngine(temp_repo).run(claim)
    assert result.verdict == "CONFLICTING"
    text = render_report(result, repo_root=str(temp_repo))
    assert "both ways" in text  # the human-decides guidance
    assert "the locking PR merged and shipped" in text  # confirming side
    assert "x: mode 0644" in text  # refuting side
    assert "Consensus" in text  # M4 consensus view surfaced


def test_report_escalate_verdict_says_block(temp_repo: Path) -> None:
    _write(temp_repo, "settings.json", '{"key": "tvly-dev-AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}\n')
    _commit_all(temp_repo)
    claim = Claim(
        id="t-escalate",
        statement="no plaintext secrets are tracked",
        domain="test",
        checks=(CheckSpec("tracked_secret", {"pattern": r"tvly-[A-Za-z0-9_-]{20,}"}),),
    )
    result = WrongnessEngine(temp_repo).run(claim)
    assert result.verdict == "ESCALATE"
    text = render_report(result, repo_root=str(temp_repo))
    assert "failure-assertion" in text
    assert "settings.json:1" in text  # the link with its line, rendered
