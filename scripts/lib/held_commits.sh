#!/usr/bin/env bash
# held_commits.sh — count unpushed commits that carry the local-only marker.
#
#   held_commits.sh [repo] [upstream-ref]     (defaults: . and @{u})
#
# Commits whose subject contains "[do-not-push]" (the factory gate's BLOCKED
# evidence commits) are local by design. A branch can only be pushed as a
# prefix, so if ANY marked commit sits in <upstream>..HEAD, pushing the branch
# publishes it. Callers that push (factory_gate_daily.sh, publish-audit.sh)
# hold the push when this prints a non-zero count.
#
# Prints the count on stdout and always exits 0; an unreadable range counts 0.
set -u
repo="${1:-.}"
up="${2:-@{u}}"
git -C "$repo" log --format=%s "$up..HEAD" 2>/dev/null | grep -cF '[do-not-push]' || true
