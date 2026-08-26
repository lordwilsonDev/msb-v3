# Local Inference — Backends & Switching

**Last updated**: 2026-08-26

## Overview

MSB v3 supports local inference through a provider-seam abstraction. The active backend is selected via `MSB_ACTIVE_BACKEND` and routed through `local_ai/client_factory.py`.

## Backends

### Ollama (default, working)

The primary local inference backend. Connects to Ollama on `:11434`.

| Setting | Default | Notes |
|---------|---------|-------|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `qwen3:8b` | Active model for chat/generation |

**Verified**: 14 integration tests pass (`tests/local_ai/`). Chat, tool calling, embedding all work.

**Models available** (on this machine):
- `qwen3:8b` — primary chat model (8.2B params, Q4_K_M)
- `nomic-embed-text` — embedding model (137M params)

### LlamaCPP (unprovisioned)

Generic llama.cpp backend via `LlamaCPPClient`. Speaks the OpenAI-compatible `/chat/completions` API — same protocol as `llama-server` and BitNet's inference server.

| Setting | Default | Notes |
|---------|---------|-------|
| `LLAMA_CPP_URL` | `http://127.0.0.1:8080` | llama-server URL |
| `LLAMA_CPP_MODEL` | `~/models/gemma-4-12b-it/...` | **Path does not exist** |
| `MSB_ACTIVE_BACKEND` | `ollama` | Set to `llamacpp` to switch |

**Status**: `llama-server` is installed (`brew llama.cpp`, build 10200) but **no model weights are on disk**. The `.env.example` documents this as unsupported until weights are re-provisioned. `/system/health` reports it red (fail-closed).

### BitNet (blocked)

BitNet models use ternary quantization (TQ1_0/TQ2_0) which is natively supported by `llama-server`. The `LlamaCPPClient` speaks the same API — **no code changes needed**, just config + model file.

**What exists**:
- `llama-server` installed with TQ1_0/TQ2_0 support
- `LlamaCPPClient` wired into the agent stack (`agent/intent.py`, `agent/handle.py`, `agent/bridge_provider.py`)
- Provider switching via `MSB_ACTIVE_BACKEND=llamacpp`

**What's missing**:
- No BitNet model file on disk (`~/models/` subdirs are empty)
- Port 8080 occupied by `moie-os` process — needs `LLAMA_CPP_URL` pointed elsewhere

**Model options for 16GB RAM** (from HuggingFace):

| Model | Size | Fit | Notes |
|-------|------|-----|-------|
| `microsoft/bitnet-b1.58-2B-4T-gguf` | ~1.1GB | ✅ | Official, but only 2B params |
| `tzervas/qwen2.5-coder-14b-bitnet-1.58b` | ~2.8GB | ✅ | Real coding model, community quant |
| `tzervas/qwen2.5-coder-32b-bitnet-1.58b` | ~6.3GB | ⚠️ | Tight with other services running |

**To enable**: download weights, start `llama-server` on a free port, set `LLAMA_CPP_URL` and `LLAMA_CPP_MODEL`.

## Provider Switching

```bash
# Use Ollama (default)
export MSB_ACTIVE_BACKEND=ollama

# Use LlamaCPP
export MSB_ACTIVE_BACKEND=llamacpp
export LLAMA_CPP_URL=http://127.0.0.1:8081  # or wherever llama-server runs
export LLAMA_CPP_MODEL=~/models/my-model.gguf
```

The factory (`local_ai/client_factory.py`) routes to the correct client at runtime. No restart required — the backend is resolved on each call.

## Health Reporting

`/system/health` reports per-backend status:
- **Ollama**: green if responding, red if down
- **LlamaCPP**: red if no model weights, green if responding

## Test Coverage

| Backend | Tests | Status |
|---------|-------|--------|
| Ollama | 14 | ✅ All pass |
| LlamaCPP | 0 (unprovisioned) | ⬜ Blocked on weights |
| BitNet | 0 (blocked) | ⬜ Blocked on weights |
