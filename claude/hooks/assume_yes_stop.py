#!/usr/bin/env python3
"""Stop hook: 'assume yes, keep going' standing instruction (multi-round).

Behaviour (only in bypass-permissions mode):
  * On turn-end, block the stop and inject a standing instruction telling Claude
    to keep doing in-scope work, and to reach the (usually AFK) user via the
    PushNotification / AskUserQuestion tools whenever a decision, completion, or
    blocker comes up.
  * Multi-round: block up to MAX_ROUNDS times per user turn, then allow the stop
    so we never loop forever. A single global counter tracks rounds (NOT keyed by
    session_id, so the cap holds even if the harness rotates session_id mid-run).
  * The counter is reset at the start of every user turn via the UserPromptSubmit
    hook (this same script, invoked with --reset).

MAX_ROUNDS is tunable via the ASSUME_YES_MAX_ROUNDS env var (default 1 -- nudge
once per turn, then allow the stop; set higher to force more keep-going rounds).

Wiring: ~/.claude/settings.json
  hooks.Stop[].hooks[]             -> python3 "$HOME/.claude/hooks/assume_yes_stop.py"
  hooks.UserPromptSubmit[].hooks[] -> python3 "$HOME/.claude/hooks/assume_yes_stop.py" --reset
(uses $HOME so the same settings.json works on Linux and macOS.)
"""
import json
import os
import sys
import time

MAX_ROUNDS = int(os.environ.get("ASSUME_YES_MAX_ROUNDS", "1"))

REASON = (
    "[Standing instruction — injected by the Stop hook]\n"
    "The user is usually AFK and not watching this terminal; PushNotification and "
    "AskUserQuestion are how you reach them (both go to their phone). Before ending your turn:\n"
    "1. IN-SCOPE work — any pending sub-step, fix, or follow-through that clearly advances the "
    "user's stated goal: just do it now. Assume the answer is YES and proceed with your best "
    "judgment, resolving clarifications YOURSELF first (codebase, internet, docs). Finish the "
    "in-scope work before you stop.\n"
    "2. NO SLOP. Real work only — edit code, run commands, test, debug, investigate. Do NOT "
    "manufacture summary/recap markdown, status writeups, reports, READMEs, CHANGELOGs, or "
    "'here's what I did' docs unless the user EXPLICITLY asked. If the only thing left is to "
    "recap or document finished work, do not invent deliverables.\n"
    "3. NEED A DECISION? If there is more than one reasonable next step, or the next step is "
    "out-of-scope / tangential / risky / hard to reverse, or you genuinely cannot tell what the "
    "user wants — do NOT guess. Ask via AskUserQuestion (reaches their phone) AND fire a "
    "PushNotification, then stop.\n"
    "4. DONE? If the task is genuinely complete and no in-scope work remains, fire ONE "
    "PushNotification (one line, lead with the outcome) to alert the AFK user, then STOP — it is "
    "fine to end the turn now. Do NOT keep looping or invent extra rounds of empty work.\n"
    "5. BLOCKED? If you hit a real blocker you cannot resolve yourself, fire a PushNotification "
    "and AskUserQuestion, then stop.\n"
    "Now act on the above. Do not end the turn silently."
)


def _counter_path():
    # A single GLOBAL counter (not keyed by session_id): the round cap must bound
    # the loop even when the harness rotates session_id mid-conversation. The
    # UserPromptSubmit --reset clears it at the start of each user turn, which is
    # what restores the full MAX_ROUNDS budget per turn in a normal session.
    base = os.path.expanduser("~/.claude/.stop-hook-rounds")
    try:
        os.makedirs(base, exist_ok=True)
        # Clean up legacy per-session counters from older versions of this hook.
        now = time.time()
        for fn in os.listdir(base):
            if fn == "rounds":
                continue
            fp = os.path.join(base, fn)
            try:
                if now - os.path.getmtime(fp) > 86400:
                    os.remove(fp)
            except OSError:
                pass
    except OSError:
        pass
    return os.path.join(base, "rounds")


def _read_count(path):
    try:
        with open(path) as f:
            return int(f.read().strip() or "0")
    except Exception:
        return 0


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        # Malformed / empty input: never block the stop.
        sys.exit(0)

    path = _counter_path()

    # UserPromptSubmit --reset: clear the per-turn round counter and exit.
    if "--reset" in sys.argv:
        try:
            os.remove(path)
        except OSError:
            pass
        sys.exit(0)

    # Stop hook from here on. Only act in bypass-permissions mode.
    if data.get("permission_mode") != "bypassPermissions":
        sys.exit(0)

    rounds = _read_count(path)
    if rounds >= MAX_ROUNDS:
        # Hit the per-turn cap -> let the turn end. Leave the counter in place so
        # any further stop this turn also passes through; UserPromptSubmit
        # (--reset) clears it when the next user turn begins.
        sys.exit(0)

    # Block again, inject the instruction, and bump the round counter.
    try:
        with open(path, "w") as f:
            f.write(str(rounds + 1))
    except Exception:
        pass
    print(json.dumps({"decision": "block", "reason": REASON}))
    sys.exit(0)


if __name__ == "__main__":
    main()
