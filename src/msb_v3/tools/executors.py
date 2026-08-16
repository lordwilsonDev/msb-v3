"""Tool executors — the contained execution half of the governed perimeter.

Every executor here terminates inside a sandbox:

    search_vault  -> tenant-scoped Qdrant RAG (read-only)
    vault_read    -> FileReader confined to the configured vault root
    vault_write   -> FileWriter confined to the configured vault root
                     (atomic write + hash receipt, reversible via rollback)

Executors are plain sync functions (the tool loop is sync); the async RAG
router runs via ``asyncio.run`` in a worker thread so it never collides with
a running event loop. Failures come back as structured strings
(``[denied]`` / ``[tool-error]``) — never exceptions past the boundary, and
never silent success.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

from msb_v3.core.config import settings
from msb_v3.node.filesystem import CapabilityViolation, FileReader, FileWriter

logger = logging.getLogger(__name__)

# One small pool for bridging the async RAG router into the sync tool loop.
_threads = ThreadPoolExecutor(max_workers=2, thread_name_prefix="govtool")


def _run_async(coro: Any) -> Any:
    """Run an async callable in a dedicated worker thread (fresh event loop)."""
    return _threads.submit(asyncio.run, coro).result()


def _vault_root() -> Any:
    from pathlib import Path

    return Path(settings.vault_path).expanduser().resolve()


# --- executors (signature: (args, *, tenant, session) -> str) -------------


def search_vault(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "[tool-error] search_vault: query is required"
    try:
        from msb_v3.fabric.retrieval_router import FabricRetrievalRouter

        router = FabricRetrievalRouter(tenant)
        result = _run_async(router.run(query, top_k=5))
        matches = list(getattr(result, "matches", None) or [])
        if not matches:
            return "No matches found in the vault."
        lines = []
        for m in matches[:5]:
            if isinstance(m, dict):
                text = str(m.get("text") or m.get("snippet") or "")[:300]
                src = str(m.get("source") or m.get("path") or "?")
                score = float(m.get("score", 0.0) or 0.0)
            else:
                text = str(getattr(m, "text", "") or "")[:300]
                src = str(getattr(m, "source", "") or "?")
                score = float(getattr(m, "score", 0.0) or 0.0)
            lines.append(f"- [{src}] (score {score:.2f}) {text}")
        return "\n".join(lines)
    except Exception as exc:
        logger.debug("search_vault failed", exc_info=True)
        return f"[tool-error] search_vault: {type(exc).__name__}: {exc}"


def vault_read(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    path = str(args.get("path") or "").strip()
    if not path:
        return "[tool-error] vault_read: path is required"
    try:
        reader = FileReader(_vault_root(), settings.node_max_read_bytes)
        data = reader.read(path)
        content = str(data.get("content") or "")
        return f"[{data.get('path')}] ({data.get('size')} bytes)\n{content[:4000]}"
    except CapabilityViolation as exc:
        return f"[denied] vault_read: {exc}"
    except Exception as exc:
        logger.debug("vault_read failed", exc_info=True)
        return f"[tool-error] vault_read: {type(exc).__name__}: {exc}"


def vault_write(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    path = str(args.get("path") or "").strip()
    content = str(args.get("content") or "")
    if not path:
        return "[tool-error] vault_write: path is required"
    try:
        writer = FileWriter(_vault_root(), settings.node_max_read_bytes)
        receipt = writer.write(path, content.encode("utf-8"))
        pub = receipt.public()
        return (
            f"wrote {pub['path']} ({pub['size']} bytes, "
            f"sha256 {str(pub['after_sha256'])[:12]})"
        )
    except CapabilityViolation as exc:
        return f"[denied] vault_write: {exc}"
    except Exception as exc:
        logger.debug("vault_write failed", exc_info=True)
        return f"[tool-error] vault_write: {type(exc).__name__}: {exc}"


# --- Code Graph executors (read-only, spec §4.2.1) ------------------------
# Every executor terminates inside the perimeter: it reads the local SQLite
# graph (never the source tree directly) and returns a string the model can
# reason over. Unknown repos return an honest "not indexed" — never a fake
# empty graph.


def _codegraph_repo_arg(args: Dict[str, Any]) -> str:
    return str(args.get("repo") or "").strip()


def _cg_explore(repo: str, args: Dict[str, Any]) -> str:
    from msb_v3.codegraph.queries import CodeGraphQueries
    from msb_v3.codegraph.store import CodeGraphStore
    from msb_v3.core.config import settings

    name = str(args.get("name") or "").strip()
    if not name:
        return "[tool-error] codegraph.explore: name is required"
    symbols = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path)).find_symbol(repo, name, limit=10)
    if not symbols:
        return f"No symbols matching {name!r} in {repo}. (Is the repo indexed? POST /codegraph/index.)"
    lines = []
    for s in symbols:
        approx = " ~" if s.get("approximate") else ""
        lines.append(f"- [{s['kind']}] {s['fq_name']} @ {s['file']}:{s['line']}{approx}")
    return "\n".join(lines)


def _cg_context(repo: str, args: Dict[str, Any]) -> str:
    from msb_v3.codegraph.queries import CodeGraphQueries
    from msb_v3.codegraph.store import CodeGraphStore
    from msb_v3.core.config import settings

    symbol = str(args.get("symbol") or "").strip()
    if not symbol:
        return "[tool-error] codegraph.context: symbol is required"
    ctx = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path)).context_of(repo, symbol)
    if not ctx.get("found"):
        cands = ctx.get("candidates") or []
        if cands:
            names = ", ".join(c["fq_name"] for c in cands[:5])
            return f"Symbol {symbol!r} not found in {repo}. Candidates: {names}"
        return f"Symbol {symbol!r} not found in {repo}. (Is the repo indexed?)"
    lines = [
        f"{ctx['kind']} {ctx['symbol']} @ {ctx['file']}:{ctx['line']} {ctx.get('signature','')}",
    ]
    callers = ctx.get("callers") or []
    callees = ctx.get("callees") or []
    if callers:
        lines.append("callers:")
        for c in callers[:10]:
            lines.append(f"  - {c['symbol']} @ {c['file']}:{c['line']}")
    if callees:
        lines.append("callees:")
        for c in callees[:10]:
            lines.append(f"  - {c['symbol']} @ {c['file']}:{c['line']}")
    return "\n".join(lines)


def _cg_impact(repo: str, args: Dict[str, Any]) -> str:
    from msb_v3.codegraph.queries import CodeGraphQueries
    from msb_v3.codegraph.store import CodeGraphStore
    from msb_v3.core.config import settings

    file = str(args.get("file") or "").strip()
    if not file:
        return "[tool-error] codegraph.impact: file is required"
    line = int(args.get("line") or 0)
    report = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path)).impact_of(repo, file, line=line)
    deps = report.get("dependents") or []
    if not deps:
        return f"No dependents found for {file} in {repo}."
    lines = [f"{len(deps)} dependents of {file}:"]
    for d in deps[:20]:
        lines.append(f"  - (hop {d['hop']}) {d['symbol']} @ {d['file']}:{d['line']}")
    return "\n".join(lines)


def _cg_rename(repo: str, args: Dict[str, Any]) -> str:
    from msb_v3.codegraph.queries import CodeGraphQueries
    from msb_v3.codegraph.store import CodeGraphStore
    from msb_v3.core.config import settings

    name = str(args.get("name") or "").strip()
    if not name:
        return "[tool-error] codegraph.rename: name is required"
    preview = CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path)).rename_preview(repo, name)
    defs = preview.get("definitions") or []
    refs = preview.get("references") or []
    if not defs and not refs:
        return f"No symbols named {name!r} in {repo}."
    lines = [f"rename {name!r}: {len(defs)} definition(s), {len(refs)} reference(s)"]
    for d in defs[:5]:
        lines.append(f"  def: {d['fq_name']} @ {d['file']}:{d['line']}")
    for r in refs[:20]:
        lines.append(f"  ref: {r['source']} -> {r['target']} ({r['relation']}) @ {r['file']}:{r['line']}")
    if len(refs) > 20:
        lines.append(f"  … and {len(refs) - 20} more")
    return "\n".join(lines)


def codegraph_explore(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    return _cg_explore(_codegraph_repo_arg(args), args)


def codegraph_context(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    return _cg_context(_codegraph_repo_arg(args), args)


def codegraph_impact(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    return _cg_impact(_codegraph_repo_arg(args), args)


def codegraph_rename(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    return _cg_rename(_codegraph_repo_arg(args), args)


# --- Memory Fabric executors (spec §4.2.2) ---------------------------------
# recall is read-only over the local SQLite fabric; store is a WRITE that
# the capability gate already guards (memory.write required — fail-closed).


def memory_recall(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    from msb_v3.core.config import settings
    from msb_v3.memory_fabric.fabric import MemoryFabric
    from msb_v3.memory_fabric.models import MemoryType
    from msb_v3.memory_fabric.store import MemoryFabricStore

    query = str(args.get("query") or "").strip()
    if not query:
        return "[tool-error] memory.recall: query is required"
    fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
    try:
        type_raw = str(args.get("type") or "").strip()
        type_ = MemoryType(type_raw) if type_raw else None
    except ValueError:
        return "[tool-error] memory.recall: unknown type (episodic|semantic|procedural|architectural)"
    try:
        top_k = max(1, min(int(args.get("top_k") or 8), 20))
    except (TypeError, ValueError):
        top_k = 8
    hits = fabric.recall_memories(
        query,
        tenant=tenant,
        project=str(args.get("project") or "").strip() or None,
        tech=str(args.get("tech") or "").strip() or None,
        type_=type_,
        top_k=top_k,
    )
    if not hits:
        return "No memories matched."
    lines = []
    for h in hits:
        tags = f" [{', '.join(h.tags)}]" if h.tags else ""
        lines.append(
            f"- [{h.score:.2f}] ({h.type}/{h.verification_state}) {h.content[:200]}{tags}"
        )
    return "\n".join(lines)


def memory_store(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    from msb_v3.core.config import settings
    from msb_v3.memory_fabric.fabric import MemoryFabric
    from msb_v3.memory_fabric.models import MemoryType
    from msb_v3.memory_fabric.store import MemoryFabricStore

    content = str(args.get("content") or "").strip()
    if not content:
        return "[tool-error] memory.store: content is required"
    type_raw = str(args.get("type") or "").strip()
    try:
        type_ = MemoryType(type_raw) if type_raw else MemoryType.SEMANTIC
    except ValueError:
        return "[tool-error] memory.store: unknown type (episodic|semantic|procedural|architectural)"
    try:
        importance = max(0.0, min(1.0, float(args.get("importance") or 0.5)))
    except (TypeError, ValueError):
        importance = 0.5
    tags_raw = args.get("tags") or []
    tags = [str(t).strip() for t in tags_raw if str(t).strip()] if isinstance(tags_raw, list) else []
    fabric = MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))
    item = fabric.store_memory(
        content,
        type_=type_,
        tags=tags,
        importance=importance,
        source_agent=str(args.get("source_agent") or "").strip() or "agent",
        source="governed-tool",
        task_id=session,
        tenant=tenant,
        project=str(args.get("project") or "").strip(),
        tech=str(args.get("tech") or "").strip(),
    )
    return (
        f"stored {item.memory_id} ({item.type.value}, importance {item.importance}, "
        f"state {item.verification_state.value})"
    )


def context_compose(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    from msb_v3.fabric.context_engine import ContextEngine

    task = str(args.get("task") or "").strip()
    if not task:
        return "[tool-error] context.compose: task is required"
    try:
        budget = max(200, int(args.get("budget_tokens") or 4000))
    except (TypeError, ValueError):
        budget = 4000
    pkg = ContextEngine().compose(
        task,
        tenant=tenant,
        session=session,
        repo=str(args.get("repo") or "").strip() or None,
        project=str(args.get("project") or "").strip() or None,
        tech=str(args.get("tech") or "").strip() or None,
        budget_tokens=budget,
    )
    if not pkg.text:
        return "[context] (empty — nothing composed)"
    return (
        f"[context {pkg.total_tokens}/{pkg.budget_tokens} tokens, "
        f"{pkg.reduction_pct}% vs naive]\n{pkg.text}"
    )


def moie_analyze(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    from msb_v3.moie import MoIEController

    claim = str(args.get("claim") or "").strip()
    if not claim:
        return "[tool-error] moie.analyze: claim is required"
    domains = args.get("domains") or []
    if not isinstance(domains, list):
        domains = []
    domains = [str(d).strip() for d in domains if str(d).strip()]
    context = {
        "domains": domains,
        "thorough": bool(args.get("thorough", False)),
        "high_impact": bool(args.get("high_impact", False)),
    }
    decision = MoIEController(tenant=tenant).analyze(claim, context=context)
    lines = [
        f"[moie] verdict={decision.verdict} confidence={decision.confidence} blocked={decision.blocked}",
        f"[moie] experts={len(decision.reports)} contradictions={len(decision.contradictions)}",
        f"[moie] ids={decision.ids.depth_score} (assumptions {decision.ids.assumptions_extracted} / inverted {decision.ids.assumptions_inverted} / evidence {decision.ids.evidence_retrieved} / predictions {decision.ids.falsifiable_predictions})",
    ]
    for r in decision.reports:
        lines.append(f"- {r.expert_id}: {r.verdict} ({r.confidence:.2f}) — {r.summary}")
    if decision.contradictions:
        lines.append("[moie] contradictions:")
        for c in decision.contradictions:
            lines.append(f"  - {c.expert_a} vs {c.expert_b}: {c.a_says} / {c.b_says}")
    if decision.recommended_actions:
        lines.append("[moie] recommended:")
        for a in decision.recommended_actions:
            lines.append(f"  - {a}")
    lines.append(f"[moie] {decision.meta_critique}")
    return "\n".join(lines)


def factory_run(args: Dict[str, Any], *, tenant: str, session: str) -> str:
    import os

    from msb_v3.factory import Builder, CliAgentBuilder, PatchBuilder, SoftwareFactory
    from msb_v3.factory.models import Issue

    title = str(args.get("title") or "").strip()
    repo = str(args.get("repo") or "").strip()
    if not title or not repo:
        return "[tool-error] factory.run: title and repo are required"
    if not os.path.isdir(repo):
        return f"[tool-error] factory.run: repo is not a directory: {repo}"

    builder_name = str(args.get("builder") or "cli")
    builder: Builder
    if builder_name == "patch":
        script = str(args.get("patch_script") or "").strip()
        if not script or not os.path.isfile(script):
            return "[tool-error] factory.run: builder=patch requires patch_script (a file path)"
        builder = PatchBuilder(script)
    elif builder_name == "cli":
        builder = CliAgentBuilder()
    else:
        return f"[tool-error] factory.run: unknown builder: {builder_name}"

    labels = args.get("labels") or []
    if not isinstance(labels, list):
        labels = []
    issue = Issue(title=title, body=str(args.get("body") or ""), repo=repo, labels=[str(x) for x in labels])
    run = _run_async(SoftwareFactory(builder=builder).process_issue(issue, repo=repo))
    lines = [
        f"[factory] verdict={run.verdict} type={run.classification.issue_type} severity={run.classification.severity}",
        f"[factory] plan_steps={len(run.plan.steps)} risks={len(run.plan.risks)}",
        f"[factory] build_ok={bool(run.build and run.build.ok)} changed={len(run.build.changed_files) if run.build else 0}",
        f"[factory] tests_ran={run.test.ran} tests_passed={run.test.passed}",
        f"[factory] review={run.review.verdict if run.review else 'none'} moie={run.review.moie_verdict if run.review else '-'}",
        f"[factory] verification={run.verification.verdict}",
        f"[factory] evidence_chain={','.join(run.evidence_chain)}",
    ]
    if run.error:
        lines.append(f"[factory] error={run.error}")
    if run.build and run.build.changed_files:
        lines.append(f"[factory] changed={','.join(run.build.changed_files[:8])}")
    return "\n".join(lines)
