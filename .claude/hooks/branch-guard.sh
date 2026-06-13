#!/usr/bin/env bash
# branch-guard.sh — PreToolUse hook (matcher: Edit|Write)
# Warns when editing files while on a protected branch. Non-blocking: it surfaces
# a message to Claude but does not halt the action (exit 0).
#
# Windows: runs under Git Bash / WSL. Ensure Claude Code's shell can execute bash.
set -euo pipefail

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"

case "$branch" in
  main|master|develop)
    echo "branch-guard: currently on protected branch '$branch'. forklift never edits or commits on a protected branch — create a feature branch first (e.g. 'git checkout -b fix/issue-<n>')." >&2
    ;;
  "")
    : # not a git repo / detached — say nothing
    ;;
esac

exit 0
