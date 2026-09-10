#!/usr/bin/env bash
# Read-only MSB v3 project archaeology inventory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

printf '%s\n' '=== MSB v3 PROJECT ARCHAEOLOGY ==='
printf 'root=%s\n' "$ROOT"
printf 'date=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'branch='; git branch --show-current 2>/dev/null || printf 'unknown\n'
printf 'head='; git rev-parse --short HEAD 2>/dev/null || printf 'not-a-git-checkout\n'
printf 'status_count='; git status --short 2>/dev/null | wc -l | tr -d ' '
printf 'python='; command -v python3 || true

printf '\n%s\n' '--- manifests ---'
find . -maxdepth 2 -type f \( -name 'pyproject.toml' -o -name 'requirements*.lock' -o -name 'Dockerfile*' -o -name 'docker-compose*.yml' -o -name 'compose*.yml' \) -not -path './.git/*' -print | sort

printf '\n%s\n' '--- source/test counts ---'
printf 'source_files='; find src -type f -name '*.py' -not -path '*/__pycache__/*' | wc -l | tr -d ' '
printf 'test_files='; find tests -type f -name 'test_*.py' | wc -l | tr -d ' '
printf 'test_functions='; grep -RhoE '^def test_[A-Za-z0-9_]+' tests 2>/dev/null | wc -l | tr -d ' '

printf '\n%s\n' '--- CI workflows ---'
find .github/workflows -maxdepth 1 -type f -print 2>/dev/null | sort || true

printf '\n%s\n' '--- documented markers ---'
grep -RInE 'TODO|FIXME|NotImplementedError|placeholder|stub|PARK|DEFER' src docs 2>/dev/null | head -80 || true

printf '\n%s\n' '--- active contract files ---'
for f in README.md CLAUDE.md MANIFEST.md REPO_REQUIREMENTS.md docs/PRODUCTION-READINESS.md; do
  if [[ -f "$f" ]]; then printf '%s\n' "$f"; fi
done
