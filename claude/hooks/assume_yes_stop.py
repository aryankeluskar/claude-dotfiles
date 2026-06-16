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


NOTIFY_TOOLS = {"PushNotification", "AskUserQuestion"}


def _tail_lines(path, max_bytes=1_000_000):
    """Read the last ~max_bytes of a (possibly large) JSONL transcript as lines,
    dropping a partial leading line if we did not start at byte 0."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        start = max(0, size - max_bytes)
        f.seek(start)
        raw = f.read()
    lines = raw.decode("utf-8", "replace").splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    return lines


def _notified_this_turn(transcript_path):
    """True if the assistant already reached the user via PushNotification or
    AskUserQuestion since the last genuine user prompt. If so, the 'reach the AFK
    user' nudge is moot and the stop should be allowed.

    A *suppressed* notification still counts: the tool_use is recorded regardless
    of whether delivery happened, and suppression ("you're active in this
    terminal") means the user is present — the AFK premise is already false.
    """
    if not transcript_path:
        return False
    try:
        lines = _tail_lines(os.path.expanduser(transcript_path))
    except Exception:
        return False
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except Exception:
            continue
        role = entry.get("type") or entry.get("role")
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = msg.get("content")
        if role == "assistant" and isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("name") in NOTIFY_TOOLS
                ):
                    return True
        elif role == "user":
            # A tool_result-only "user" message is the harness feeding tool output
            # back, NOT a new user turn — keep scanning past it. A plain-text or
            # text-block user message is the real turn boundary: stop here.
            if isinstance(content, str):
                return False
            if isinstance(content, list):
                only_tool_results = content and all(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                )
                if not only_tool_results:
                    return False
            else:
                return False
    return False


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

    # If the assistant already reached the user this turn (PushNotification or
    # AskUserQuestion, delivered or suppressed), the "reach the AFK user" nudge is
    # redundant — allow the stop instead of re-injecting the standing instruction.
    if _notified_this_turn(data.get("transcript_path")):
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
