#!/usr/bin/env python3
"""Read-only MSB-v3 repository architecture/forensics harness.

This is deliberately not a new runtime orchestrator. It inventories the tree,
reconstructs the canonical governed path, parses the surface map and ADRs,
extracts recent git decision commits, and computes a lightweight Python
package import graph.

Usage:
  python scripts/msb-system-harness.py --check --markdown
  python scripts/msb-system-harness.py --out-dir artifacts/system-harness

The harness never mutates source code, runtime databases, git refs, or operator state.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "artifacts" / "system-harness"

CANONICAL_PATHS = (
    "src/msb_v3/agent/handle.py",
    "src/msb_v3/agent/safety.py",
    "src/msb_v3/agent/executor.py",
    "src/msb_v3/tools/registry.py",
    "src/msb_v3/tools/runtime.py",
    "src/msb_v3/governance/capability_registry.py",
    "src/msb_v3/governance/capability_resolver.py",
    "src/msb_v3/gateway/route.py",
    "src/msb_v3/evidence/receipt.py",
    "src/msb_v3/evidence/spine.py",
    "src/msb_v3/replay/engine.py",
    "src/msb_v3/observability/audit_log.py",
)

REQUIRED_DOCS = (
    "README.md",
    "CLAUDE.md",
    "docs/SURFACE.md",
    "docs/architecture.md",
    "docs/what-msb-v3-is.md",
    "docs/releases/MSB-v3-RELEASE.md",
    "PLAN.md",
)

IMPORTANT_TESTS = (
    "tests/governance/test_bypass.py",
    "tests/governance/test_capability_registry.py",
    "tests/contracts/test_gate_contract.py",
    "tests/uac/test_audit_chain.py",
    "tests/uac/test_merkle.py",
    "tests/docs/test_surface_map.py",
    "tests/integrations/test_cli_provider_isolation.py",
)

@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    subject: str
    evidence: list[str]
    interpretation: str

@dataclass(frozen=True)
class PackageInfo:
    name: str
    path: str
    files: int
    bytes: int
    python_modules: int
    classification: str

def run_git(*args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip()

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def parse_version() -> str | None:
    try:
        import tomllib
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        return data.get("project", {}).get("version")
    except Exception:
        return None

def parse_surface() -> dict[str, str]:
    path = ROOT / "docs" / "SURFACE.md"
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        match = re.match(
            r"\|\s*\`?msb_v3/([^\s\`|]+)\`?\s*\|\s*([^|]+)\|",
            line,
        )
        if match:
            result[match.group(1).rstrip("/")] = match.group(2).strip().upper()
    return result

def inventory_packages(surface: dict[str, str]) -> list[PackageInfo]:
    base = ROOT / "src" / "msb_v3"
    result: list[PackageInfo] = []
    if not base.is_dir():
        return result
    for pkg in sorted(p for p in base.iterdir() if p.is_dir()):
        files = [p for p in pkg.rglob("*") if p.is_file()]
        result.append(
            PackageInfo(
                name=pkg.name,
                path=f"src/msb_v3/{pkg.name}",
                files=len(files),
                bytes=sum(p.stat().st_size for p in files),
                python_modules=sum(1 for p in files if p.suffix == ".py"),
                classification=surface.get(pkg.name, "UNMAPPED"),
            )
        )
    return result

def parse_adrs() -> list[dict[str, Any]]:
    base = ROOT / "docs" / "adr"
    rows: list[dict[str, Any]] = []
    if not base.is_dir():
        return rows
    for path in sorted(base.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        def field(label: str) -> str | None:
            match = re.search(
                rf"^\*\*{re.escape(label)}\*\*:\s*(.+)$",
                text,
                re.MULTILINE,
            )
            return match.group(1).strip() if match else None
        title = next(
            (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")),
            path.stem,
        )
        rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "title": title,
                "status": field("Status"),
                "date": field("Date"),
                "decision_maker": field("Decision Maker") or field("Deciders"),
                "sha256": sha256(path),
            }
        )
    return rows

def parse_blueprints() -> list[dict[str, Any]]:
    base = ROOT / "docs" / "blueprints"
    rows: list[dict[str, Any]] = []
    if not base.is_dir():
        return rows
    for path in sorted(base.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        status = re.search(r"\bStatus:\s*([A-Z][A-Z_ -]*)", text)
        lower = text.lower()
        rows.append(
            {
                "file": str(path.relative_to(ROOT)),
                "status": status.group(1).strip() if status else None,
                "explicitly_proposed_or_unbuilt": (
                    "nothing in this document is built" in lower
                    or "not built" in lower
                    or "proposed" in lower
                ),
            }
        )
    return rows

def recent_commits(limit: int = 50) -> list[dict[str, str]]:
    raw = run_git(
        "log",
        f"-n{limit}",
        "--date=iso-strict",
        "--pretty=format:%H%x09%ad%x09%s",
    )
    if raw is None:
        return []
    result = []
    for line in raw.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            result.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return result

def import_graph() -> dict[str, Any]:
    base = ROOT / "src" / "msb_v3"
    edges: Counter[tuple[str, str]] = Counter()
    if not base.is_dir():
        return {"edges": [], "inbound": {}, "outbound": {}}

    def package_for(path: Path) -> str:
        rel = path.relative_to(base)
        return rel.parts[0] if rel.parts else path.stem

    for path in base.rglob("*.py"):
        source = package_for(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if not name.startswith("msb_v3."):
                    continue
                target = name.split(".")[1]
                if target != source:
                    edges[(source, target)] += 1

    inbound: Counter[str] = Counter()
    outbound: Counter[str] = Counter()
    for (source, target), count in edges.items():
        outbound[source] += count
        inbound[target] += count

    return {
        "edges": [
            {"from": source, "to": target, "imports": count}
            for (source, target), count in sorted(edges.items(), key=lambda x: (-x[1], x[0]))
        ],
        "inbound": dict(inbound.most_common()),
        "outbound": dict(outbound.most_common()),
    }

def findings(packages: list[PackageInfo], blueprints: list[dict[str, Any]]) -> list[Finding]:
    out: list[Finding] = []

    for rel in CANONICAL_PATHS:
        if not (ROOT / rel).exists():
            out.append(Finding(
                "canonical-path-missing", "ERROR", rel, [rel],
                "A required canonical-path component is absent.",
            ))

    for rel in REQUIRED_DOCS:
        if not (ROOT / rel).exists():
            out.append(Finding(
                "documentation-missing", "WARN", rel, [rel],
                "An architecture or release reference is missing.",
            ))

    for pkg in packages:
        if pkg.classification == "UNMAPPED":
            out.append(Finding(
                "surface-drift", "ERROR", pkg.path, ["docs/SURFACE.md", pkg.path],
                "Package exists under src/msb_v3 but is not classified by the surface map.",
            ))

    if not (ROOT / "CONTEXT.md").exists():
        out.append(Finding(
            "domain-context-missing", "WARN", "CONTEXT.md",
            ["docs/agents/domain.md", "CONTEXT.md"],
            "The repo convention requests one root CONTEXT.md; the current tree did not contain one.",
        ))

    proposed = [item["file"] for item in blueprints if item["explicitly_proposed_or_unbuilt"]]
    out.append(Finding(
        "blueprint-boundary", "INFO", "docs/blueprints",
        proposed[:12],
        f"{len(proposed)} blueprint documents contain proposed/unbuilt language; treat them as design evidence, not implementation proof.",
    ))
    return out

def report() -> dict[str, Any]:
    packages = inventory_packages(parse_surface())
    blueprints = parse_blueprints()
    return {
        "schema": "msb-system-harness/v1",
        "generated_at": run_git("show", "-s", "--format=%cI", "HEAD") or "",
        "repo": {
            "path": str(ROOT),
            "version": parse_version(),
            "head_sha": run_git("rev-parse", "HEAD"),
            "branch": run_git("branch", "--show-current"),
            "dirty": bool(run_git("status", "--porcelain")),
        },
        "counts": {
            root: {
                "files": len([p for p in (ROOT / root).rglob("*") if p.is_file()]) if (ROOT / root).exists() else 0,
                "bytes": sum(p.stat().st_size for p in (ROOT / root).rglob("*") if p.is_file()) if (ROOT / root).exists() else 0,
            }
            for root in ("src", "tests", "docs", "scripts", "experiments")
        },
        "canonical_path": list(CANONICAL_PATHS),
        "packages": [asdict(item) for item in packages],
        "surface_map": parse_surface(),
        "import_graph": import_graph(),
        "adrs": parse_adrs(),
        "blueprints": blueprints,
        "recent_commits": recent_commits(),
        "evidence": {
            "important_tests": {rel: (ROOT / rel).exists() for rel in IMPORTANT_TESTS},
            "claims_gate": (ROOT / "scripts" / "verify-claims.py").exists(),
            "production_gate": (ROOT / "scripts" / "production_gate.py").exists(),
            "harness_gate_consumer": (ROOT / "scripts" / "ci-harness-gate.sh").exists(),
            "conversation_e2e_spec": (ROOT / "docs" / "conversation-e2e-harness-v1.md").exists(),
        },
        "findings": [asdict(item) for item in findings(packages, blueprints)],
        "interpretation_rules": {
            "unknown_is_not_safe": True,
            "model_is_not_authority": True,
            "proposed_is_not_implemented": True,
            "test_is_not_whole_system_proof": True,
        },
    }

def markdown(data: dict[str, Any]) -> str:
    lines = [
        "# MSB-v3 System Harness Report",
        "",
        f"Version: {data['repo']['version']}",
        f"HEAD: {data['repo']['head_sha']}",
        f"Branch: {data['repo']['branch']}",
        f"Dirty: {data['repo']['dirty']}",
        "",
        "## Canonical control path",
        "",
        "request -> intent -> task DAG -> capability/authorization",
        "-> governed tools -> verification -> evidence -> audit ledger -> replay",
        "",
        "## System inventory",
        "",
        "| Package | Class | Python | Files | Bytes |",
        "|---|---|---:|---:|---:|",
    ]
    for item in data["packages"]:
        lines.append(
            f"| {item['path']} | {item['classification']} | {item['python_modules']} | {item['files']} | {item['bytes']:,} |"
        )

    lines += [
        "",
        "## Authority map",
        "",
        "| Layer | Components | Role |",
        "|---|---|---|",
        "| Intelligence | moie, plei, factory, triumvirate | propose / classify / review |",
        "| Authority | governance, agent/safety.py, gateway, vesta | authorize / deny |",
        "| Execution | agent, tools, local_ai, providers | bounded work |",
        "| Evidence | evidence, uac, msb_ledger, replay | reconstruct / verify |",
        "| Operations | cron, wake, ops, runtime | supervise / recover |",
        "",
        "## High-connectivity packages",
        "",
    ]
    for name, count in list(data["import_graph"]["inbound"].items())[:12]:
        lines.append(f"- {name}: {count} inbound internal imports")

    lines += ["", "## Findings", ""]
    for item in data["findings"]:
        lines.append(f"- {item['severity']} {item['kind']} {item['subject']}: {item['interpretation']}")
    return "\n".join(lines) + "\n"

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    data = report()
    output_dir = args.out_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "system-map.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.markdown:
        (output_dir / "system-map.md").write_text(markdown(data), encoding="utf-8")

    errors = [item for item in data["findings"] if item["severity"] == "ERROR"]
    print(f"[msb-system-harness] packages={len(data['packages'])} errors={len(errors)} out={output_dir}")
    for item in data["findings"]:
        print(f"[msb-system-harness] {item['severity']}: {item['kind']}: {item['subject']}")
    return 1 if args.check and errors else 0

if __name__ == "__main__":
    raise SystemExit(main())
