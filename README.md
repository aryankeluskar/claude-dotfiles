# claude-dotfiles

Portable [Claude Code](https://claude.com/claude-code) configuration synced across
machines: settings, custom hooks, and skills. Clone it on any new machine, run one
script, and you have the same Claude Code setup.

Only curated, portable config lives here — **never** credentials, history, or
session state (see [What is NOT synced](#what-is-not-synced)).

## Layout

```
claude-dotfiles/
├── claude/
│   ├── settings.json              # Claude Code settings + hook wiring
│   └── hooks/assume_yes_stop.py   # the "assume yes / keep going" Stop hook
├── agents/skills/                 # custom skills (real files; ~/.claude/skills/* symlink here)
│   ├── fetch-paper/
│   ├── find-skills/
│   ├── humanizer/
│   ├── make-interfaces-feel-better/
│   └── stop-slop/
├── apply.sh                       # repo -> live (~/.claude, ~/.agents), backs up overwrites
├── pull.sh                        # git pull; re-apply only if upstream changed (used by auto-pull)
├── push.sh                        # live -> repo, commit, push
└── .gitignore                     # backstop so secrets/state can never be committed
```

## Set up on a new machine

```bash
git clone https://github.com/aryankeluskar/claude-dotfiles.git ~/dotfiles-claude
~/dotfiles-claude/apply.sh      # installs into ~/.claude and ~/.agents
```

Then **restart Claude Code** so the new `settings.json` and hooks load.

Requirements:
- **python3** on PATH — the Stop hook runs on it. On macOS: `command -v python3`,
  and if missing, `xcode-select --install` (or `brew install python`).
- **git**. Paths use `$HOME`, so the same `settings.json` works on Linux
  (`/home/<you>`) and macOS (`/Users/<you>`).

### Handoff prompt (paste into a fresh Claude Code)

```
Set up my synced Claude Code config on this machine from my dotfiles repo.

1. Clone it (settings.json, my Stop/UserPromptSubmit/SessionStart hooks, custom skills):
   git clone https://github.com/aryankeluskar/claude-dotfiles.git ~/dotfiles-claude
2. Install it (copies into ~/.claude and ~/.agents, recreates skill symlinks, backs up
   anything it overwrites):
   ~/dotfiles-claude/apply.sh
3. The hook needs python3 — check `command -v python3`; if missing, install it
   (macOS: `xcode-select --install` or `brew install python`).
4. Verify and report anything that fails:
   - ~/.claude/settings.json is valid JSON with hooks for Stop, UserPromptSubmit, SessionStart
   - Stop hook fires only in bypass mode: piping '{"permission_mode":"bypassPermissions"}'
     into `python3 ~/.claude/hooks/assume_yes_stop.py` prints JSON with "decision":"block";
     piping '{"permission_mode":"default"}' prints nothing
   - `ls -l ~/.claude/skills/` shows symlinks resolving into ~/.agents/skills/
   - SessionStart hook runs ~/dotfiles-claude/pull.sh (auto-pulls config each launch)
5. Restart Claude Code so the new settings + hooks load.

Going forward: after editing config on any machine I run ~/dotfiles-claude/push.sh;
this machine auto-pulls on next launch. Only install what's in the repo — never copy
credentials or session files.
```

## Day-to-day sync

- **After editing config** on any machine: `~/dotfiles-claude/push.sh` (captures live
  config into the repo, commits, pushes).
- **Other machines** auto-pull on the next Claude Code launch (via the `SessionStart`
  hook), or pull on demand: `~/dotfiles-claude/pull.sh`.

Pull is automatic; **push stays manual** so half-finished edits don't propagate.

## How the hooks work

All hooks are wired in `claude/settings.json` and only do anything in
**bypass-permissions mode** (they're no-ops in default/plan/acceptEdits/dontAsk).

- **`Stop` → `assume_yes_stop.py`** — at turn-end, injects a standing instruction:
  keep doing in-scope work, no slop (no manufactured docs/recaps), and — since the
  user is usually AFK — reach them via `PushNotification` / `AskUserQuestion` (both go
  to the phone) on decisions, completion, or blockers. By default it nudges **once**
  per turn, then lets the turn end (notify-on-completion, no empty-round grinding).
- **`UserPromptSubmit` → `assume_yes_stop.py --reset`** — clears the per-turn round
  counter at the start of each turn, restoring the full round budget.
- **`SessionStart`** — runs `pull.sh` in the background to auto-pull the latest config.
  Safe no-op if the repo isn't cloned or has no remote.

### Tuning

The Stop hook's round budget is set by `ASSUME_YES_MAX_ROUNDS` (default `1`):

```bash
export ASSUME_YES_MAX_ROUNDS=3   # force up to 3 "keep going" rounds per turn
```

The round counter is a single global file (`~/.claude/.stop-hook-rounds/rounds`), so
the cap holds even if the session id rotates mid-run.

## What is NOT synced

Secrets and per-machine state never leave the machine and are blocked by `.gitignore`:
`.credentials.json`, `.git-credentials`, `.netrc`, `history.jsonl`, `projects/`,
`sessions/`, `session-env/`, `todos/`, `tasks/`, `transcripts/`, `plans/`, caches,
`statsig/`, `telemetry/`, `debug/`, and `*-cache.json`.

This repo is public, so it intentionally contains no secrets. If you ever add
machine-specific or sensitive config, make the repo private first.
```

