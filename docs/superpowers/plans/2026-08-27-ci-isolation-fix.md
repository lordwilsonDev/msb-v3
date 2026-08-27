# CI Isolation Fix — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Get all three CI gates (`msb-v3 CI`, `factory-gate`, `harness-gate`) green on `main` by making the run-scoped CI server isolate its *own* environment without polluting the pytest process environment, and making the ~9 live-test files discover the server's port through one channel.

**Architecture:** The 2026-08-27 convergence WIP replaced the hardcoded `:8766` CI boot with a run-scoped server (free port + private DB) from `scripts/ci-runtime.sh`. Its flaw: `ci_runtime_init` `export`s `MSB_PORT` / `MSB_DB_PATH` / `MSB_RESEARCH_ROOT` / `CI_RUNTIME_DIR` into the shell that then runs the whole suite, so every test inherits a redirected config. Fix: `ci_runtime_start_server` launches the server subprocess with a *scoped* env and exports only `MSB_BASE_URL` for the suite; tests read `MSB_BASE_URL` (default `http://127.0.0.1:8766`) via a shared `tests/conftest.py` helper.

**Tech Stack:** bash, pytest, httpx, FastAPI TestClient, GitHub Actions, ruff/mypy, miniforge python at `/opt/homebrew/Caskroom/miniforge/base/bin/python`.

## Global Constraints

- DCO: every commit needs `Signed-off-by: lordwilson <theapexintelligence@gmail.com>` (commit-msg hook enforces).
- Co-author trailer: `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- Ruff selection is `E4,E7,E9,F,I` (pyproject `[tool.ruff.lint]`); `mypy src` must stay 0 errors.
- Pre-push hook runs `make portability` (~6 min) + `make lint`; both must pass. Bypass only with `MSB_SKIP_PORTABILITY=1` and only when explicitly agreed.
- Dual remotes `origin` + `sovereign_intelligence_core` point at the same GitHub repo; push both.
- Do NOT commit `.plei/calibration.jsonl` or `artifacts/hygiene/daily_gate_events.jsonl` (runtime noise).
- `python` is NOT on the self-hosted runner PATH — use `python3` or `${MSB_PYTHON}`.
- Speech + energy_matrix are EXPERIMENTAL (`docs/governance/production-boundary-2026-08-27.md`); CI must not hard-depend on their C extensions.

**Current state:** origin/main = `e0f708f` (20 convergence/speech commits pushed, all 3 gates red). Nothing tagged; last tag `v0.3.2` = `bf27f6a` (also was red). Target after this plan: green → `scripts/release.sh` tags `v0.3.3`.

---

## Task 1: Scope the CI server env; stop polluting the pytest shell

**Files:**
- Modify: `scripts/ci-runtime.sh`
- Modify: `tests/test_ci_runtime.py`

**Interfaces:**
- Produces: after `ci_runtime_init` + `ci_runtime_start_server`, the only msb-v3 env var exported to the caller is `MSB_BASE_URL=http://127.0.0.1:<port>`. `CI_RUNTIME_DIR`, `CI_SERVER_PID` still exported (cleanup needs them). `MSB_PORT` / `MSB_DB_PATH` / `MSB_RESEARCH_ROOT` are passed only to the server subprocess, NOT exported to the shell.

- [ ] **Step 1: Rewrite `ci_runtime_init`** — compute values into locals, export only what cleanup needs:

```bash
ci_runtime_init() {
  : "${RUNNER_TEMP:=/tmp}"
  CI_RUNTIME_DIR="${CI_RUNTIME_DIR:-$(mktemp -d "$RUNNER_TEMP/msb-v3-ci-XXXXXX")}"
  mkdir -p "$CI_RUNTIME_DIR"
  export CI_RUNTIME_DIR
  # Scoped server config — NOT exported to the caller's shell (that would
  # redirect every test's Settings). Kept in file-scoped vars the
  # start_server step reads.
  CI_SERVER_HOST="${MSB_HOST:-127.0.0.1}"
  if [ -z "${CI_SERVER_PORT:-}" ]; then
    CI_SERVER_PORT="$(python3 - <<'PY'
import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])
PY
)"
  fi
  CI_SERVER_DB="$CI_RUNTIME_DIR/msb.db"
  CI_SERVER_RESEARCH="$CI_RUNTIME_DIR/research"
  mkdir -p "$CI_SERVER_RESEARCH"
  export CI_SERVER_HOST CI_SERVER_PORT CI_SERVER_DB CI_SERVER_RESEARCH
  echo "[ci-runtime] dir=$CI_RUNTIME_DIR port=$CI_SERVER_PORT db=$CI_SERVER_DB"
}
```

