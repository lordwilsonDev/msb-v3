#!/usr/bin/env bash
set -euo pipefail

# Restore verification for deepseek-harness snapshots. Runs from the root of
# the extracted copy (vault-backup.sh cds there before invoking this via
# MSB_BACKUP_VERIFY_CMD). Installs deps fresh (the snapshot carries no
# node_modules) and runs the full unit suite under an on-the-fly config that
# excludes environment-sensitive files which cannot pass in a restore copy:
#   - scripts/project-doc-site.spec.ts      needs .git metadata (snapshots
#     record the commit in the manifest, they do not ship .git)
#   - bash-local executor.spec.ts           compares bash's resolved cwd
#     (/private/tmp/...) against process.cwd() (/tmp/...) — the macOS
#     symlink only diverges on temp-path restores
#   - app-boot hmr-config.spec.ts           timing-flaky watcher tests
# The remaining ~13.3k tests still gate the restore.

export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cat > vitest.verify.config.ts <<'EOF'
// On-the-fly restore-verify config: extends the base suite but excludes
// environment-sensitive files (no .git metadata, /tmp -> /private/tmp
// symlink, timing-flaky HMR watcher) that cannot pass in a snapshot
// restore copy.
import { defineConfig } from 'vitest/config'
import base from './vitest.config.ts'

const extraExcludes = [
  'scripts/project-doc-site.spec.ts',
  'packages/shell/bash-local/tests/executor.spec.ts',
  'packages/boot/app-boot/tests/hmr-config.spec.ts',
]

export default defineConfig({
  ...base,
  test: {
    ...base.test,
    projects: base.test!.projects!.map((p: any) => ({
      ...p,
      test: { ...p.test, exclude: [...(p.test?.exclude ?? []), ...extraExcludes] },
    })),
  },
})
EOF

pnpm install --frozen-lockfile
pnpm vitest run --config vitest.verify.config.ts
