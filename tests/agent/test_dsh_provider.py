"""DshAgentProvider — DeepSeek Harness (dsh) as a governed subprocess worker.

Hermetic by construction: the dsh command is injected as a fake script in a
tmp dir — no real dsh / npx binary is ever invoked. Pins the same two halves
of the harness bar as the cli/DeepSeek/Anthropic providers:

1. ``DshAgentProvider`` runs ``dsh --profile headless <goal>`` in an isolated
   worktree, captures output (bounded), kills on timeout, and retrieves
   artifacts — fail-closed on a missing binary or nonzero exit.
2. The provider is registered behind the ``AgentProvider`` ABC and selected
   through ``ProviderRegistry`` — same seam, no governance fork.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.agent.providers import DshAgentProvider, ProviderRegistry, default_providers
from msb_v3.core.config import settings


def _write_script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


_OK_SCRIPT = """#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
assert "--profile" in args and args[args.index("--profile") + 1] == "headless"
worktree = os.environ.get("MSB_WORKTREE", ".")
with open(os.path.join(worktree, "result.txt"), "w") as f:
    f.write("dsh produced: " + args[-1])
print("DSH OK", flush=True)
"""

_SLEEP_SCRIPT = "#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n"

_FAIL_SCRIPT = "#!/usr/bin/env python3\nimport sys\nprint('boom', file=sys.stderr)\nsys.exit(3)\n"


# --- DshAgentProvider (hermetic) --------------------------------------------


@pytest.mark.asyncio
async def test_dsh_provider_runs_headless_in_worktree(tmp_path):
    provider = DshAgentProvider(command=(_write_script(tmp_path, "dsh.py", _OK_SCRIPT),), timeout_s=10)
    assert provider.available() is True
    result = await provider.execute("build the thing", session="s")
    assert result.ok is True
    assert "DSH OK" in result.output
    assert "result.txt" in result.artifacts  # artifact retrieved from the worktree


@pytest.mark.asyncio
async def test_dsh_provider_timeout_kills_worker(tmp_path):
    provider = DshAgentProvider(command=(_write_script(tmp_path, "sleepy.py", _SLEEP_SCRIPT),), timeout_s=1)
    result = await provider.execute("do nothing", session="s")
    assert result.ok is False
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_dsh_provider_streams_output_to_observation_sink(tmp_path):
    """Each non-empty stdout line is streamed as it is produced — source
    dsh.output, incrementing update_count, timestamps — while the final
    output stays intact."""
    script = _write_script(
        tmp_path, "talker.py",
        "#!/usr/bin/env python3\nimport time\nfor line in [\"first line\", \"second line\", \"third line\"]:\n    print(line, flush=True)\n    time.sleep(0.05)\n",
    )
    provider = DshAgentProvider(command=(script,), timeout_s=10)
    samples = []

    async def sink(sample):
        samples.append(sample)

    result = await provider.execute("say stuff", context={"observation_sink": sink}, session="s")
    assert result.ok is True
    assert [s["content"] for s in samples] == ["first line", "second line", "third line"]
    assert all(s["source"] == "dsh.output" for s in samples)
    assert [s["update_count"] for s in samples] == [1, 2, 3]
    assert all(s.get("observed_at") for s in samples)
    assert "first line" in result.output


@pytest.mark.asyncio
async def test_dsh_provider_sink_failure_is_best_effort(tmp_path):
    """A failing observation sink never breaks the worker run — output is
    still captured and the result is unaffected."""
    script = _write_script(tmp_path, "one_liner.py", "#!/usr/bin/env python3\nprint('hello', flush=True)\n")
    provider = DshAgentProvider(command=(script,), timeout_s=10)

    async def bad_sink(sample):
        raise RuntimeError("sink exploded")

    result = await provider.execute("hi", context={"observation_sink": bad_sink}, session="s")
    assert result.ok is True
    assert result.output.strip() == "hello"


@pytest.mark.asyncio
async def test_dsh_provider_nonzero_exit_is_failure(tmp_path):
    provider = DshAgentProvider(command=(_write_script(tmp_path, "fail.py", _FAIL_SCRIPT),), timeout_s=10)
    result = await provider.execute("fail", session="s")
    assert result.ok is False
    assert "exit code 3" in (result.error or "")


@pytest.mark.asyncio
async def test_dsh_provider_unavailable_binary(monkeypatch):
    """With no resolvable dsh binary the provider is honestly unavailable and
    refuses to execute, rather than failing at spawn time."""
    monkeypatch.setattr(settings, "dsh_binary", "/definitely/not/a/real/dsh")
    provider = DshAgentProvider()
    assert provider.available() is False
    assert "not on PATH" in provider.unavailable_reason()
    result = await provider.execute("x", session="s")
    assert result.ok is False
    assert "unavailable" in (result.error or "")


def test_dsh_provider_resolves_shlex_prefix(monkeypatch, tmp_path):
    """DSH_BINARY may be a space-separated prefix (npx @deepseek-ai/dsh);
    shlex resolves it to tokens and availability probes the first token."""
    fake_npx = tmp_path / "npx"
    fake_npx.write_text("#!/bin/sh\n")
    fake_npx.chmod(0o755)
    monkeypatch.setattr(settings, "dsh_binary", f"{fake_npx} @deepseek-ai/dsh")
    provider = DshAgentProvider()
    assert provider._resolve_command() == (str(fake_npx), "@deepseek-ai/dsh")
    assert provider.available() is True


def test_dsh_provider_registered_behind_seam():
    """The provider is part of the default registry — selected by kind/id,
    never hardcoded in consumers."""
    reg = ProviderRegistry()
    provider = reg.get("dsh.headless")
    assert provider is not None
    assert isinstance(provider, DshAgentProvider)
    assert any(p.spec.provider_id == "dsh.headless" for p in default_providers())


def test_dsh_provider_spec_risk_and_kind():
    """A subprocess agent on the operator's account is tier 4 (HIGH), not
    tier 1 — the registry must be able to exclude it on risk alone."""
    provider = DshAgentProvider()
    assert provider.spec.kind == "dsh"
    assert provider.spec.max_risk_tier == 4
    assert provider.capabilities() == ()
