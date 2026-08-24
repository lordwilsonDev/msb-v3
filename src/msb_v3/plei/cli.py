#!/usr/bin/env python3
"""PLEI CLI — ``plei analyze .``

A single command that ingests a project and prints the canonical summary.
RULE: ``python -m msb_v3.plei.cli <path>`` or ``plei analyze .``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from msb_v3.plei.orchestrator import ingest_all, twin_summary


def _print_section(title: str) -> None:
    width = 60
    print(f"\n{title}")
    print("─" * min(len(title), width))


def main() -> None:
    # Handle --json flag
    as_json = "--json" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project_root = Path(args[0] if args else ".").resolve()

    print("PLEI v1.0 — Project Lifecycle Engineering Intelligence")
    print(f"Target: {project_root}")

    twin = ingest_all(project_root)
    summary = twin_summary(twin)

    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return

    # Human-readable output
    _print_section("PROJECT UNDERSTANDING")
    print(f"  Name:    {summary.get('project', 'unknown')}")
    print(f"  Version: {summary.get('version', 'unknown')}")

    lc = summary.get("lifecycle", {})
    _print_section("LIFECYCLE")
    print(f"  Stage:      {lc.get('stage', 'UNKNOWN')}")
    print(f"  Confidence: {lc.get('confidence', 0.0)}")
    for e in lc.get("evidence", []):
        print(f"    • {e}")

    arch = summary.get("architecture", {})
    _print_section("ARCHITECTURE")
    print(f"  Style:      {arch.get('style', 'unknown')}")
    components = arch.get("components", [])
    if components:
        print(f"  Components: {', '.join(components[:10])}")

    ev = summary.get("evidence", {})
    _print_section("EVIDENCE")
    print(f"  Tests:      {ev.get('test_count', 0)}")
    print(f"  Audit:      {ev.get('audit_chain_entries', 0)} entries")
    print(f"  Server:     {'healthy' if ev.get('server_healthy') else 'not probed'}")

    risks = summary.get("risks", [])
    if risks:
        _print_section("RISKS")
        for r in (risks if isinstance(risks, list) else [risks]):
            print(f"  • {r}")

    caps = summary.get("missing_capabilities", "")
    if caps and caps != "None":
        _print_section("MISSING CAPABILITIES")
        print(f"  {caps}")

    gaps_data = summary.get("gaps", {})
    if gaps_data:
        _print_section("CAPABILITY GAPS")
        coverage_pct = (
            int(gaps_data["covered"] / gaps_data["total_capabilities_required"] * 100)
            if gaps_data.get("total_capabilities_required")
            else 0
        )
        print(f"  Coverage: {gaps_data['covered']}/{gaps_data['total_capabilities_required']} "
              f"({coverage_pct}%)  "
              f"Partial: {gaps_data['partial']}  Missing: {gaps_data['missing']}")
        for g in gaps_data.get("gaps", [])[:8]:
            if g.get("status") != "COVERED":
                print(f"  [{g['criticality']}/10] {g['status']}: {g['capability']}")
                print(f"    → {g['recommendation']}")

        # Next actions
        na = gaps_data.get("next_actions", [])
        if na:
            _print_section("NEXT ACTIONS")
            for a in na[:3]:
                print(f"  • {a}")

        roles = gaps_data.get("required_roles", [])
        if roles:
            _print_section("REQUIRED ROLES")
            for r in roles:
                print(f"  [{r['discipline']}] {r['name']}: {r['description']}")

    risk_data = summary.get("risk", {})
    if risk_data:
        _print_section("RISK OVERVIEW")
        print(f"  Total risks: {risk_data.get('total_risks', 0)}")
        print(f"  Dependency risks: {risk_data.get('dependency_risks', 0)}")
        print(f"  Failure modes: {risk_data.get('failure_modes', 0)}")
        print(f"  Debt items: {risk_data.get('debt_items', 0)}")
        tr = risk_data.get("top_risks", [])
        if tr:
            print()
            print("  TOP RISKS")
            for r in tr[:5]:
                print(f"  [{r['risk_score']:.1f}] [{r['source']}] {r['description']}")
        cp = risk_data.get("critical_path", [])
        if cp:
            print()
            print(f"  Critical path: {' → '.join(cp)}")
        bns = risk_data.get("bottlenecks", [])
        if bns:
            print()
            print("  Bottlenecks:")
            for bn in bns[:3]:
                print(f"    {bn['module']} (fan-in: {bn['fan_in']})")

    print()


if __name__ == "__main__":
    main()