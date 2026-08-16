"""Provider abstraction (unified-architecture §7) + agent identity delegation.

Hermetic by construction: CLI workers are fake scripts in a tmp dir — no
real Claude/Codex/OpenCode binary is ever invoked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.agent.providers import (
    CliAgentProvider,
    LocalAgentProvider,
    ProviderRegistry,
)


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


_OK_SCRIPT = """#!/usr/bin/env python3
import os, sys
worktree = os.environ.get("MSB_WORKTREE", ".")
with open(os.path.join(worktree, "result.txt"), "w") as f:
    f.write("worker produced: " + sys.argv[-1])
print("WORKER OK", flush=True)
"""

_SLEEP_SCRIPT = "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n"

_FAIL_SCRIPT = "#!/usr/bin/env python3\nimport sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"


# --- CliAgentProvider (hermetic) --------------------------------------------


@pytest.mark.asyncio
async def test_cli_provider_runs_worker_in_worktree(tmp_path):
    provider = CliAgentProvider((_write_script(tmp_path, "worker.py", _OK_SCRIPT),), timeout_s=10)
    assert provider.available() is True
    result = await provider.execute("build the thing", session="s")
    assert result.ok is True
    assert "WORKER OK" in result.output
    assert "result.txt" in result.artifacts  # artifact retrieved from the worktree


@pytest.mark.asyncio
async def test_cli_provider_timeout_kills_worker(tmp_path):
    provider = CliAgentProvider((_write_script(tmp_path, "sleepy.py", _SLEEP_SCRIPT),), timeout_s=1)
    result = await provider.execute("do nothing", session="s")
    assert result.ok is False
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_cli_provider_streams_output_to_observation_sink(tmp_path):
    """Each non-empty stdout line is streamed to the observation sink as it
    is produced — source cli.output, incrementing update_count, timestamps —
    while the final output stays intact."""
    script = _write_script(
        tmp_path, "talker.py",
        "#!/usr/bin/env python3\nimport time\nfor line in [\"first line\", \"second line\", \"third line\"]:\n    print(line, flush=True)\n    time.sleep(0.05)\n",
    )
    provider = CliAgentProvider((script,), provider_id="cli.talk", timeout_s=10)
    samples = []

    async def sink(sample):
        samples.append(sample)

    result = await provider.execute("say stuff", context={"observation_sink": sink}, session="s")
    assert result.ok is True
    assert [s["content"] for s in samples] == ["first line", "second line", "third line"]
    assert all(s["source"] == "cli.output" for s in samples)
    assert [s["update_count"] for s in samples] == [1, 2, 3]
    assert all(s.get("observed_at") for s in samples)
    assert "first line" in result.output


@pytest.mark.asyncio
async def test_cli_provider_sink_failure_is_best_effort(tmp_path):
    """A failing observation sink never breaks the worker run — output is
    still captured and the result is unaffected."""
    script = _write_script(tmp_path, "one_liner.py", "#!/usr/bin/env python3\nprint('hello', flush=True)\n")
    provider = CliAgentProvider((script,), provider_id="cli.one", timeout_s=10)

    async def bad_sink(sample):
        raise RuntimeError("sink exploded")

    result = await provider.execute("hi", context={"observation_sink": bad_sink}, session="s")
    assert result.ok is True
    assert result.output.strip() == "hello"


@pytest.mark.asyncio
async def test_cli_provider_nonzero_exit_is_failure(tmp_path):
    provider = CliAgentProvider((_write_script(tmp_path, "fail.py", _FAIL_SCRIPT),), timeout_s=10)
    result = await provider.execute("fail", session="s")
    assert result.ok is False
    assert "exit code 3" in (result.error or "")


@pytest.mark.asyncio
async def test_cli_provider_unavailable_binary(tmp_path):
    provider = CliAgentProvider(("/definitely/not/a/real/binary",), timeout_s=5)
    assert provider.available() is False
    result = await provider.execute("x", session="s")
    assert result.ok is False
    assert "unavailable" in (result.error or "")


# --- LocalAgentProvider ------------------------------------------------------


@pytest.mark.asyncio
async def test_local_provider_delegates_to_slice(tmp_path):
    from msb_v3.agent.safety import ActionGate

    class _Resp:
        def __init__(self, text: str) -> None:
            self.text = text
            self.model = "fake"
            self.latency_s = 0.0
            self.tool_calls = []

    class _Client:
        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            return _Resp(
                '{"goals": ["research the vault"], "constraints": [], "permissions": ["read_vault"], "privacy": true, "domain": "client-brief"}'
            )

    class _Provider:
        async def run_tool(self, name, *, task, inputs, session):
            if name == "search_query":
                return [{"text": "source one", "source": "vault/a.md"}]
            if name == "chat":
                return "the brief"
            raise ValueError(name)

    provider = LocalAgentProvider(client=_Client(), provider=_Provider(), gate=ActionGate())
    result = await provider.execute("research the vault", session="s")
    assert result.ok is True
    assert result.artifacts.get("deterministic_hash")


# --- ProviderRegistry --------------------------------------------------------


def test_registry_select_filters_by_capability_and_risk(tmp_path):
    local = LocalAgentProvider()
    cli = CliAgentProvider((_write_script(tmp_path, "w.py", _OK_SCRIPT),), timeout_s=5)
    reg = ProviderRegistry((local, cli))
    assert reg.get("local.slice") is local
    assert reg.get("cli.w.py") is cli  # id derives from the binary name

    # requires a capability only the local slice carries
    chosen = reg.select(required_capabilities=("vault_write",), max_risk_tier=3)
    assert [p.spec.provider_id for p in chosen] == ["local.slice"]
    # cli is HIGH risk — excluded below tier 4
    assert reg.select(max_risk_tier=2) == []
    assert reg.select(max_risk_tier=4) == [local, cli]


def test_registry_list_shape(tmp_path):
    reg = ProviderRegistry((CliAgentProvider((_write_script(tmp_path, "w.py", _OK_SCRIPT),), timeout_s=5),))
    listing = reg.list()
    assert listing[0]["kind"] == "cli"
    assert listing[0]["available"] is True


# --- handle() agent delegation -----------------------------------------------


@pytest.fixture()
def registry(tmp_path):
    return AgentRegistry(db_path=str(tmp_path / "agents.db"))


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class _SequenceClient:
    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        text = self._texts.pop(0) if self._texts else "garbage"
        return _Resp(text)


_INTENT_WITH_WRITE = (
    '{"goals": ["research the vault"], "constraints": [], '
    '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
)


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component, event_type, payload) -> None:
        self.events.append((component, event_type, payload))


class _FakeWriteProvider:
    def __init__(self, tmp_path) -> None:
        self._tmp = tmp_path

    async def run_tool(self, name, *, task, inputs, session):
        if name == "search_query":
            return [{"text": "source one", "source": "vault/a.md"}]
        if name == "chat":
            return "The client-ready brief."
        if name == "vault_write":
            (self._tmp / "brief.md").write_text("# Brief\n\nbrief\n")
            return {"path": str(self._tmp / "brief.md"), "heading": "# Brief"}
        raise ValueError(name)


@pytest.mark.asyncio
async def test_handle_unknown_agent_errors(tmp_path, registry):
    from msb_v3.agent.handle import handle

    result = await handle("do the thing", agent_id="ghost", registry=registry)
    assert result.ok is False
    assert "unknown agent" in (result.error or "")


@pytest.mark.asyncio
async def test_handle_revoked_agent_errors(tmp_path, registry):
    from msb_v3.agent.handle import handle

    registry.register(AgentIdentity(agent_id="retired", name="r", kind="local", provider_id="local.slice"))
    registry.revoke("retired")
    result = await handle("do the thing", agent_id="retired", registry=registry)
    assert result.ok is False
    assert "revoked" in (result.error or "")


@pytest.mark.asyncio
async def test_handle_local_agent_respects_capability_grant(tmp_path, registry):
    """An agent granted only read access must be blocked from the write."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.safety import ActionGate

    registry.register(
        AgentIdentity(
            agent_id="read_only",
            name="reader",
            kind="local",
            provider_id="local.slice",
            granted_capabilities=("read_vault",),  # no llm_synthesis / write_file
        )
    )
    result = await handle(
        "research the vault and write a client brief",
        client=_SequenceClient(_INTENT_WITH_WRITE),
        approve=True,
        provider=_FakeWriteProvider(tmp_path),
        gate=ActionGate(audit_chain=_Audit()),
        agent_id="read_only",
        registry=registry,
    )
    assert result.ok is False
    assert "not granted" in (result.error or "")


