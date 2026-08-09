#!/usr/bin/env python3
"""r02_outcome_runner.py — Semantic Retrieval Router outcome gate.

Measures the blueprint's success criteria against a labeled retrieval
fixture (r02_eval_cases.jsonl):

  1. NDCG@10 of the full cue-based plan vs a forced vector-only baseline,
     gated at >= 15% relative improvement
     (blueprint: "NDCG@10 improves by >= 15% over single-index baseline").
  2. Routing precision: predicted plan routes vs human-labeled expected
     routes, gated at > 0.9 (blueprint: "routing accuracy > 90%").
  3. p95 end-to-end latency of the router, gated at < 500 ms
     (blueprint: "95th percentile response time < 500 ms for typical
     queries").

Zero-spend by construction: local Ollama embeddings + local Qdrant, no LLM
calls. Requires live infra; when unavailable the verdict is `blocked`
(exit 2, non-fatal in the hygiene aggregate). CI has no Qdrant/Ollama and
reports blocked; on this machine the gate runs for real.

Artifact: <repo>/artifacts/hygiene/r02_outcome_<ts>.json
Exit: 0 = pass, 1 = fail, 2 = blocked.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

REPO = Path(os.environ.get("MSB_REPO", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO))
EVIDENCE_DIR = REPO / "artifacts" / "hygiene"
EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "r02_eval_cases.jsonl"

SKILL = "regression-hygiene"
TOP_K = 10
EMBED_DIM = 768

# Blueprint success criteria (gates).
NDCG_MIN_IMPROVEMENT = 0.15   # >= 15% over vector-only baseline
PRECISION_MIN = 0.90          # > 0.90 routing precision
P95_MAX_MS = 500.0            # < 500 ms p95 latency

# Absolute floor on router mean NDCG: the relative-improvement gate alone can
# pass when BOTH plans are mediocre (0.4 -> 0.46 = +15%). This closes the
# "both bad but 15% less bad" hole; routing precision only guards the planner.
NDCG_ROUTER_FLOOR = 0.50
# Relative improvement is only meaningful when the baseline actually retrieves;
# below this, treat the comparison as "baseline broken" (see main()).
NDCG_MIN_BASELINE = 0.10
# Timed trials per case; the per-case median feeds p95. A single cold/Ollama
# hiccup otherwise becomes p95 itself (12 samples -> p95 == max).
LATENCY_TRIALS = 3


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _infra_available() -> bool:
    try:
        from msb_v3.api.rag import _HAS_QDRANT, _qdrant_client
        if not _HAS_QDRANT:
            return False
        _qdrant_client().get_collections()
        import httpx
        ollama = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        if httpx.get(f"{ollama}/api/tags", timeout=2).status_code != 200:
            return False
        return True
    except Exception:
        return False


def ndcg_at_k(ranked_ids: list[str], relevant: set[str], k: int = TOP_K) -> float:
    """Binary-relevance NDCG@k over the ranked id list."""
    dcg = 0.0
    for i, doc_id in enumerate(ranked_ids[:k]):
        if doc_id in relevant:
            dcg += 1.0 / math.log2(i + 2)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(relevant), k)))
    return dcg / ideal if ideal > 0 else 0.0


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(1, math.ceil(p / 100.0 * len(sorted_vals)))
    return sorted_vals[idx - 1]


def load_fixture() -> tuple[list[dict], list[dict]]:
    docs: list[dict] = []
    cases: list[dict] = []
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if obj.get("kind") == "corpus":
            docs = obj["docs"]
        elif obj.get("kind") == "case":
            cases.append(obj)
    if not docs or not cases:
        raise ValueError(f"fixture {FIXTURE} has no corpus or cases")
    return docs, cases


async def seed_corpus(client, tenant: str, docs: list[dict]) -> list[str]:
    from msb_v3.api.rag import _collection, _embed

    now = time.time()
    # Use the same tenant -> collection normalization the engine applies
    # (msb_v3.api.rag._collection), or the router searches a name that was
    # never created.
    collection = _collection(tenant)
    client.create_collection(
        collection_name=collection,
        vectors_config={"size": EMBED_DIM, "distance": "Cosine"},
    )
    points = []
    for doc in docs:
        vec = await _embed(doc["text"])
        # Qdrant point ids must be integers or UUIDs; the human doc id travels
        # in the payload as metadata.doc_id.
        points.append({
            "id": str(uuid.uuid4()),
            "vector": vec,
            "payload": {
                "tenant_id": tenant,
                "text": doc["text"],
                "source": "r02-eval",
                "metadata": {
                    "doc_id": doc["id"],
                    "tag": doc.get("tag", ""),
                    "timestamp": now - doc["days_ago"] * 86400.0,
                },
            },
        })
    client.upsert(collection_name=collection, points=points)
    return [d["id"] for d in docs]


def ranked_doc_ids(matches: list[dict]) -> list[str]:
    """Human doc ids in ranked order (from the payload; qdrant point ids are uuids)."""
    return [m["metadata"].get("doc_id", m["id"]) for m in matches]


def build_artifact(metrics: dict, n_cases: int, started: float,
                   errors: list[str]) -> dict:
    passed = (
        metrics["ndcg_improvement_ok"]
        and metrics["ndcg_floor_ok"]
        and metrics["routing_precision_ok"]
        and metrics["latency_ok"]
    )
    return {
        "experiment": "r02_outcome",
        "experiment_id": "r02_outcome",
        "artifact": str(EVIDENCE_DIR / f"r02_outcome_{_now()}.json"),
        "skill": SKILL,
        "input": f"pytest-style offline eval over {FIXTURE.name} "
                 f"({n_cases} labeled cases, live Qdrant+Ollama)",
        "environment": f"msb-v3 repo @ {REPO}",
        "expected_behavior": (
            f"NDCG@10 improvement >= {NDCG_MIN_IMPROVEMENT:.0%} over vector-only, "
            f"routing precision > {PRECISION_MIN:.0%}, p95 latency < {P95_MAX_MS:.0f} ms"
        ),
        "actual_behavior": json.dumps(metrics, sort_keys=True),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "errors": errors,
        "state_before": {"zero_spend": True, "llm_calls": 0,
                         "network": "localhost only (Ollama + Qdrant)"},
        "state_after": metrics,
        "recovery": "n/a — live eval; re-run against the same fixture",
        "false_repair": False,
        "evidence": [f"ndcg_baseline={metrics['ndcg_baseline_mean']:.4f} "
                     f"ndcg_router={metrics['ndcg_router_mean']:.4f} "
                     f"improvement={metrics['ndcg_improvement_pct']:.1f}% "
                     f"precision={metrics['routing_precision']:.3f} "
                     f"recall={metrics['routing_recall']:.3f} "
                     f"p95={metrics['p95_ms']:.0f}ms"],
        "verdict": "pass" if passed else "fail",
    }


async def main() -> int:
    started = time.perf_counter()
    errors: list[str] = []
    cases_detail: list[dict] = []

    if not _infra_available():
        artifact = {
            "experiment": "r02_outcome",
            "experiment_id": "r02_outcome",
            "artifact": str(EVIDENCE_DIR / f"r02_outcome_{_now()}.json"),
            "skill": SKILL,
            "input": f"live eval over {FIXTURE.name}",
            "environment": f"msb-v3 repo @ {REPO}",
            "expected_behavior": "NDCG@10 improvement, routing precision, p95 latency gates",
            "actual_behavior": "blocked: no live Qdrant/Ollama on this host",
            "latency_ms": 0,
            "errors": ["live Qdrant and/or Ollama unavailable"],
            "state_before": {"zero_spend": True},
            "state_after": {"blocked": True},
            "recovery": "run on a host with Qdrant (localhost:6333) and Ollama "
                        "(localhost:11434) up; CI cannot execute this leg",
            "false_repair": False,
            "evidence": ["blocked"],
            "verdict": "blocked",
        }
        EVIDENCE_DIR.joinpath(artifact["artifact"]).write_text(
            json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 2

    try:
        docs, cases = load_fixture()
    except Exception as exc:  # noqa: BLE001
        artifact = {
            "experiment": "r02_outcome", "experiment_id": "r02_outcome",
            "artifact": str(EVIDENCE_DIR / f"r02_outcome_{_now()}.json"),
            "skill": SKILL, "input": f"fixture {FIXTURE}",
            "environment": f"msb-v3 repo @ {REPO}",
            "expected_behavior": "load fixture", "actual_behavior": f"fixture error: {exc}",
            "latency_ms": 0, "errors": [str(exc)],
            "state_before": {}, "state_after": {"loaded": False},
            "recovery": "fix r02_eval_cases.jsonl", "false_repair": False,
            "evidence": [str(exc)], "verdict": "fail",
        }
        EVIDENCE_DIR.joinpath(artifact["artifact"]).write_text(
            json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 1

    from msb_v3.api.rag import _qdrant_client
    from msb_v3.retrieval.engine import RetrievalRouter

    client = _qdrant_client()
    # PID-suffixed: int(time.time()) is second-granular and two runners in the
    # same second would share (and stomp) one collection's lifecycle.
    tenant = f"r02_eval_{int(time.time())}_{os.getpid()}"
    try:
        await seed_corpus(client, tenant, docs)
        router = RetrievalRouter(tenant_id=tenant)

        # Burn-in before timing: warm the embedding pipeline (Ollama model
        # load) AND each route type's first-touch costs (fresh collection
        # segment/index build). Otherwise the first timed case pays a
        # multi-second cold spike and p95 misreports steady-state latency,
        # which is what the blueprint's "typical queries" criterion means.
        for warm_q in (
            "warmup quarterly renewal process",                       # vector
            "warmup what changed last week in vendor onboarding",     # + temporal
            "warmup marketing budget tag:budget notes",               # + structural
        ):
            await router.run(warm_q, top_k=5)

        baseline_scores: list[float] = []
        router_scores: list[float] = []
        latencies: list[float] = []
        predicted_matches: int = 0
        route_errors: dict[str, int] = {}

        for case in cases:
            query = case["query"]
            relevant = set(case["relevant"])

            baseline = await router.run(query, top_k=TOP_K, routes=["vector"])
            baseline_scores.append(
                ndcg_at_k(ranked_doc_ids(baseline["matches"]), relevant))

            # Timed trials: the per-case MEDIAN feeds p95, so a single
            # cold/hiccup trial can't become the p95 by itself.
            lat_trials: list[float] = []
            routed = None
            for _ in range(LATENCY_TRIALS):
                started_case = time.perf_counter()
                routed = await router.run(query, top_k=TOP_K)
                lat_trials.append((time.perf_counter() - started_case) * 1000.0)
            latencies.append(statistics.median(lat_trials))
            router_scores.append(
                ndcg_at_k(ranked_doc_ids(routed["matches"]), relevant))

            predicted = {r["index"] for r in routed["plan"]["routes"]}
            expected = set(case["expected_routes"])
            predicted_matches += len(predicted & expected)
            for route, err in routed["route_errors"].items():
                route_errors[route] = route_errors.get(route, 0) + 1
            cases_detail.append({
                "query": query,
                "expected_routes": sorted(expected),
                "predicted_routes": sorted(predicted),
                "ndcg_baseline": round(baseline_scores[-1], 4),
                "ndcg_router": round(router_scores[-1], 4),
                "latency_ms": round(latencies[-1], 1),
            })

        n_predicted = sum(len(c["predicted_routes"]) for c in cases_detail)
        n_expected = sum(len(c["expected_routes"]) for c in cases_detail)
        routing_precision = predicted_matches / n_predicted if n_predicted else 0.0
        routing_recall = predicted_matches / n_expected if n_expected else 0.0

        baseline_mean = sum(baseline_scores) / len(baseline_scores)
        router_mean = sum(router_scores) / len(router_scores)
        if baseline_mean >= NDCG_MIN_BASELINE:
            # Relative improvement, as the blueprint states it.
            improvement = (router_mean - baseline_mean) / baseline_mean
        else:
            # Baseline effectively broken (empty/near-empty retrieval): the
            # ratio would amplify noise. The router must clear the absolute
            # floor to count as an improvement.
            improvement = 1.0 if router_mean >= NDCG_ROUTER_FLOOR else 0.0
        improvement_pct = improvement * 100.0

        sorted_lat = sorted(latencies)
        p95 = percentile(sorted_lat, 95.0)

        metrics = {
            "cases": len(cases),
            "ndcg_baseline_mean": round(baseline_mean, 4),
            "ndcg_router_mean": round(router_mean, 4),
            "ndcg_improvement_pct": round(improvement_pct, 1),
            "ndcg_improvement_ok": improvement_pct >= NDCG_MIN_IMPROVEMENT * 100.0,
            "ndcg_floor_ok": router_mean >= NDCG_ROUTER_FLOOR,
            "routing_precision": round(routing_precision, 4),
            "routing_recall": round(routing_recall, 4),
            "routing_precision_ok": routing_precision > PRECISION_MIN,
            "p95_ms": round(p95, 1),
            "p50_ms": round(percentile(sorted_lat, 50.0), 1),
            "max_ms": round(sorted_lat[-1], 1) if sorted_lat else 0.0,
            "latency_ok": p95 < P95_MAX_MS,
            "route_errors": route_errors,
            "per_case": cases_detail,
        }
        artifact = build_artifact(metrics, len(cases), started, errors)
        artifact["state_after"] = metrics
        artifact["actual_behavior"] = (
            f"improvement={improvement_pct:.1f}% (gate >= {NDCG_MIN_IMPROVEMENT * 100:.0f}%) | "
            f"router_ndcg={router_mean:.3f} (floor {NDCG_ROUTER_FLOOR}) | "
            f"precision={routing_precision:.3f} (gate > {PRECISION_MIN}) | "
            f"p95={p95:.0f}ms (gate < {P95_MAX_MS:.0f}ms) | verdict={artifact['verdict']}"
        )
        EVIDENCE_DIR.joinpath(artifact["artifact"]).write_text(
            json.dumps(artifact, indent=2, default=str), encoding="utf-8")
        print(json.dumps(artifact, indent=2, default=str))
        return 0 if artifact["verdict"] == "pass" else 1
    except Exception as exc:  # noqa: BLE001
        errors.append(f"{type(exc).__name__}: {exc}")
        artifact = {
            "experiment": "r02_outcome", "experiment_id": "r02_outcome",
            "artifact": str(EVIDENCE_DIR / f"r02_outcome_{_now()}.json"),
            "skill": SKILL, "input": f"live eval over {FIXTURE.name}",
            "environment": f"msb-v3 repo @ {REPO}",
            "expected_behavior": "run the eval and gate the metrics",
            "actual_behavior": f"runner crashed: {errors[-1]}",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "errors": errors, "state_before": {}, "state_after": {"crashed": True},
            "recovery": "inspect the traceback; infra or fixture issue",
            "false_repair": False, "evidence": errors, "verdict": "fail",
        }
        EVIDENCE_DIR.joinpath(artifact["artifact"]).write_text(
            json.dumps(artifact, indent=2), encoding="utf-8")
        print(json.dumps(artifact, indent=2))
        return 1
    finally:
        try:
            from msb_v3.api.rag import _collection
            client.delete_collection(collection_name=_collection(tenant))
        except Exception:  # noqa: BLE001 — cleanup is best-effort
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
