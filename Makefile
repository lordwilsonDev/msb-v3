.PHONY: test lint policy-gate deps portability env-drift server server-start server-stop server-status smoke vesta-loopback hygiene webcheck webcheck-desktop webcheck-all harness-gate-dryrun qdrant qdrant-start qdrant-stop qdrant-status qdrant-sweep backup restore backup-verify hooks-install hooks-uninstall governance-status governance-arm governance-disarm governance-approvals governance-approve governance-reject governance-config governance-token provision-models setup flywheel-turn flywheel-status flywheel-approve flywheel-config

REPO := $(shell pwd)
PY := /opt/homebrew/Caskroom/miniforge/base/bin/python

export VIRTUAL_ENV :=
export PATH := /opt/homebrew/Caskroom/miniforge/base/bin:$(PATH)
export PYTHONPATH := $(REPO)/src:~/.local/lib/msb-v3
export OLLAMA_MODEL ?= qwen3:8b
export MSB_DB_PATH ?= $(REPO)/data/msb_v3.db
export MSB_HOST ?= 127.0.0.1
export MSB_PORT ?= 8766

test:
	@bash scripts/seed-research-runtime.sh
	$(PY) -m pytest -q tests/

# MoIE detection policy drift gate — the same gate CI's lint job runs
# (scripts/ci-policy-gate.sh). Validates config/risk_templates.json with the
# engine's fail-closed loader and fails if detection coverage drifts from
# the pinned baseline (MSB-GATE-EVAL-001: 17/8/8/23). A policy edit that
# changes what the gate blocks must land together with its pin updates in
# test_gate_contract.py + test_phase2_calibration.py — `make lint` and the
# pre-push hook enforce that locally, before CI does.
#   MSB_RISK_POLICY_PATH=<candidate.json> make policy-gate   # diff a candidate
#   MSB_STRICT=0 make policy-gate                            # dry-run warning
# Exit: 0 = valid + matches baseline, 1 = validation failure, 2 = drift,
#       3 = environment/usage error.
policy-gate:
	MSB_PYTHON=$(PY) bash scripts/ci-policy-gate.sh

# Lint + typecheck + policy drift gate — the exact gates CI's lint job runs.
# Cheap; the pre-push hook calls this so a red lint job can never land.
# Bare mypy (no --ignore-missing-imports): the blanket suppress masked the
# P4 extraction's red state — sys.modules shim aliases are runtime-only and
# invisible to static mypy. Targeted overrides live in pyproject.toml.
lint:
	$(PY) -m ruff check src/ tests/
	$(PY) -m mypy src
	$(PY) scripts/gen-requirements.py --check
	$(PY) scripts/verify-claims.py
	MSB_PYTHON=$(PY) bash scripts/ci-policy-gate.sh

# Regenerate requirements-{runtime,dev}.lock from pyproject.toml (the single
# source of truth). The .lock files are fully transitive-pinned with
# cross-platform hashes via pip-compile (needs pip-tools + network), so the
# Docker image is reproducible. The transient .in files are written and removed
# here (committing them tripped Dependabot's pip-compile detection). `make
# lint` and CI run --check so a stale lock can't ship.
deps:
	$(PY) scripts/gen-requirements.py

# Portability gate: stage the repo to a temp dir (MSB_HOME pointed at the
# copy), verify no /Users/... machine literals remain in live code, and run
# the full pytest suite there. Catches any path-portability regression from
# the de-hardcode pass. Auto-cleans the copy; keep it with PORTABILITY_KEEP=1.
portability:
	bash scripts/portability-check.sh

# Release verification: fresh-clone the tag (default v<pyproject version>)
# from the remote, confirm the checkout is virgin, seed the research-runtime
# fixtures, and run the full suite from the clone — fails on any test failure
# or seeded-artifact skip. Run right after tagging; keep the clone with
# VERIFY_KEEP=1, pin the pass count with EXPECTED_PASS=814.
verify-release:
	bash scripts/verify-release.sh

# Warn when the live .env drifts from the locked .env.example (non-secret
# contract vars only; secrets are never compared or printed). --fail makes
# mismatches exit non-zero for scripting. The portability gate runs this
# automatically; set PORTABILITY_FAIL_ON_DRIFT=1 there to block on drift.
env-drift:
	bash scripts/check-env-drift.sh

# Install the pre-push hook (scripts/hooks/pre-push -> .git/hooks/pre-push)
# so every future push runs the portability gate first. Git hooks aren't
# versioned, so each fresh clone needs this once. Idempotent; overwrites an
# existing pre-push hook. Remove with `make hooks-uninstall`.
hooks-install:
	install -m 755 scripts/hooks/pre-push .git/hooks/pre-push
	@echo "pre-push hook installed (.git/hooks/pre-push)"

hooks-uninstall:
	rm -f .git/hooks/pre-push
	@echo "pre-push hook removed"

# Full engineering hygiene battery, then sweep the test-named Qdrant
# collections the live experiments leave behind (tenant_live_test_*, *-test).
hygiene:
	$(PY) scripts/hygiene/hygiene_runner.py --all
	bash scripts/qdrant-sweep.sh

server:
	$(PY) -m msb_v3

# Launchd-aware server control (same as `bash scripts/start.sh ...`);
# `make server` above stays the foreground dev run.
server-start:
	bash scripts/start.sh start

server-stop:
	bash scripts/start.sh stop

