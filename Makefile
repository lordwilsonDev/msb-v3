.PHONY: test server smoke

REPO := $(shell pwd)
PY := /opt/homebrew/Caskroom/miniforge/base/bin/python

export VIRTUAL_ENV :=
export PATH := /opt/homebrew/Caskroom/miniforge/base/bin:$(PATH)
export PYTHONPATH := $(REPO)/src
export OLLAMA_MODEL ?= qwen3:latest
export MSB_DB_PATH ?= $(REPO)/data/msb_v3.db
export MSB_HOST ?= 127.0.0.1
export MSB_PORT ?= 8766

test:
	$(PY) -m pytest -q tests/

server:
	$(PY) -m msb_v3

smoke:
	bash scripts/smoke.sh
