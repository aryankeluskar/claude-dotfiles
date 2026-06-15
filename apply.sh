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

backup_if_diff "$REPO/claude/hooks/save-last-response.sh" "$HOME/.claude/hooks/save-last-response.sh"
cp "$REPO/claude/hooks/save-last-response.sh" "$HOME/.claude/hooks/save-last-response.sh"
chmod +x "$HOME/.claude/hooks/save-last-response.sh"

backup_if_diff "$REPO/claude/statusline.py" "$HOME/.claude/statusline.py"
cp "$REPO/claude/statusline.py" "$HOME/.claude/statusline.py"

# GateGuard git-repo airbag: re-apply the operator patch to the vendored ecc
# plugin (a `claude plugin update` reverts it). Idempotent: skip if the plugin
# is absent or the patch is already applied. Enabled at runtime via the
# GATEGUARD_GIT_REPO_RELAX=1 env in settings.json.
ECC_DIR="$HOME/.claude/plugins/marketplaces/ecc"
GG_PATCH="$REPO/claude/gateguard-gitaware.patch"
if [ -f "$ECC_DIR/scripts/hooks/gateguard-fact-force.js" ] && [ -f "$GG_PATCH" ]; then
  if git -C "$ECC_DIR" apply --reverse --check "$GG_PATCH" >/dev/null 2>&1; then
    echo "gateguard airbag patch already applied"
  elif git -C "$ECC_DIR" apply "$GG_PATCH" >/dev/null 2>&1; then
    echo "applied gateguard git-repo airbag patch"
  else
    echo "WARN: gateguard airbag patch did not apply cleanly; regenerate claude/gateguard-gitaware.patch"
  fi
fi

# Real skill files live under ~/.agents/skills; ~/.claude/skills/<name> are relative symlinks.
rsync -a "$REPO/agents/skills/" "$HOME/.agents/skills/"
for d in "$REPO"/agents/skills/*/; do
  name="$(basename "$d")"
  ln -sfn "../../.agents/skills/$name" "$HOME/.claude/skills/$name"
done

[ -d "$BK" ] && echo "backed up overwritten files -> $BK"
echo "applied Claude config from $REPO"