# Convenience: `make server-status` shows launchd/standby state via
# scripts/start.sh (same as `bash scripts/start.sh status`).
server-status:
	bash scripts/start.sh status

# Hardware-independent signed-device development probe. Requires a running
# server and a non-empty MSB_NODE_PAIRING_CODE; private key is ephemeral.
vesta-loopback:
	$(PY) scripts/vesta-loopback.py

smoke:
	MSB_PORT=8767 MSB_DB_PATH=/tmp/msb-v3-smoke.db $(PY) -m msb_v3 & echo $$! > /tmp/msb-v3-smoke.pid; \
	for i in 1 2 3 4 5; do \
	  curl -fsS http://127.0.0.1:8767/health >/dev/null && break; \
	  sleep 1; \
	done; \
	bash scripts/smoke.sh; \
	code=$$?; \
	kill $$(cat /tmp/msb-v3-smoke.pid 2>/dev/null) >/dev/null 2>&1 || true; \
	rm -f /tmp/msb-v3-smoke.pid; \
	exit $$code

# Browser smoke test of the live server's lifecycle endpoints (system Chrome
# via Playwright): /status, authed /mcp/status, /metrics -- text + screenshot
# + console-error capture, artifacts under artifacts/webcheck-<ts>/. Requires
# the server to be running (make server-start) and ~/bin/webcheck.py.
# Multi-step UI flows: make webcheck FLOW=scripts/webcheck/<flow>.json runs a
# click/fill/assert check-script instead (e.g. the n8n sign-in flow).
webcheck:
	bash scripts/webcheck.sh

# Browser verification of client-facing HTML deliverables on the Desktop
# (Botpress demo, julie 1, Mixboard -- system Chrome via Playwright, same
# engine as webcheck). No server required. Artifacts + screenshots under
# artifacts/webcheck-desktop-<ts>/. Missing files are reported, not skipped.
webcheck-desktop:
	bash scripts/webcheck-desktop.sh

# Full browser verification suite in one pass. Stages are configurable via
# STAGES (comma-separated, any subset/order): endpoints (server), desktop
# (deliverables), flow (one check-script, FLOW=<script.json>), custom
# (CUSTOM_CMD='bash ...'), harness (video-harness evidence producer +
# CI-consumer gate in one stage: harness-evidence.sh writes the v1 report,
# ci-harness-gate.sh gates it). Every requested stage runs even if an
# earlier one failed, so you get the full picture; exits non-zero if any
# stage failed. Needs the server for endpoints+flow stages (make server-start).
#   make webcheck-all
#   make webcheck-all STAGES=flow
#   make webcheck-all STAGES=endpoints,custom CUSTOM_CMD="bash scripts/foo.sh"
#   make webcheck-all STAGES=endpoints,harness
webcheck-all:
	bash scripts/webcheck-all.sh

# Local pre-push dry-run of the self-hosted harness-gate CI workflow
# (.github/workflows/harness-gate.yml): ensure Qdrant + server are up
# (best-effort, idempotent), then run the endpoints+harness webcheck-all
# gate -- the exact steps CI runs, minus the artifact upload. Exit is
# non-zero if any step failed.
harness-gate-dryrun:
	bash scripts/harness-gate-dryrun.sh

# Convenience alias: `make qdrant` shows status (same as qdrant-status).
qdrant:
	bash scripts/start-qdrant.sh status

qdrant-start:
	bash scripts/start-qdrant.sh start

qdrant-stop:
	bash scripts/start-qdrant.sh stop

qdrant-status:
	bash scripts/start-qdrant.sh status

# Delete test-named Qdrant collections (tenant_live_test_*, *-test). Dry run:
# make qdrant-sweep ARGS=--dry-run
qdrant-sweep:
	bash scripts/qdrant-sweep.sh $(ARGS)

backup:
	$(PY) -m msb_v3.ops backup

restore:
	$(PY) -m msb_v3.ops restore $(TS)

backup-verify:
	$(PY) -m pytest tests/ops -q

# Governance brakes (Phase 0B): status/control from the terminal. The
# Cockpit UI is Phase 1; these targets are the operator surface until then.
governance-status:
	$(PY) -m msb_v3.governance status

governance-arm:
	$(PY) -m msb_v3.governance arm "$(REASON)"

governance-disarm:
	$(PY) -m msb_v3.governance disarm

governance-approvals:
	$(PY) -m msb_v3.governance approvals

governance-approve:
	$(PY) -m msb_v3.governance approve "$(ID)"

governance-reject:
	$(PY) -m msb_v3.governance reject "$(ID)" "$(REASON)"

governance-token:
	bash scripts/set-operator-token.sh status

governance-config:
	$(PY) -m msb_v3.governance config

# Flywheel (Phase 2): drive a turn, view turns, approve a parked turn. The
# turn parks at build/combine/record until you approve it — that is the
# owner-approval brake, not a bug.
flywheel-turn:
	$(PY) -m msb_v3.flywheel turn "$(PROBLEM)" --charger "$(CHARGER)"

flywheel-status:
	$(PY) -m msb_v3.flywheel status

flywheel-approve:
	$(PY) -m msb_v3.flywheel approve "$(ID)"

flywheel-config:
	$(PY) -m msb_v3.flywheel config

# Fresh-box provisioning: pull the two models the stack uses (idempotent).
provision-models:
	bash scripts/provision-models.sh

# Reproducible rebuild from a fresh clone (host path; see MANIFEST.md).
setup:
	bash scripts/setup.sh

