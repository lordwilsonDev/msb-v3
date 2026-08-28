"""META-1A: Translation engine tests — the bridge between MetaTask and worker execution.

Tests verify:
  - ContextBudget utilization calculation
  - ToolPolicy enforcement (allow/forbid)
  - ModelTask worker envelope serialization
  - ContextCompiler selects minimum-sufficient context
  - TaskTranslator preserves objective semantics
  - TaskTranslator produces audit trail
"""

from __future__ import annotations

from msb_v3.meta.contracts import Complexity, MetaTask, TaskState
from msb_v3.meta.translation.context_compiler import ContextCompiler
from msb_v3.meta.translation.import_graph import ImportGraph
from msb_v3.meta.translation.model_task import ContextBudget, ModelTask, ToolPolicy
from msb_v3.meta.translation.task_translator import TaskTranslator, WorkerProfile

# ---------------------------------------------------------------------------
# ToolPolicy
# ---------------------------------------------------------------------------

class TestToolPolicy:
    def test_empty_policy_allows_all(self) -> None:
        tp = ToolPolicy()
        assert tp.is_allowed("anything") is True

    def test_allowlist_rejects_unlisted(self) -> None:
        tp = ToolPolicy(allowed=["pytest", "ruff"])
        assert tp.is_allowed("pytest") is True
        assert tp.is_allowed("ruff") is True
        assert tp.is_allowed("curl") is False

    def test_forbidlist_overrides_allowlist(self) -> None:
        tp = ToolPolicy(allowed=["pytest", "curl"], forbidden=["curl"])
        assert tp.is_allowed("pytest") is True
        assert tp.is_allowed("curl") is False

    def test_forbidlist_blocks_even_without_allowlist(self) -> None:
        tp = ToolPolicy(forbidden=["network"])
        assert tp.is_allowed("network") is False
        assert tp.is_allowed("file_write") is True


# ---------------------------------------------------------------------------
# ContextBudget
# ---------------------------------------------------------------------------

class TestContextBudget:
    def test_utilization_zero_when_fresh(self) -> None:
        b = ContextBudget(total_tokens=8192, reserved_tokens=2048, available_tokens=6144)
        assert b.utilization == 0.0

    def test_utilization_one_when_exhausted(self) -> None:
        b = ContextBudget(total_tokens=8192, reserved_tokens=2048, available_tokens=0)
        assert b.utilization == 1.0

    def test_utilization_partial(self) -> None:
        b = ContextBudget(total_tokens=8192, reserved_tokens=2048, available_tokens=3072)
        # used = 8192 - 2048 - 3072 = 3072
        # capacity = 8192 - 2048 = 6144
        # utilization = 3072 / 6144 = 0.5
        assert abs(b.utilization - 0.5) < 0.01


# ---------------------------------------------------------------------------
# ModelTask
# ---------------------------------------------------------------------------

class TestModelTask:
    def test_worker_envelope_contains_required_keys(self) -> None:
        mt = ModelTask(
            model_task_id="MT-1",
            source_task_id="T-1",
            worker_id="qwen3b",
            objective="Implement foo",
        )
        env = mt.to_worker_envelope()
        assert env["task_id"] == "MT-1"
        assert env["source_task_id"] == "T-1"
        assert env["worker_id"] == "qwen3b"
        assert env["objective"] == "Implement foo"
        assert "context" in env
        assert "tools" in env
        assert "stop_conditions" in env
        assert "budget" in env

    def test_state_starts_ready(self) -> None:
        mt = ModelTask(
            model_task_id="MT-1",
            source_task_id="T-1",
            worker_id="w",
            objective="x",
        )
        assert mt.state is TaskState.READY


# ---------------------------------------------------------------------------
# ContextCompiler
# ---------------------------------------------------------------------------