- [ ] **Step 2: Rewrite `ci_runtime_start_server`** — pass config to the subprocess env only, export `MSB_BASE_URL` for the suite:

```bash
ci_runtime_start_server() {
  : "${CI_RUNTIME_DIR:?call ci_runtime_init first}"
  CI_SERVER_PYTHON="${CI_SERVER_PYTHON:-${MSB_PYTHON:-python3}}"
  export CI_SERVER_PYTHON
  env MSB_HOST="$CI_SERVER_HOST" MSB_PORT="$CI_SERVER_PORT" \
      MSB_DB_PATH="$CI_SERVER_DB" MSB_RESEARCH_ROOT="$CI_SERVER_RESEARCH" \
      "$CI_SERVER_PYTHON" -m msb_v3 >"$CI_RUNTIME_DIR/server.log" 2>&1 &
  CI_SERVER_PID=$!
  printf '%s\n' "$CI_SERVER_PID" >"$CI_RUNTIME_DIR/server.pid"
  export CI_SERVER_PID
  export MSB_BASE_URL="http://127.0.0.1:${CI_SERVER_PORT}"
  trap 'ci_runtime_cleanup' EXIT
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$MSB_BASE_URL/health"; then
      echo "[ci-runtime] server healthy pid=$CI_SERVER_PID port=$CI_SERVER_PORT"
      return 0
    fi
    if ! kill -0 "$CI_SERVER_PID" 2>/dev/null; then
      echo "[ci-runtime] server exited; log follows" >&2
      cat "$CI_RUNTIME_DIR/server.log" >&2 || true
      return 1
    fi
    sleep 1
  done
  echo "[ci-runtime] server did not become healthy; log follows" >&2
  cat "$CI_RUNTIME_DIR/server.log" >&2 || true
  return 1
}
```

`ci_runtime_cleanup` is unchanged (already PID-file-owned + idempotent).

- [ ] **Step 3: Update `tests/test_ci_runtime.py`** — the port/paths test now checks `CI_SERVER_*` not `MSB_*`, and forces a clean env so a parent `ci_runtime_init` (the workflow step) can't leak in:

```python
def test_runtime_init_allocates_port_and_private_paths(tmp_path: Path) -> None:
    command = (
        f"source {SCRIPT}; ci_runtime_init; "
        f"printf '%s\\n' \"$CI_SERVER_PORT\" \"$CI_SERVER_DB\" \"$CI_RUNTIME_DIR\""
    )
    clean = {k: v for k, v in os.environ.items()
             if k not in {"CI_RUNTIME_DIR", "CI_SERVER_PORT", "MSB_PORT",
                          "MSB_DB_PATH", "MSB_RESEARCH_ROOT"}}
    clean["RUNNER_TEMP"] = str(tmp_path)
    result = subprocess.run(["bash", "-c", command], env=clean,
                            capture_output=True, text=True, check=True)
    port, db_path, runtime_dir = result.stdout.strip().splitlines()[-3:]
    assert 1024 <= int(port) <= 65535
    assert db_path.startswith(runtime_dir)
    assert runtime_dir.startswith(str(tmp_path))


def test_init_does_not_export_msb_config_to_the_shell(tmp_path: Path) -> None:
    """The pytest shell must keep default Settings — ci_runtime_init only
    scopes the server subprocess. Regression: 2026-08-27 `assert 58665 == 8766`."""
    command = (
        f"source {SCRIPT}; ci_runtime_init; "
        f"printf 'PORT=[%s] DB=[%s] RR=[%s]\\n' "
        f"\"${{MSB_PORT:-}}\" \"${{MSB_DB_PATH:-}}\" \"${{MSB_RESEARCH_ROOT:-}}\""
    )
    clean = {k: v for k, v in os.environ.items()
             if k not in {"CI_RUNTIME_DIR", "CI_SERVER_PORT", "MSB_PORT",
                          "MSB_DB_PATH", "MSB_RESEARCH_ROOT"}}
    clean["RUNNER_TEMP"] = str(tmp_path)
    result = subprocess.run(["bash", "-c", command], env=clean,
                            capture_output=True, text=True, check=True)
    assert "PORT=[] DB=[] RR=[]" in result.stdout, result.stdout
```

