#!/usr/bin/env bash
# Idempotent model provisioning for a fresh box (or after `ollama rm`).
# Pulls the two models the stack actually uses (settings.ollama_model and
# the embedding model), skipping any already present.
set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
  echo "[provision] ERROR: ollama binary not found on PATH" >&2
  exit 1
fi

if ! ollama list >/dev/null 2>&1; then
  echo "[provision] ERROR: cannot reach the ollama server (is it running on :11434?)" >&2
  exit 1
fi

MODELS=(qwen3:8b nomic-embed-text)
for m in "${MODELS[@]}"; do
  if ollama list | awk '{print $1}' | grep -qx "$m"; then
    echo "[provision] $m already present — skipping"
  else
    echo "[provision] pulling $m ..."
    ollama pull "$m"
  fi
done

echo "[provision] done. Installed models:"
ollama list
