---
id: readme-mac-mini
type: guide
title: Mac Mini setup
created: 2026-10-13
updated: 2026-10-13
tags: [starter, mac-mini]
---

# Mac Mini setup

> **This setup is for the Mac Mini** (Apple Silicon, macOS, Homebrew paths such as
> `/opt/homebrew/...`, `launchd` agents). It works elsewhere only through the
> portable spec in `docs/vault-rebuild.md`.

```bash
cp -R vault-template ~/Documents/Vault      # or: export MSB_VAULT_PATH=/your/path
cd ~/Documents/Vault && git init && git add -A && git commit -m "init vault"
# in the msb-v3 repo:
make setup                  # deps, launchd agents, qdrant, models, /health smoke
make qdrant-start           # vector store on :6333
make provision-models       # ornith:9b + nomic-embed-text via Ollama
```

Then index the vault into Qdrant (collection `tenant_wilson-vault`) and open
`http://127.0.0.1:8766/cockpit`. Open the folder in Obsidian as a vault.

Notes: the vault stays **outside** the repo (user data). Secrets never go in notes.