Keep `test_runtime_script_never_discovers_or_kills_port_8766`, `test_start_server_assigns_python_interpreter_not_just_expands`, `test_runtime_cleanup_is_pid_owned_and_idempotent` as-is (the second one already asserts the `CI_SERVER_PYTHON="${...}"` form).

- [ ] **Step 4: Verify**

Run: `bash -n scripts/ci-runtime.sh && PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/test_ci_runtime.py -v`
Expected: syntax OK, all tests PASS.

Run (real boot, isolated): `bash -c 'set -euo pipefail; env -u MSB_PORT -u MSB_DB_PATH source scripts/ci-runtime.sh; ci_runtime_init; ci_runtime_start_server && echo "BASE=$MSB_BASE_URL" && test -z "${MSB_PORT:-}" && echo "shell not polluted"; ci_runtime_cleanup'`
Expected: `server healthy`, `BASE=http://127.0.0.1:<port>`, `shell not polluted`.

- [ ] **Step 5: Commit**

```bash
git add scripts/ci-runtime.sh tests/test_ci_runtime.py
git commit -m "$(printf 'fix(ci): scope run-scoped server env to the subprocess only\n\nci_runtime_init exported MSB_PORT/MSB_DB_PATH/MSB_RESEARCH_ROOT into the\nshell that runs pytest, so every test inherited a redirected Settings\n(assert settings.port == 8766 -> 58665). Now those are passed only to the\n`python -m msb_v3` subprocess; the suite gets MSB_BASE_URL and nothing\nelse. Adds a regression test pinning the no-pollution contract.\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nSigned-off-by: lordwilson <theapexintelligence@gmail.com>')"
```

---

## Task 2: Shared `BASE` helper + point the ~9 live-test files at it

**Files:**
- Modify: `tests/conftest.py` (add helper)
- Modify: `tests/test_harness.py`, `tests/test_skill_router.py`, `tests/triumvirate/test_metrics.py`, `tests/triumvirate/test_triumvirate_lifecycle.py`, `tests/triumvirate/test_guardian_endpoints.py`, `tests/triumvirate/test_argus_resolve.py`, `tests/vesta/test_dev_harness.py`, `tests/device/test_device_client.py`

**Interfaces:**
- Consumes: `MSB_BASE_URL` env (Task 1).
- Produces: `tests.conftest.msb_base_url()` -> `str` (e.g. `"http://127.0.0.1:8766"`).

- [ ] **Step 1: Add the helper to `tests/conftest.py`** (top level, after imports):

```python
import os


def msb_base_url() -> str:
    """Base URL of the msb-v3 server under test.

    CI's run-scoped server exports MSB_BASE_URL with a free port; locally the
    launchd server on :8766 is the default.
    """
    return os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766")
```

- [ ] **Step 2: Run it to confirm it imports**

Run: `PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -c "from tests.conftest import msb_base_url; print(msb_base_url())"`
Expected: `http://127.0.0.1:8766`

- [ ] **Step 3: In each of the 7 files with `BASE = "http://127.0.0.1:8766"`**, replace that line with:

```python
from tests.conftest import msb_base_url

BASE = msb_base_url()
```

(If the file already imports from `tests.conftest`, merge the import.) For `tests/test_harness.py:242` also replace the inline `client.get("http://127.0.0.1:8766/")` with `client.get(f"{BASE}/")`.

- [ ] **Step 4: For `tests/vesta/test_dev_harness.py:86` and `tests/device/test_device_client.py:55`** (the `base_url="http://127.0.0.1:8766",` kwargs), replace the literal with `base_url=msb_base_url(),` and add the import.

- [ ] **Step 5: Leave `tests/automation/test_clients.py` alone** — its `:82`/`:88` occurrences assert a *constructed URL string* for an n8n forwarder workflow, not a live connection. Confirm by reading those two lines; if they are pure string assertions, no change.

- [ ] **Step 6: Verify (against the local launchd :8766 server, which is up)**

Run: `PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/test_harness.py tests/test_skill_router.py tests/triumvirate/ tests/vesta/test_dev_harness.py tests/device/test_device_client.py -q`
Expected: PASS (same as before the change — the default resolves to :8766).