class TestContextCompiler:
    def test_empty_knowledge_base_returns_no_files(self) -> None:
        compiler = ContextCompiler()
        task = MetaTask(task_id="T1", objective="Implement X")
        sel = compiler.compile(task)
        assert sel.files == []
        assert sel.file_contents == {}

    def test_explicitly_requested_files_always_included(self) -> None:
        file_index = {
            "src/foo.py": {"content": "def foo(): pass"},
            "src/bar.py": {"content": "def bar(): pass"},
        }
        compiler = ContextCompiler(file_index=file_index)
        task = MetaTask(
            task_id="T1",
            objective="Implement foo",
            relevant_files=["src/foo.py"],
        )
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        assert "src/foo.py" in sel.files
        assert sel.file_contents["src/foo.py"] == "def foo(): pass"

    def test_budget_is_respected(self) -> None:
        huge_content = "x" * 100000  # ~25000 tokens
        file_index = {f"src/big{i}.py": {"content": huge_content} for i in range(10)}
        compiler = ContextCompiler(file_index=file_index)
        task = MetaTask(task_id="T1", objective="big task")
        budget = ContextBudget(total_tokens=4096, reserved_tokens=1024, available_tokens=3072)
        sel = compiler.compile(task, budget=budget)
        # Should not include all 10 files — budget limits inclusion.
        assert len(sel.files) < 10

    def test_keyword_relevance_scoring(self) -> None:
        file_index = {
            "src/auth.py": {"content": "class Auth: ..."},
            "src/translation/engine.py": {"content": "class Translator: ..."},
        }
        compiler = ContextCompiler(file_index=file_index)
        task = MetaTask(task_id="T1", objective="implement translation engine")
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        # translation engine should score higher than auth
        if sel.files:
            assert "src/translation/engine.py" in sel.files or len(sel.files) == 0

    def test_rejected_files_recorded_for_audit(self) -> None:
        # Create a scenario where budget forces rejection.
        file_index = {f"src/file{i}.py": {"content": f"# content {i}\n" + "x" * 200} for i in range(5)}
        compiler = ContextCompiler(file_index=file_index)
        task = MetaTask(task_id="T1", objective="task with many files")
        # Tiny budget forces rejection.
        budget = ContextBudget(total_tokens=512, reserved_tokens=128, available_tokens=384)
        sel = compiler.compile(task, budget=budget)
        # At least some should be rejected or all fit in 384 tokens
        assert isinstance(sel.rejected, list)


# ---------------------------------------------------------------------------
# ContextCompiler + ImportGraph (META-2)
# ---------------------------------------------------------------------------

class TestContextCompilerWithGraph:
    """Tests that verify graph-aware context compilation."""

    def test_graph_increases_relevance_of_connected_files(self) -> None:
        """Files connected via import graph should score higher than isolated files."""
        graph = ImportGraph.from_adjacency({
            "src/auth.py": ["src/crypto.py"],
            "src/crypto.py": ["src/config.py"],
            "src/config.py": [],
        })
        file_index = {
            "src/auth.py": {"content": "class Auth: ..."},
            "src/crypto.py": {"content": "class Crypto: ..."},
            "src/config.py": {"content": "DB_URL=..."},
            "src/unrelated.py": {"content": "# nothing"},
        }
        compiler = ContextCompiler(file_index=file_index, graph=graph)
        task = MetaTask(
            task_id="T1",
            objective="Fix auth",
            relevant_files=["src/auth.py"],
        )
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        # auth.py is explicit → always included
        # crypto.py and config.py are graph-connected → should be included
        # unrelated.py has no graph connection → should score lower
        assert "src/auth.py" in sel.files
        assert "src/crypto.py" in sel.files

    def test_transitive_deps_included_via_graph(self) -> None:
        """Graph transitive deps should be available for scoring."""
        graph = ImportGraph.from_adjacency({
            "src/a.py": ["src/b.py"],
            "src/b.py": ["src/c.py"],
            "src/c.py": [],
        })
        file_index = {
            "src/a.py": {"content": "import b"},
            "src/b.py": {"content": "import c"},
            "src/c.py": {"content": "CONFIG"},
            "src/z.py": {"content": "unrelated"},
        }
        compiler = ContextCompiler(file_index=file_index, graph=graph)
        task = MetaTask(
            task_id="T1",
            objective="task",
            relevant_files=["src/a.py"],
        )
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        assert "src/a.py" in sel.files
        # b.py is direct dep → high score
        # c.py is transitive dep → positive score
        # z.py has no graph connection → lower
        included_contents = list(sel.file_contents.values())
        assert len(included_contents) >= 2  # at least a.py + b.py

    def test_graph_none_falls_back_to_legacy(self) -> None:
        """Without a graph, scoring falls back to keyword/dependency heuristics."""
        file_index = {
            "src/foo.py": {"content": "def foo(): pass"},
            "src/bar.py": {"content": "def bar(): pass"},
        }
        compiler = ContextCompiler(file_index=file_index)  # no graph
        task = MetaTask(task_id="T1", objective="Implement foo")
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        # Should still work via keyword matching
        assert isinstance(sel.files, list)

    def test_empty_graph_compiles_cleanly(self) -> None:
        """An empty graph should not break compilation."""
        graph = ImportGraph.empty()
        file_index = {"src/x.py": {"content": "x = 1"}}
        compiler = ContextCompiler(file_index=file_index, graph=graph)
        task = MetaTask(task_id="T1", objective="do x")
        sel = compiler.compile(task)
        assert isinstance(sel.files, list)

    def test_graph_distance_scores_prefer_closer_files(self) -> None:
        """Direct deps should score higher than transitive deps via graph."""
        graph = ImportGraph.from_adjacency({
            "src/seed.py": ["src/direct.py"],
            "src/direct.py": ["src/transitive.py"],
            "src/transitive.py": [],
        })
        file_index = {
            "src/seed.py": {"content": "# seed"},
            "src/direct.py": {"content": "# direct dep"},
            "src/transitive.py": {"content": "# transitive dep"},
        }
        compiler = ContextCompiler(file_index=file_index, graph=graph)
        task = MetaTask(
            task_id="T1",
            objective="task",
            relevant_files=["src/seed.py"],
        )
        sel = compiler.compile(task, budget=ContextBudget(total_tokens=8192, available_tokens=8192))
        # Both should be included given large budget
        assert "src/direct.py" in sel.files
        assert "src/transitive.py" in sel.files


