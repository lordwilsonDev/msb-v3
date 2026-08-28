"""ContextCompiler — the minimum-sufficient-context engine.

Blueprint §7, §12:
    The compiler asks: *What does this worker actually need to know?*
    Not: *What information does the platform possess?*

    Minimum sufficient context is preferred over maximum available context.

Architecture:
    Full Knowledge
          ↓
    Relevance Filter
          ↓
    Task Context
          ↓
    Worker Context  (budget-constrained)

The ContextCompiler does NOT dump the entire repository into a 3B model.
It compiles: relevant architecture + relevant contracts + relevant files +
relevant tests + task specification + constraints + acceptance criteria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from msb_v3.meta.contracts import MetaTask
from msb_v3.meta.translation.import_graph import ImportGraph
from msb_v3.meta.translation.model_task import ContextBudget

logger = logging.getLogger(__name__)


@dataclass
class RelevanceScore:
    """How relevant a piece of context is to a specific task."""

    path: str
    score: float  # 0.0–1.0
    reason: str = ""
    source: str = ""  # "dependency" | "import" | "test" | "architecture" | "explicit"


@dataclass
class ContextSelection:
    """The compiled context for a specific task — minimum sufficient."""

    files: List[str] = field(default_factory=list)
    file_contents: Dict[str, str] = field(default_factory=dict)
    tests: List[str] = field(default_factory=list)
    architecture_refs: List[str] = field(default_factory=list)
    dependency_refs: List[str] = field(default_factory=list)
    budget: ContextBudget = field(default_factory=ContextBudget)
    rejected: List[RelevanceScore] = field(default_factory=list)  # for audit


class ContextCompiler:
    """Compiles minimum-sufficient context for a task from available knowledge.

    Usage::

        compiler = ContextCompiler(knowledge_base=repo_index)
        selection = compiler.compile(meta_task, budget=ContextBudget(...))
        # selection.files, selection.file_contents, selection.budget
    """

    def __init__(
        self,
        *,
        knowledge_base: Optional[Any] = None,
        file_index: Optional[Dict[str, Any]] = None,
        import_graph: Optional[Dict[str, List[str]]] = None,
        graph: Optional[ImportGraph] = None,
        test_index: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self._knowledge_base = knowledge_base
        self._file_index = file_index or {}
        self._import_graph = import_graph or {}
        self._graph = graph
        self._test_index = test_index or {}

    def compile(
        self,
        task: MetaTask,
        *,
        budget: Optional[ContextBudget] = None,
        worker_capabilities: Optional[List[str]] = None,
    ) -> ContextSelection:
        """Compile minimum-sufficient context for *task* within *budget*.

        This is the core algorithm: score all available context by relevance,
        then greedily include the highest-scoring items until the budget is
        exhausted.
        """
        budget = budget or ContextBudget()
        candidates = self._score_relevance(task, worker_capabilities)

        # Sort by score descending — most relevant first.
        candidates.sort(key=lambda r: r.score, reverse=True)

        selected_files: List[str] = []
        selected_contents: Dict[str, str] = {}
        selected_tests: List[str] = []
        selected_arch: List[str] = list(task.architecture_refs)  # always include
        selected_deps: List[str] = list(task.dependencies)
        rejected: List[RelevanceScore] = []
        tokens_used = 0

        # Always include explicitly requested files.
        for path in task.relevant_files:
            if path not in selected_files:
                selected_files.append(path)
                content = self._read_file(path)
                if content:
                    selected_contents[path] = content
                    tokens_used += self._estimate_tokens(content)

        # Always include explicitly requested tests.
        for path in task.relevant_tests:
            if path not in selected_tests:
                selected_tests.append(path)
                content = self._read_file(path)
                if content:
                    selected_contents[path] = content
                    tokens_used += self._estimate_tokens(content)

        # Greedily include scored candidates.
        budget_tokens = budget.available_tokens
        for candidate in candidates:
            if tokens_used >= budget_tokens:
                rejected.append(candidate)
                continue
            if candidate.path in selected_files or candidate.path in selected_tests:
                continue
            # Skip architecture refs already included.
            if candidate.path in selected_arch:
                continue

            content = self._read_file(candidate.path)
            if not content:
                continue

            est = self._estimate_tokens(content)
            if tokens_used + est > budget_tokens:
                rejected.append(candidate)
                continue

            selected_files.append(candidate.path)
            selected_contents[candidate.path] = content
            tokens_used += est

        # Update budget to reflect actual usage.
        budget.files_included = len(selected_files)
        budget.files_available = len(self._file_index) or len(selected_files)
        budget.tests_included = len(selected_tests)
        budget.tests_available = len(self._test_index) or len(selected_tests)
        budget.available_tokens = max(0, budget_tokens - tokens_used)

        return ContextSelection(
            files=selected_files,
            file_contents=selected_contents,
            tests=selected_tests,
            architecture_refs=selected_arch,
            dependency_refs=selected_deps,
            budget=budget,
            rejected=rejected,
        )

    # -- internal scoring ---------------------------------------------------

    def _score_relevance(
        self,
        task: MetaTask,
        worker_capabilities: Optional[List[str]] = None,
    ) -> List[RelevanceScore]:
        """Score every known file by relevance to *task*.

        Uses two scoring channels:
            1. Graph-aware scoring (META-2): distance from seed files, hub detection,
               reverse-dep bonus.
            2. Legacy scoring: keyword matching, same-directory, test matching.

        The graph score takes priority when an ImportGraph is available.
        """
        scores: List[RelevanceScore] = []
        task_words = set(task.objective.lower().split())
        related_files: Set[str] = set(task.relevant_files)

        # Expand related files via flat import graph (legacy).
        for f in list(related_files):
            for dep in self._import_graph.get(f, []):
                related_files.add(dep)

        # Expand via ImportGraph transitive deps (META-2).
        if self._graph is not None and task.relevant_files:
            for f in task.relevant_files:
                related_files |= self._graph.transitive_deps(f, max_depth=5)

        # Compute graph-aware relevance scores for all candidates.
        graph_scores: Dict[str, float] = {}
        if self._graph is not None and task.relevant_files:
            all_candidates = [p for p in self._file_index if p not in task.relevant_files]
            graph_scores = self._graph.relevance_scores(
                seed_files=task.relevant_files,
                candidate_files=all_candidates,
            )

        for path, meta in self._file_index.items():
            if path in task.relevant_files:
                continue  # already included explicitly

            score = 0.0
            reasons: List[str] = []

            # Graph-aware distance score (META-2).
            if path in graph_scores and graph_scores[path] > 0:
                graph_score = graph_scores[path]
                score += graph_score * 0.6  # 60% weight for graph signal
                reasons.append(f"graph_distance:{graph_score:.2f}")

            # Direct dependency — high relevance.
            if path in related_files:
                score += 0.5
                reasons.append("dependency")

            # Test file matching task tests.
            if path in task.relevant_tests:
                score += 0.4
                reasons.append("requested_test")

            # File path matches task objective words.
            path_words = set(path.lower().replace("/", " ").replace("_", " ").replace("-", " ").split())
            overlap = task_words & path_words
            if overlap:
                score += 0.2 * min(1.0, len(overlap) / 3)
                reasons.append(f"keyword_match:{','.join(sorted(overlap)[:3])}")

            # Same directory as relevant files.
            for rf in task.relevant_files:
                if path.rsplit("/", 1)[0] == rf.rsplit("/", 1)[0]:
                    score += 0.1
                    reasons.append("same_directory")
                    break

            if score > 0:
                scores.append(RelevanceScore(
                    path=path,
                    score=min(1.0, score),
                    reason="; ".join(reasons),
                    source="context_compiler",
                ))

        return scores

    # -- helpers ------------------------------------------------------------

    def _read_file(self, path: str) -> Optional[str]:
        """Read file content from the knowledge base or file index."""
        if self._file_index and path in self._file_index:
            meta = self._file_index[path]
            if isinstance(meta, dict):
                return meta.get("content", "")
            return str(meta)
        if self._knowledge_base and hasattr(self._knowledge_base, "read_file"):
            try:
                return self._knowledge_base.read_file(path)
            except Exception:  # noqa: BLE001
                return None
        return None

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token for English code."""
        return max(1, len(text) // 4)