@pytest.mark.asyncio
async def test_handle_local_agent_full_grant_passes(tmp_path, registry):
    from msb_v3.agent.handle import handle
    from msb_v3.agent.safety import ActionGate

    registry.register(
        AgentIdentity(
            agent_id="full_agent",
            name="worker",
            kind="local",
            provider_id="local.slice",
            granted_capabilities=("read_vault", "llm_synthesis", "write_file"),
        )
    )
    result = await handle(
        "research the vault and write a client brief",
        client=_SequenceClient(_INTENT_WITH_WRITE),
        approve=True,
        provider=_FakeWriteProvider(tmp_path),
        gate=ActionGate(audit_chain=_Audit()),
        agent_id="full_agent",
        registry=registry,
    )
    assert result.ok is True
    assert result.verdict == "PASS"


@pytest.mark.asyncio
async def test_handle_cli_agent_delegates_whole_task(tmp_path, registry):
    """A CLI worker gets the whole task as a bounded subprocess — and, like
    Paseo workers, starts from a composed Context Engine package whose
    ledger is persisted to the Memory Fabric after the run."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import ProviderRegistry
    from msb_v3.fabric.context_engine import ContextPackage, LayerResult
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    script = _write_script(tmp_path, "worker.py", _OK_SCRIPT)
    goal_seen: dict = {}

    class _FakeContextEngine:
        def compose(self, task, *, tenant="default", session="default", repo=None, **kwargs):
            return ContextPackage(
                text="SYS INVARIANTS\nTask: %s" % task,
                budget_tokens=4000,
                total_tokens=8,
                naive_tokens=60,
                layers=[
                    LayerResult("L0", "system-invariants", "SYS INVARIANTS", included_tokens=2),
                    LayerResult("L1", "task", "Task: %s" % task, included_tokens=6),
                ],
            )

    class _FakeMemoryFabric:
        def __init__(self) -> None:
            self.stored: list = []

        def store_memory(self, content, *, type_=None, tags=None, importance=0.5, source_agent="", source="", task_id="", tenant="default", project="", tech="", decay_factor=0.9, relationships=None, memory_id=None):
            self.stored.append(
                {"content": content, "type": type_, "tags": tags, "importance": importance, "source_agent": source_agent, "source": source, "task_id": task_id, "tenant": tenant, "project": project}
            )
            return type("Item", (), {"memory_id": "mem-cli-1"})()

        def consolidate(self, tenant="default", *, by="system"):
            return {"tenant": tenant, "merged": 0, "deprecations": [], "decayed": 0, "kept": 1}

    # Patch the provider's execute so we can see the exact goal handed to
    # the subprocess (the script echoes argv[-1], but this pins the wiring).
    provider = CliAgentProvider((script,), provider_id="cli.w", timeout_s=10)
    original_execute = provider.execute

    async def _capturing_execute(goal, *, context=None, session="default"):
        goal_seen["goal"] = goal
        goal_seen["context"] = context
        return await original_execute(goal, context=context, session=session)

    provider.execute = _capturing_execute  # type: ignore[method-assign]

    fabric = _FakeMemoryFabric()
    registry.register(
        AgentIdentity(agent_id="cli_w1", name="cli worker", kind="cli", provider_id="cli.w")
    )
    providers = ProviderRegistry((provider,))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    result = await handle(
        "write a report",
        agent_id="cli_w1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        repo="/tmp/cli-repo",
        context_engine=_FakeContextEngine(),
        memory_fabric=fabric,
    )
    assert result.ok is True
    assert result.verdict == "PASS"
    assert result.trace["provider"] == "cli.w"
    assert result.trace["agent_id"] == "cli_w1"
    # The subprocess goal starts with the composed context and ends with
    # the raw task.
    assert goal_seen["goal"].startswith("SYS INVARIANTS")
    assert goal_seen["goal"].endswith("TASK: write a report")
    # The composed ledger rode in the provider context and the architectural
    # memory was persisted with full provenance.
    assert goal_seen["context"]["composed_context"]["total_tokens"] == 8
    assert len(fabric.stored) == 1
    mem = fabric.stored[0]
    assert "cli.w" in mem["tags"]
    assert mem["source_agent"] == "cli_w1"
    assert mem["task_id"] == result.run_id
    record = lifecycle.get(result.run_id)
    types = [e["event_type"] for e in record["events"]]
    assert "CONTEXT_COMPOSED" in types
    assert "MEMORY_STORED" in types
    assert "MEMORY_CONSOLIDATED" in types
    # The worker's stdout streamed into the task as observations.
    assert "OBSERVATION_RECORDED" in types
    obs = record["task"]["observations"]
    assert any(o["source"] == "cli.output" and o["content"] == "WORKER OK" for o in obs)
    # The real subprocess still ran (the worker wrote its artifact).
    assert "result.txt" in result.trace.get("artifacts", {})


@pytest.mark.asyncio
async def test_handle_paseo_agent_delegates_with_repo_and_lifecycle(tmp_path, registry):
    """A Paseo worker gets the whole task; `repo` flows to the provider; the
    lifecycle walks the legal transition path (the delegation *is* the plan)."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )
    from msb_v3.fabric.context_engine import ContextPackage
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    captured: dict = {}

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            captured["context"] = context
            return ProviderResult(ok=True, output="worker done", artifacts={"paseo_agent_id": "a-1"})

    class _FakeContextEngine:
        def compose(self, task, **kwargs):
            return ContextPackage(text="ctx: %s" % task, budget_tokens=100, total_tokens=4, naive_tokens=20)

    class _FakeMemoryFabric:
        def store_memory(self, *args, **kwargs):
            return type("Item", (), {"memory_id": "mem-p1"})()

        def consolidate(self, tenant="default", *, by="system"):
            return {"tenant": tenant, "merged": 0, "deprecations": [], "decayed": 0, "kept": 1}

    registry.register(
        AgentIdentity(agent_id="p1", name="p", kind="paseo", provider_id="paseo.claude")
    )
    providers = ProviderRegistry((_FakePaseoProvider(),))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    result = await handle(
        "do it",
        agent_id="p1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        repo="/tmp/target-repo",
        context_engine=_FakeContextEngine(),
        memory_fabric=_FakeMemoryFabric(),
    )
    assert result.ok is True
    assert captured["context"]["repo"] == "/tmp/target-repo"
    record = lifecycle.get(result.run_id)
    assert record["state"] == "COMPLETED"
    types = [e["event_type"] for e in record["events"]]
    ordered = [t for t in types if t in ("TASK_CREATED", "PLAN_CREATED", "AGENT_STARTED", "AGENT_COMPLETED", "VERIFICATION_STARTED", "VERIFICATION_PASSED", "TASK_COMPLETED")]
    assert ordered == ["TASK_CREATED", "PLAN_CREATED", "AGENT_STARTED", "AGENT_COMPLETED", "VERIFICATION_STARTED", "VERIFICATION_PASSED", "TASK_COMPLETED"]


