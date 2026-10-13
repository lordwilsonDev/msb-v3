#!/usr/bin/env bash
# Run ON THE MAC MINI. Copies the whole live vault into vault-template/ so it
# can be pushed and shared, after a secret scan. Usage: scripts/vault-export.sh
# Env: MSB_VAULT (default ~/Documents/Vault). EXPORT_BACKUPS=1 also copies Backups/
# (off by default: those are full repo snapshots, i.e. duplicates of this repo).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VAULT="${MSB_VAULT:-${MSB_VAULT_PATH:-$HOME/Documents/Vault}}"
DEST="$REPO/vault-template"
[ -d "$VAULT" ] || { echo "vault not found: $VAULT" >&2; exit 2; }

EXCL=(--exclude='.git/' --exclude='.obsidian/workspace*.json' --exclude='.trash/' --exclude='.DS_Store' --exclude='.env' --exclude='.env.*')
[ "${EXPORT_BACKUPS:-0}" = 1 ] || EXCL+=(--exclude='/Backups/*' --include='/Backups/.gitkeep')
rsync -a "${EXCL[@]}" "$VAULT"/ "$DEST"/
mkdir -p "$DEST/Backups" && touch "$DEST/Backups/.gitkeep"

cd "$REPO"
# Secret scan of the copied files BEFORE staging (repo families + broader vault shapes).
if ! python3 scripts/vault-secret-scan.py vault-template; then
  echo "SECRETS FOUND - nothing staged. Remove them from the vault notes and re-run." >&2
  exit 1
fi
git add -A vault-template
if ! python3 scripts/scan-secrets.py --staged; then
  git reset -q vault-template
  echo "SECRETS FOUND - nothing committed. Remove them from the vault notes and re-run." >&2
  exit 1
fi
echo "files staged: $(git diff --cached --name-only -- vault-template | wc -l)"
echo "clean. Review with 'git status', then: git commit -m 'Publish full vault' && git push"
