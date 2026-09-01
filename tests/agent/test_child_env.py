from msb_v3.agent.providers import _CHILD_ENV_PASSTHROUGH, _child_env


def test_passthrough_present(monkeypatch):
    monkeypatch.setenv("PATH", "/x")
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    result = _child_env("/wt", "sess")
    assert result["PATH"] == "/x"
    assert result["OLLAMA_API_KEY"] == "k"


def test_unset_key_absent(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    result = _child_env("/wt", "sess")
    assert "LC_ALL" not in result


def test_no_none_values(monkeypatch):
    result = _child_env("/wt", "sess")
    assert None not in result.values()


def test_secrets_never_leak(monkeypatch):
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", "secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    result = _child_env("/wt", "sess")
    assert "MSB_OPERATOR_TOKEN" not in result
    assert "DEEPSEEK_API_KEY" not in result


def test_markers_always_set():
    result = _child_env("/wt", "sess")
    assert result["MSB_WORKTREE"] == "/wt"
    assert result["MSB_SESSION"] == "sess"


def test_only_allowlisted_or_marker_keys():
    result = _child_env("/wt", "sess")
    allowed = set(_CHILD_ENV_PASSTHROUGH) | {"MSB_WORKTREE", "MSB_SESSION"}
    assert set(result.keys()) <= allowed


def test_malloc_debug_vars_never_pass(monkeypatch):
    monkeypatch.setenv("MallocStackLogging", "1")
    monkeypatch.setenv("MallocScribble", "1")
    monkeypatch.setenv("NSZombieEnabled", "YES")
    result = _child_env("/wt", "sess")
    assert "MallocStackLogging" not in result
    assert "MallocScribble" not in result
    assert "NSZombieEnabled" not in result


def test_scrub_debug_env_is_reusable():
    from msb_v3.agent.providers import scrub_debug_env

    dirty = {"PATH": "/x", "MallocStackLogging": "1", "NSZombieEnabled": "1", "FOO": "bar"}
    clean = scrub_debug_env(dirty)
    assert clean == {"PATH": "/x", "FOO": "bar"}