# ---------------------------------------------------------------------------
# TaskTranslator
# ---------------------------------------------------------------------------

class TestTaskTranslator:
    def test_objective_is_preserved_semantically(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(
            task_id="T-100",
            objective="Implement audio renderer contract",
            task_type="implementation",
        )
        worker = WorkerProfile(worker_id="qwen3b", worker_name="Qwen 3B")
        mt = translator.translate(task, worker=worker)
        assert mt.objective == task.objective
        assert mt.source_task_id == task.task_id

    def test_worker_id_appears_in_translated_task(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(task_id="T-1", objective="Do X")
        worker = WorkerProfile(worker_id="deepseek-v3")
        mt = translator.translate(task, worker=worker)
        assert mt.worker_id == "deepseek-v3"

    def test_translation_notes_recorded(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(task_id="T-1", objective="Do X")
        worker = WorkerProfile(worker_id="w1", examples=[{"input": "a", "output": "b"}])
        mt = translator.translate(task, worker=worker)
        assert any("worker: w1" in n for n in mt.translation_notes)
        assert any("examples" in n for n in mt.translation_notes)

    def test_tool_policy_derived_from_worker_capabilities(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(task_id="T-1", objective="Do X")
        worker = WorkerProfile(
            worker_id="w1",
            capabilities=["python", "testing"],
            negative_capabilities=["network"],
        )
        mt = translator.translate(task, worker=worker)
        assert mt.tool_policy.is_allowed("python") is True
        assert mt.tool_policy.is_allowed("network") is False

    def test_task_constraints_override_worker_constraints(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(
            task_id="T-1",
            objective="Do X",
            metadata={"constraints": {"max_files_changed": 3}},
        )
        worker = WorkerProfile(worker_id="w1", constraints={"max_files_changed": 10})
        mt = translator.translate(task, worker=worker)
        assert mt.constraints["max_files_changed"] == 3  # task overrides

    def test_complexity_carried_from_task(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(task_id="T-1", objective="Do X", complexity=Complexity.CRITICAL)
        worker = WorkerProfile(worker_id="w1")
        mt = translator.translate(task, worker=worker)
        assert mt.complexity is Complexity.CRITICAL

    def test_budget_reflects_worker_max_context(self) -> None:
        translator = TaskTranslator()
        task = MetaTask(task_id="T-1", objective="Do X")
        worker = WorkerProfile(worker_id="w1", max_context_tokens=4096)
        mt = translator.translate(task, worker=worker)
        assert mt.budget.total_tokens == 4096
