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

    sim_data = summary.get("simulation", {})
    if sim_data:
        mc = sim_data.get("monte_carlo", {})
        fc = sim_data.get("forecast", {})
        dur = mc.get("duration", {})
        fc_dur = fc.get("duration", {})
        unc = fc.get("uncertainty", {})
        if dur:
            _print_section("MONTE CARLO SIMULATION")
            print(f"  Trials: {mc['trial_count']}  (seed {mc['seed']}, {mc['elapsed_s']}s)")
            print(f"  Duration:  P50={dur['p50']:.0f}d  P80={dur['p80']:.0f}d  P95={dur['p95']:.0f}d")
            print(f"             mean={dur['mean']:.0f}d  stdev={dur['stdev']:.0f}d  CV={dur['coefficient_of_variation']}")
        if fc_dur:
            print(f"  Forecast:  {fc_dur['range']}  (uncertainty: {unc.get('level', 'unknown')})")
        print(f"  Failure:   {mc['failure_probability']:.1%} probability  ({mc['avg_failure_count']:.1f} avg/trial)")
        traj = fc.get("trajectory", "")
        rec = fc.get("recommendation", "")
        if traj:
            print(f"\n  {traj}")
        if rec:
            print(f"\n  {rec}")

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

    decisions_data = summary.get("decisions", {})
    if decisions_data:
        na = decisions_data.get("next_action", {})
        primary = na.get("primary", {})
        if primary and primary.get("description") and primary.get("description") != "no actions available":
            _print_section("NEXT-BEST-ACTION")
            print(f"  #{primary.get('rank', 1)}: {primary.get('description', '')}")
            print(f"  Score: {primary.get('score', 0)}  Category: {primary.get('category', '')}")
            print(f"  Reversibility: {primary.get('reversibility', 0):.0%}")
            print(f"  Provider: {'available' if primary.get('provider_available') else 'MISSING'} ({primary.get('provider_id', 'n/a')})")
            print(f"  Expected: {primary.get('expected_outcome', '')}")
            alt = na.get("alternatives", [])
            if alt:
                print(f"  Fallbacks: {', '.join(a.get('description', '')[:50] for a in alt[:2])}")

        to = decisions_data.get("tradeoffs", {})
        rec = to.get("recommendation", "")
        if rec:
            _print_section("TRADEOFF RECOMMENDATION")
            print(f"  {rec}")

        prov = decisions_data.get("providers", {})
        sel = prov.get("selections", [])
        if sel:
            s = sel[0]
            if s.get("rationale"):
                _print_section("PROVIDER ROUTING")
                print(f"  {s['rationale']}")

    harness_data = summary.get("harness", {})
    if harness_data.get("ready"):
        _print_section("WORK PLAN (Harness Bridge)")
        print(f"  Plan:       {harness_data['plan_id']}")
        print(f"  Action:     {harness_data['source_action']}")
        print(f"  Category:   {harness_data['category']}")
        print(f"  Steps:      {harness_data['total_steps']}")
        print(f"  Risk Tier:  {harness_data['max_risk_tier']}/4")
        print(f"  Providers:  {', '.join(harness_data.get('primary_providers', [])) or 'n/a'}")
        if harness_data.get("requires_operator_approval"):
            print("  ⚠️  Operator approval required")
        steps = harness_data.get("steps", [])
        if steps:
            print("\n  STEPS:")
            for s in steps:
                gate = "🔒" if s.get("risk_tier", 0) >= 3 else "  "
                print(f"    {gate} [{s['sequence']}] {s['description'][:80]}")
                print(f"         Provider: {s.get('preferred_provider_id', 'n/a')}  |  Tier {s.get('risk_tier', 0)}")
                vc = s.get("verification_claims", [])
                if vc:
                    print(f"         Verify: {vc[0][:70]}")
        print("\n  Run: POST /plei/execute  to execute this plan through the governed bridge")

    cal_data = summary.get("calibration", {})
    if cal_data:
        err = cal_data.get("error", {})
        sched = cal_data.get("schedule", {})
        fb = cal_data.get("feedback", {})
        _print_section("CALIBRATION (Phase 7)")
        print(f"  Pairs:     {cal_data.get('total_pairs', 0)} (predictions: {cal_data.get('total_predictions', 0)}, outcomes: {cal_data.get('total_outcomes', 0)})")
        if cal_data.get("total_pairs", 0) > 0:
            print(f"  Status:    {err.get('calibration_status', 'unknown')}")
            print(f"  MAPE:      {err.get('mape', 0):.1%}  |  Brier: {err.get('brier_score', 0):.3f}  |  ECE: {err.get('calibration_error', 0):.3f}")
            if err.get("is_overconfident"):
                print("  ⚠️  OVERCONFIDENT — predictions too narrow")
            if err.get("is_underconfident"):
                print("  ⚠️  UNDERCONFIDENT — predictions too wide")
            print(f"  Bias:      {err.get('bias_days', 0):+.1f}d  |  RMSE: {err.get('rmse_days', 0):.1f}d")
            if fb.get("description") and fb.get("description") != "no adjustments needed":
                print(f"  Feedback:  {fb['description']}")
        else:
            print("  No calibration pairs yet — run more simulations to accumulate data.")
        if sched.get("should_calibrate"):
            print(f"  ⚡ Re-calibrate: {sched.get('reason', '')}")

    print()


if __name__ == "__main__":
    main()