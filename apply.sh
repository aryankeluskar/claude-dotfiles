#!/usr/bin/env bash
# Copy this repo's Claude config into the live locations (~/.claude, ~/.agents).
# Any live file that differs is backed up first, so nothing is silently lost.
# Use for first-time install on a machine, or to re-apply by hand.
set -eo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BK="$HOME/.claude/.sync-backups/$(date +%Y%m%d-%H%M%S)"

backup_if_diff() {  # $1=repo src  $2=live dst
  if [ -f "$2" ] && ! cmp -s "$1" "$2"; then
    mkdir -p "$BK"; cp "$2" "$BK/$(basename "$2")"
  fi
}

mkdir -p "$HOME/.claude/hooks" "$HOME/.claude/skills" "$HOME/.agents/skills"

backup_if_diff "$REPO/claude/settings.json" "$HOME/.claude/settings.json"
cp "$REPO/claude/settings.json" "$HOME/.claude/settings.json"

backup_if_diff "$REPO/claude/hooks/assume_yes_stop.py" "$HOME/.claude/hooks/assume_yes_stop.py"
cp "$REPO/claude/hooks/assume_yes_stop.py" "$HOME/.claude/hooks/assume_yes_stop.py"

# Real skill files live under ~/.agents/skills; ~/.claude/skills/<name> are relative symlinks.
rsync -a "$REPO/agents/skills/" "$HOME/.agents/skills/"
for d in "$REPO"/agents/skills/*/; do
  name="$(basename "$d")"
  ln -sfn "../../.agents/skills/$name" "$HOME/.claude/skills/$name"
done

[ -d "$BK" ] && echo "backed up overwritten files -> $BK"
echo "applied Claude config from $REPO"
