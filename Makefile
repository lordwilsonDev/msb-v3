.PHONY: test verify server clean

help:
	@echo "MSB v3 — sovereign core"
	@echo "  make test     - pytest (miniforge python)"
	@echo "  make verify   - pytest + ruff"
	@echo "  make server   - uvicorn with local env"

test:
	/opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest -q

verify:
	/opt/homebrew/Caskroom/miniforge/base/bin/python -m pytest -q --tb=short
	ruff check src/ tests/

server:
	MSB_RELOAD=$(MSB_RELOAD) \
	/opt/homebrew/Caskroom/miniforge/base/bin/python -m msb_v3

clean:
	rm -rf **/__pycache__ .ruff_cache .pytest_cache logs/*
