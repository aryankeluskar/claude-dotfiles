#!/usr/bin/env python3
"""Stop hook: 'assume yes, proceed' standing instruction.

On every turn-end this blocks the stop exactly ONCE and injects an instruction
back into Claude's context. The `stop_hook_active` guard is what prevents an
infinite loop: when Claude is already continuing because of this hook, we let it
stop instead of blocking again.

This only fires when the session is in bypass-permissions mode
(`permission_mode == "bypassPermissions"`). In any other mode (default, plan,
acceptEdits, auto, dontAsk) the stop is allowed through untouched.

Wiring: ~/.claude/settings.json -> hooks.Stop[].hooks[] -> command:
    python3 "$HOME/.claude/hooks/assume_yes_stop.py"
(uses $HOME so the same settings.json works on Linux and macOS.)
"""
import json
import sys

REASON = (
    "[Standing instruction — injected by the Stop hook]\n"
    "You are about to end your turn. Before you do, decide whether the next step is IN SCOPE "
    "of the goal/prompt the user actually asked for:\n"
    "1. IN-SCOPE work — a pending sub-step, fix, or follow-through that clearly advances the "
    "user's stated goal: just do it. Assume the answer is YES and proceed with your best "
    "judgment. Resolve any clarification YOURSELF first (explore the codebase, the internet, "
    "docs, etc.) rather than asking.\n"
    "2. CONTINUE WITH ACTION AND AGENCY, NOT SLOP. 'Keep going' means do real work — edit "
    "code, run commands, test, debug, investigate, explore the codebase, drive the actual goal "
    "forward. It does NOT mean manufacturing output to look busy: do NOT create summary or "
    "recap markdown files, status writeups, reports, READMEs, CHANGELOGs, or other docs unless "
    "the user EXPLICITLY asked for them. No filler, no boilerplate, no 'here's what I did' "
    "documents. If the only thing left is to recap or document work you already finished, the "
    "task is DONE — just stop; do not invent deliverables.\n"
    "3. OUT-OF-SCOPE or judgment-call work — a next step that expands beyond what was asked, "
    "is tangential, changes direction, is risky/hard to reverse, or where you genuinely cannot "
    "tell what the user wants: do NOT just barrel ahead. Stop and ask via the AskUserQuestion "
    "tool, and raise a PushNotification so the user sees it. Don't end the turn silently.\n"
    "4. Also stop and ask (AskUserQuestion + PushNotification) if you have a STRONG reason to "
    "believe the user would say NO, or you hit a real blocker you cannot resolve on your own.\n"
    "5. Notifications are for MILESTONES and the asks above — not routine stops. While there is "
    "still in-scope work to do, just keep going; do NOT notify. Fire a PushNotification only "
    "when you have genuinely completed the whole goal, or when you need the user per items 3-4.\n"
    "Now act on the above. Do not end the turn silently."
)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        # Malformed / empty input: never block the stop.
        sys.exit(0)

    # Loop guard: if this stop is itself the result of a prior block by this
    # hook, allow the turn to end now.
    if data.get("stop_hook_active"):
        sys.exit(0)

    # Only inject the standing instruction in bypass-permissions mode. In every
    # other permission mode, let the turn end normally.
    if data.get("permission_mode") != "bypassPermissions":
        sys.exit(0)

    print(json.dumps({"decision": "block", "reason": REASON}))
    sys.exit(0)


if __name__ == "__main__":
    main()