@pytest.mark.asyncio
async def test_handle_paseo_delegation_composes_context(tmp_path, registry):
    """A Paseo run starts from a composed Context Engine package: the goal
    handed to the worker is the composed context + the task, the ledger is
    recorded as a CONTEXT_COMPOSED event, and the composition rides in the
    provider context."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )
    from msb_v3.fabric.context_engine import ContextPackage, LayerResult
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    captured: dict = {}

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            captured["goal"] = goal
            captured["context"] = context
            return ProviderResult(ok=True, output="worker done")

    class _FakeContextEngine:
        def compose(self, task, *, tenant="default", session="default", repo=None, **kwargs):
            return ContextPackage(
                text="SYSTEM INVARIANTS\n---\nTask: %s" % task,
                budget_tokens=4000,
                total_tokens=12,
                naive_tokens=80,
                layers=[
                    LayerResult("L0", "system-invariants", "SYSTEM INVARIANTS", included_tokens=3),
                    LayerResult("L1", "task", "Task: %s" % task, included_tokens=9),
                ],
            )

    class _FakeMemoryFabric:
        def __init__(self) -> None:
            self.stored: list = []
            self.consolidations: list = []

        def store_memory(self, content, *, type_=None, tags=None, importance=0.5, source_agent="", source="", task_id="", tenant="default", project="", tech="", decay_factor=0.9, relationships=None, memory_id=None):
            self.stored.append(
                {"content": content, "type": type_, "tags": tags, "importance": importance, "source_agent": source_agent, "source": source, "task_id": task_id, "tenant": tenant, "project": project}
            )
            return type("Item", (), {"memory_id": "mem-ctx-1"})()

        def consolidate(self, tenant="default", *, by="system"):
            self.consolidations.append((tenant, by))
            return {"tenant": tenant, "merged": 1, "deprecations": ["mem-ctx-0"], "decayed": 0, "kept": 2}

    fabric = _FakeMemoryFabric()
    registry.register(
        AgentIdentity(agent_id="p2", name="p", kind="paseo", provider_id="paseo.claude")
    )
    providers = ProviderRegistry((_FakePaseoProvider(),))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    result = await handle(
        "fix the auth bug",
        agent_id="p2",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        repo="/tmp/repo",
        context_engine=_FakeContextEngine(),
        memory_fabric=fabric,
    )
    assert result.ok is True
    # The worker goal starts with the composed context and ends with the task.
    assert captured["goal"].startswith("SYSTEM INVARIANTS")
    assert captured["goal"].endswith("TASK: fix the auth bug")
    # The composition ledger rides in the provider context.
    assert captured["context"]["composed_context"]["total_tokens"] == 12
    # The lifecycle records the composition.
    record = lifecycle.get(result.run_id)
    types = [e["event_type"] for e in record["events"]]
    assert "CONTEXT_COMPOSED" in types
    composed = record["task"]["context"]["composed"]
    assert composed["reduction_pct"] == 85.0
    # The CONTEXT_COMPOSED consumer persisted an architectural memory
    # after the run, with full provenance.
    assert len(fabric.stored) == 1
    mem = fabric.stored[0]
    assert str(mem["type"]) == "MemoryType.ARCHITECTURAL" or "architectural" in str(mem["type"])
    assert mem["content"] == "SYSTEM INVARIANTS\n---\nTask: fix the auth bug"
    assert mem["source_agent"] == "p2"
    assert mem["task_id"] == result.run_id
    assert mem["tenant"] == "wilson-vault"
    assert mem["project"] == "/tmp/repo"
    assert "context-composed" in mem["tags"]
    assert "MEMORY_STORED" in types
    # The consolidation pass ran against the run's tenant and its honest
    # report landed as a MEMORY_CONSOLIDATED event.
    assert fabric.consolidations == [("wilson-vault", "delegation")]
    assert "MEMORY_CONSOLIDATED" in types
    con = [e for e in record["events"] if e["event_type"] == "MEMORY_CONSOLIDATED"][0]
    assert con["payload"]["merged"] == 1
    assert con["payload"]["deprecations"] == ["mem-ctx-0"]


@pytest.mark.asyncio
async def test_handle_paseo_context_compose_failure_is_best_effort(tmp_path, registry):
    """A failing context engine never breaks the run — the worker gets the
    raw request and the run completes normally."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )

    captured: dict = {}

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            captured["goal"] = goal
            return ProviderResult(ok=True, output="worker done")

    class _BoomEngine:
        def compose(self, *args, **kwargs):
            raise RuntimeError("qdrant exploded")

    registry.register(
        AgentIdentity(agent_id="p3", name="p", kind="paseo", provider_id="paseo.claude")
    )
    providers = ProviderRegistry((_FakePaseoProvider(),))
    result = await handle(
        "do it anyway",
        agent_id="p3",
        registry=registry,
        providers=providers,
        context_engine=_BoomEngine(),
    )
    assert result.ok is True
    assert captured["goal"] == "do it anyway"  # degraded to the raw request
    assert "composed_context" not in captured.get("context", {})


