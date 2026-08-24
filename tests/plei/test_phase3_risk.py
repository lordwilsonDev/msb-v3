"""PLEI Phase 3 tests — dependency graph, failure model, debt model, risk report.

Tests the three new modules against msb-v3 as the target project.
"""

from __future__ import annotations

from pathlib import Path

from msb_v3.plei.dependency.graph import (
    DependencyGraph,
    build_dependency_graph,
    dependency_graph_as_dict,
)
from msb_v3.plei.orchestrator import ingest_all
from msb_v3.plei.risk.debt_model import (
    DebtReport,
    debt_report_as_dict,
    score_debt,
)
from msb_v3.plei.risk.failure_model import (
    FailureReport,
    analyze_failures,
    failure_report_as_dict,
)
from msb_v3.plei.risk.report import (
    RiskReport,
    analyze_risk,
    risk_report_as_dict,
)

ROOT = Path(__file__).resolve().parents[2]


# --- Dependency Graph ---

def test_build_dependency_graph_has_nodes():
    graph = build_dependency_graph(ROOT)
    assert isinstance(graph, DependencyGraph)
    assert graph.node_count > 0, f"Should have nodes: {graph.node_count}"
    assert "msb_v3" in graph.nodes, f"Nodes: {list(graph.nodes.keys())}"
    assert "msb_ledger" in graph.nodes


def test_dependency_graph_has_edges():
    graph = build_dependency_graph(ROOT)
    assert graph.edge_count > 0, "Should have internal dependency edges"


def test_dependency_graph_computes_bottlenecks():
    graph = build_dependency_graph(ROOT)
    assert len(graph.bottlenecks) >= 1, "Should identify at least one bottleneck"
    bn = graph.bottlenecks[0]
    assert "module" in bn
    assert "fan_in" in bn
    assert bn["fan_in"] > 0


def test_dependency_graph_computes_coupling_score():
    graph = build_dependency_graph(ROOT)
    assert 0.0 <= graph.coupling_score <= 1.0, f"Coupling out of range: {graph.coupling_score}"


def test_dependency_graph_as_dict_is_json_safe():
    graph = build_dependency_graph(ROOT)
    d = dependency_graph_as_dict(graph)
    import json
    json.dumps(d)
    assert "nodes" in d
    assert "critical_path" in d
    assert "bottlenecks" in d


# --- Debt Model ---

def test_score_debt_produces_report():
    twin = ingest_all(ROOT)
    report = score_debt(twin)
    assert isinstance(report, DebtReport)
    assert report.total_items >= 7, f"Should have at least 7 known debt items: {report.total_items}"
    assert report.total_priority > 0, "Total priority should be non-zero"
    assert len(report.top_5) == 5 or len(report.top_5) == report.total_items


def test_debt_sorted_by_priority():
    twin = ingest_all(ROOT)
    report = score_debt(twin)
    priorities = [i.priority for i in report.items]
    assert priorities == sorted(priorities, reverse=True), "Debt must be sorted by priority descending"


def test_debt_by_class_has_categories():
    twin = ingest_all(ROOT)
    report = score_debt(twin)
    cats = list(report.by_class.keys())
    assert "Operational" in cats or "Security" in cats, f"Categories: {cats}"


def test_debt_report_as_dict_is_json_safe():
    twin = ingest_all(ROOT)
    report = score_debt(twin)
    d = debt_report_as_dict(report)
    import json
    json.dumps(d)
    assert "top_5" in d
    assert "by_class" in d


# --- Failure Model ---

def test_analyze_failures_finds_static_modes():
    twin = ingest_all(ROOT)
    report = analyze_failures(twin)
    assert isinstance(report, FailureReport)
    assert report.total_modes >= 3, f"Should find at least 3 failure modes: {report.total_modes}"
    kinds = {m.kind for m in report.modes}
    assert "disk_saturation" in kinds or "single_point_of_failure" in kinds


def test_failure_modes_ranked_by_risk():
    twin = ingest_all(ROOT)
    report = analyze_failures(twin)
    scores = [m.severity * m.likelihood for m in report.modes]
    assert scores == sorted(scores, reverse=True), "Failure modes must be ranked by risk"


def test_failure_report_as_dict_is_json_safe():
    twin = ingest_all(ROOT)
    report = analyze_failures(twin)
    d = failure_report_as_dict(report)
    import json
    json.dumps(d)
    assert "modes" in d
    assert "risk_distribution" in d


# --- Unified Risk Report ---

def test_analyze_risk_combines_all_layers():
    twin = ingest_all(ROOT)
    report = analyze_risk(twin)
    assert isinstance(report, RiskReport)
    assert report.dependency_risks > 0
    assert report.failure_modes >= 3
    assert report.debt_items >= 7
    assert len(report.top_risks) >= 3, "Should have at least 3 top risks"


def test_risk_report_sorted_by_score():
    twin = ingest_all(ROOT)
    report = analyze_risk(twin)
    scores = [r.risk_score for r in report.top_risks]
    assert scores == sorted(scores, reverse=True), "Top risks must be sorted by risk_score"


def test_risk_report_as_dict_is_json_safe():
    twin = ingest_all(ROOT)
    report = analyze_risk(twin)
    d = risk_report_as_dict(report)
    import json
    json.dumps(d)
    assert "top_risks" in d
    assert "dependency_graph" in d
    assert "failure_report" in d
    assert "debt_report" in d
    assert "critical_path" in d


# --- Integration: risk appears in twin_summary ---

def test_twin_summary_includes_risk():
    twin = ingest_all(ROOT)
    from msb_v3.plei.orchestrator import twin_summary
    summary = twin_summary(twin)
    assert "risk" in summary
    risk = summary["risk"]
    assert "top_risks" in risk
    assert "dependency_graph" in risk