Run: `/opt/homebrew/Caskroom/miniforge/base/bin/python -m ruff check tests/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_harness.py tests/test_skill_router.py tests/triumvirate/ tests/vesta/test_dev_harness.py tests/device/test_device_client.py
git commit -m "$(printf 'test: resolve server base URL from MSB_BASE_URL, not a :8766 literal\n\nThe run-scoped CI server uses a free port; these live-integration files\nhardcoded http://127.0.0.1:8766 and could not reach it. New\ntests.conftest.msb_base_url() reads MSB_BASE_URL and defaults to :8766\nfor the local launchd server.\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nSigned-off-by: lordwilson <theapexintelligence@gmail.com>')"
```

---

## Task 3: Relax `test_core_config_loads` port assertion

**Files:**
- Modify: `tests/test_scaffold.py:23`

**Interfaces:** none.

- [ ] **Step 1: Change the assertion** — `settings.port` is legitimately overridable via `MSB_PORT`; assert the type and the default-or-override, not a fixed literal:

```python
def test_core_config_loads():
    from msb_v3.core.config import settings

    assert settings.ollama_model in {"deepseek-r1:1.5b", "qwen3:latest", "qwen3:8b"}
    assert isinstance(settings.port, int)
    assert settings.port == int(os.environ.get("MSB_PORT", "8766"))
```

Add `import os` at the top of the file if absent.

- [ ] **Step 2: Verify**

Run: `PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/test_scaffold.py -q`
Expected: PASS.

Run: `MSB_PORT=54321 PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/test_scaffold.py::test_core_config_loads -q`
Expected: PASS (proves it follows the override).

- [ ] **Step 3: Commit**

```bash
git add tests/test_scaffold.py
git commit -m "$(printf 'test(scaffold): assert configured port, not the :8766 literal\n\nsettings.port follows MSB_PORT; a hard `== 8766` broke whenever the\nrun-scoped CI server set a free port in the environment.\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nSigned-off-by: lordwilson <theapexintelligence@gmail.com>')"
```

---

## Task 4: `webrtcvad` — optional extra + importorskip in speech tests

**Files:**
- Modify: `pyproject.toml` (`[project.optional-dependencies]`)
- Modify: `tests/speech/test_speech_vad.py`, and any other `tests/speech/*.py` that instantiates `VoiceDetector` / imports a webrtcvad-backed symbol at module load

**Interfaces:** none.

- [ ] **Step 1: Add a `speech` optional extra** to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
speech = [
  "webrtcvad==2.0.10",  # VAD in msb_v3/speech/vad.py — EXPERIMENTAL subsystem, not in the release contract
]
```

- [ ] **Step 2: Guard the speech tests that need it.** At the top of `tests/speech/test_speech_vad.py` (and siblings that hit `VoiceDetector`):

```python
import pytest

pytest.importorskip("webrtcvad", reason="speech VAD is an EXPERIMENTAL extra: pip install -e '.[speech]'")
```

Run `grep -rln "VoiceDetector\|import webrtcvad\|from msb_v3.speech.vad" tests/speech/` to find the full set; add the guard to each that imports a VAD-backed path at collection time.

- [ ] **Step 3: Verify both ways**

Run (have webrtcvad): `PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/speech/ -q`
Expected: PASS (unchanged locally).

Run (simulate CI absence): `PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest tests/speech/test_speech_vad.py -q -p no:cacheprovider --override-ini "addopts=" ` after `pip uninstall -y webrtcvad` in a throwaway venv — OR just trust the `importorskip`. Minimum: confirm `importorskip` line present and `ruff` clean.

Run: `/opt/homebrew/Caskroom/miniforge/base/bin/python -m ruff check tests/speech/ && /opt/homebrew/Caskroom/miniforge/base/bin/python -m mypy src`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml tests/speech/
git commit -m "$(printf 'build: make webrtcvad an optional [speech] extra; importorskip in tests\n\nspeech/vad.py imports webrtcvad (C extension) lazily; it was never a\ndeclared dependency, so fresh CI failed tests/speech/test_speech_vad.py\nwith ModuleNotFoundError. Speech is EXPERIMENTAL and not in the release\ncontract, so it becomes `.[speech]` and its tests importorskip.\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nSigned-off-by: lordwilson <theapexintelligence@gmail.com>')"
```

---

## Task 5: Fix `python: command not found` in harness-gate qdrant preflight

**Files:**
- Modify: `.github/workflows/harness-gate.yml` (the "Qdrant environment contract preflight" step, ~line 68-74)

**Interfaces:** none.

- [ ] **Step 1: Use an explicit interpreter.** The self-hosted runner PATH has no `python`. Change the step's `run:` to resolve the same interpreter the Makefile uses:

