#!/usr/bin/env bash
# Capture the live Claude config into this repo, commit, and push.
# Run this on whichever machine you just edited config on.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$HOME/.claude/settings.json"            "$REPO/claude/settings.json"
cp "$HOME/.claude/hooks/assume_yes_stop.py" "$REPO/claude/hooks/assume_yes_stop.py"
rsync -a "$HOME/.agents/skills/" "$REPO/agents/skills/"

cd "$REPO"
git add -A
git diff --cached --quiet && { echo "nothing changed"; exit 0; }
git commit -q -m "sync from $(hostname -s 2>/dev/null || hostname) $(date '+%Y-%m-%d %H:%M')"
if git remote | grep -q .; then
  git push -q && echo "pushed"
else
  echo "committed locally. Set a remote then push, e.g.:"
  echo "  git -C $REPO remote add origin git@github.com:<you>/dotfiles-claude.git"
  echo "  git -C $REPO push -u origin main"
fi
