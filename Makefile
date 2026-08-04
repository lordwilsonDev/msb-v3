.PHONY: test server smoke

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
	$(PY) -m pytest -q tests/

server:
	$(PY) -m msb_v3

smoke:
	MSB_PORT=8767 MSB_DB_PATH=/tmp/msb-v3-smoke.db $(PY) -m msb_v3 &
	$$! > /tmp/msb-v3-smoke.pid
	for i in 1 2 3 4 5; do \
	  curl -fsS http://127.0.0.1:8767/health >/dev/null && break; \
	  sleep 1; \
	done; \
	bash scripts/smoke.sh; \
	code=$$?; \
	kill $$(cat /tmp/msb-v3-smoke.pid 2>/dev/null) >/dev/null 2>&1 || true; \
	rm -f /tmp/msb-v3-smoke.pid; \
	exit $$code