```yaml
      - name: Qdrant environment contract preflight
        run: |
          set -euo pipefail
          PY="${MSB_PYTHON:-/opt/homebrew/Caskroom/miniforge/base/bin/python}"
          MSB_QDRANT_ENABLED=1 "$PY" -c 'from msb_v3.infrastructure.qdrant_contract import preflight; r = preflight(); print(r); raise SystemExit(0 if r.ready else 1)'
        env:
          MSB_REPO: ${{ github.workspace }}
          PYTHONPATH: ${{ github.workspace }}/src
```

- [ ] **Step 2: Verify locally** (the self-hosted runner == this Mac):

Run: `MSB_QDRANT_ENABLED=1 PYTHONPATH=src /opt/homebrew/Caskroom/miniforge/base/bin/python -c 'from msb_v3.infrastructure.qdrant_contract import preflight; r=preflight(); print(r); raise SystemExit(0 if r.ready else 1)'`
Expected: prints a `QdrantContract(... classification='PASS' ...)` and exits 0 (Qdrant is up locally on :6333).

Run: `/opt/homebrew/Caskroom/miniforge/base/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/harness-gate.yml')); print('YAML OK')"`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/harness-gate.yml
git commit -m "$(printf 'ci(harness-gate): resolve python explicitly in the qdrant preflight\n\nThe self-hosted runner PATH has no bare `python`; the new preflight step\nexited 127. Use ${MSB_PYTHON:-<miniforge>} like the Makefile.\n\nCo-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>\nSigned-off-by: lordwilson <theapexintelligence@gmail.com>')"
```

---

## Task 6: Full green — push, watch, tag

**Files:** none (integration).

- [ ] **Step 1: Local full gate**

Run: `/opt/homebrew/Caskroom/miniforge/base/bin/python -m ruff check src tests && /opt/homebrew/Caskroom/miniforge/base/bin/python -m mypy src`
Expected: clean.

Run: `make portability`
Expected: `PASS: full suite green from a foreign checkout path`. (Still leans on the local :8766 server — that's fine; hermeticity is Task 7, out of scope for green.)

- [ ] **Step 2: Push both remotes**

```bash
git push origin main
git push sovereign_intelligence_core main
```

- [ ] **Step 3: Watch the three gates** on the new HEAD:

```bash
gh run list -L 6 --json workflowName,status,conclusion,headSha \
  -q '.[] | "\(.headSha[0:7]) \(.status)/\(.conclusion // "-") \(.workflowName)"'
```

Poll until `msb-v3 CI`, `factory-gate`, `harness-gate` are all `completed/success` on HEAD. If any fails: `gh run view <id> --log-failed`, diagnose, fix, repeat. Do NOT proceed to tag while any gate is red.

- [ ] **Step 4: Tag v0.3.3**

```bash
bash scripts/release.sh
```

(This runs `scripts/verify-release.sh` locally pre-tag, bumps/tags per the repo's release flow, and `release-verify.yml` runs post-tag. The tag-immutability ruleset locks it once created.)

- [ ] **Step 5: Update the vault**

In `~/Documents/Vault/10_Projects/MSB-v3.md`, add a dated line under the 2026-08-27 re-check thread: runway cleared — 20 convergence/speech commits + CI-isolation fix pushed, all 3 gates green, tagged v0.3.3. Bump `updated:`.

---

## Task 7 (OPTIONAL, separate session): make `make portability` hermetic

**Files:** `scripts/portability-check.sh`

Not required for green. Today the portability gate's live-integration tests silently rely on the launchd :8766 server; a truly hermetic gate would `source scripts/ci-runtime.sh; ci_runtime_init; ci_runtime_start_server` before `bash scripts/test.sh` and tear it down after. Risk: the staged copy's server needs its deps + a writable DB dir; getting this wrong turns the gate red for infra reasons. Do this deliberately, on its own, after v0.3.3 is out.

---

## Self-Review

- **Spec coverage:** all four CI root causes have a task — env pollution (T1), hardcoded `:8766` (T2), `settings.port` assertion (T3), `webrtcvad` (T4), self-hosted python path (T5); integration + tag (T6); hermeticity explicitly deferred (T7).
- **Placeholder scan:** every code step has concrete content; the one "find the full set" step (T4 S2) includes the exact `grep` to run.
- **Type consistency:** `msb_base_url()` name used identically in T2 S1/S3/S4; `CI_SERVER_PORT`/`CI_SERVER_DB` names consistent between `ci_runtime_init` and `ci_runtime_start_server` and the T1 S3 test.
