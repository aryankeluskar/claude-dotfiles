#!/usr/bin/env bash
# Automated sync: fast-forward from the remote; if new commits arrived, apply
# them to the live config. Safe to run on a schedule or at Claude startup --
# it only touches live files when the remote actually had changes.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

git remote | grep -q . || { echo "no git remote set; nothing to pull"; exit 0; }

before="$(git rev-parse HEAD 2>/dev/null || echo none)"
git pull --ff-only --quiet || { echo "pull: not fast-forwardable -- resolve by hand"; exit 1; }
after="$(git rev-parse HEAD 2>/dev/null || echo none)"

[ "$before" = "$after" ] && { echo "up to date"; exit 0; }
"$REPO/apply.sh"