@pytest.mark.asyncio
async def test_handle_paseo_memory_persist_failure_is_best_effort(tmp_path, registry):
    """A failing Memory Fabric never breaks the run — the composed context
    is simply not persisted (no MEMORY_STORED event) and the run completes."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )
    from msb_v3.fabric.context_engine import ContextPackage
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            return ProviderResult(ok=True, output="worker done")

    class _FakeContextEngine:
        def compose(self, task, **kwargs):
            return ContextPackage(text="ctx for %s" % task, budget_tokens=100, total_tokens=4, naive_tokens=20)

    class _BoomFabric:
        def store_memory(self, *args, **kwargs):
            raise RuntimeError("disk full")

    registry.register(
        AgentIdentity(agent_id="p4", name="p", kind="paseo", provider_id="paseo.claude")
    )
    providers = ProviderRegistry((_FakePaseoProvider(),))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    result = await handle(
        "do it",
        agent_id="p4",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        context_engine=_FakeContextEngine(),
        memory_fabric=_BoomFabric(),
    )
    assert result.ok is True
    record = lifecycle.get(result.run_id)
    types = [e["event_type"] for e in record["events"]]
    assert "MEMORY_STORED" not in types
    assert "CONTEXT_COMPOSED" in types  # composition still recorded


@pytest.mark.asyncio
async def test_handle_paseo_consolidation_failure_is_best_effort(tmp_path, registry):
    """A failing consolidation pass never breaks the run — the memory is
    stored and MEMORY_STORED fires, but MEMORY_CONSOLIDATED is absent."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )
    from msb_v3.fabric.context_engine import ContextPackage
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            return ProviderResult(ok=True, output="worker done")

    class _FakeContextEngine:
        def compose(self, task, **kwargs):
            return ContextPackage(text="ctx for %s" % task, budget_tokens=100, total_tokens=4, naive_tokens=20)

    class _ConsolidateBoomFabric:
        def store_memory(self, *args, **kwargs):
            return type("Item", (), {"memory_id": "mem-x"})()

        def consolidate(self, *args, **kwargs):
            raise RuntimeError("merge conflict")

    registry.register(
        AgentIdentity(agent_id="p5", name="p", kind="paseo", provider_id="paseo.claude")
    )
    providers = ProviderRegistry((_FakePaseoProvider(),))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    result = await handle(
        "do it",
        agent_id="p5",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        context_engine=_FakeContextEngine(),
        memory_fabric=_ConsolidateBoomFabric(),
    )
    assert result.ok is True
    record = lifecycle.get(result.run_id)
    types = [e["event_type"] for e in record["events"]]
    assert "MEMORY_STORED" in types  # the memory itself persisted fine
    assert "MEMORY_CONSOLIDATED" not in types  # the pass failed quietly


@pytest.mark.asyncio
async def test_handle_cli_agent_unavailable_provider(tmp_path, registry):
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import ProviderRegistry

    registry.register(
        AgentIdentity(agent_id="cli_w2", name="c", kind="cli", provider_id="cli.missing")
    )
    providers = ProviderRegistry((CliAgentProvider(("/no/such/binary",), provider_id="cli.missing"),))
    result = await handle("x", agent_id="cli_w2", registry=registry, providers=providers)
    assert result.ok is False
    assert "unavailable" in (result.error or "")


@pytest.mark.asyncio
async def test_handle_delegated_agent_emits_spine_vertebrae(tmp_path, registry):
    """Phase 2.3: a delegated worker's MoIE inversion gate is the governed
    decision — the Evidence Spine records decision -> execution ->
    verification, each vertebra linked back to the decision."""
    from msb_v3.agent.handle import handle
    from msb_v3.agent.providers import (
        AgentProvider,
        ProviderRegistry,
        ProviderResult,
        ProviderSpec,
    )
    from msb_v3.evidence.spine import DecisionEvidenceStore
    from msb_v3.tasks.lifecycle import TaskLifecycle
    from msb_v3.uac.audit_chain import AuditChain

    class _FakeMoIE:
        def analyze(self, claim, context=None):
            return type(
                "D",
                (),
                {
                    "as_dict": lambda self: {
                        "verdict": "APPROVE",
                        "blocked": False,
                        "confidence": 0.9,
                        "assumptions": [],
                        "contradictions": [],
                    }
                },
            )()

    class _FakePaseoProvider(AgentProvider):
        spec = ProviderSpec(provider_id="paseo.claude", display_name="p", kind="paseo", max_risk_tier=4)

        def available(self) -> bool:
            return True

        async def execute(self, goal, *, context=None, session="default"):
            return ProviderResult(ok=True, output="worker done")

    registry.register(AgentIdentity(agent_id="spine_p", name="p", kind="paseo", provider_id="paseo.claude"))
    providers = ProviderRegistry((_FakePaseoProvider(),))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))

    result = await handle(
        "do the delegated thing",
        agent_id="spine_p",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        moie=_FakeMoIE(),
        spine=spine,
    )

    assert result.ok is True
    trail = spine.trail(result.run_id)
    assert [r.evidence.kind for r in trail] == ["decision", "execution", "verification"]
    decision, execution, verification = trail
    assert decision.evidence.policy_result == "ALLOW"
    assert decision.evidence.selected_action == "delegate"
    assert decision.evidence.agent_id == "spine_p"
    assert decision.evidence.provider == "paseo.claude"
    assert execution.evidence.parent_decision_id == decision.decision_id
    assert verification.evidence.parent_decision_id == decision.decision_id
    assert verification.evidence.verification_id  # the worker output digest
    assert spine.verify_chain()["valid"] is True
