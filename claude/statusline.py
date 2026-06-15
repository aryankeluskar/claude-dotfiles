#!/usr/bin/env python3
"""Claude Code status line.

Segments:
  1. Plan usage % for the current 5-hour session as an ASCII progress bar
     (real /usage number via the OAuth token in macOS Keychain). Falls back to
     the real local 5h-block cost if the OAuth call fails.
  2. Cycling dollar value of tokens used in the last 24h / 7d / 30d, computed
     from local transcripts using Claude API pricing.

Design notes:
  - Claude Code invokes this on every conversation update (throttled ~300ms),
    passing a JSON blob on stdin. It is NOT a live ticker.
  - All expensive work (scanning every transcript, hitting the network) runs in
    a detached background refresh that writes a cache file. Every render reads
    the cache and returns immediately, so the status line never lags the UI.
  - The cost cycle advances with wall-clock time (every ~5s) but only actually
    changes on the next render, i.e. when there is session activity.

Modes:
  (no args)          render the status line (reads stdin JSON)
  --recompute-cost   background: rescan transcripts, write cost cache
  --recompute-usage  background: hit OAuth usage endpoint, write usage cache
  --debug-usage      print raw OAuth usage endpoint responses (for fixing parse)
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
PROJECTS_DIR = os.path.join(HOME, ".claude", "projects")
CACHE_DIR = os.path.join(HOME, ".claude", ".statusline_cache")
COST_CACHE = os.path.join(CACHE_DIR, "cost.json")
USAGE_CACHE = os.path.join(CACHE_DIR, "usage.json")
COST_LOCK = os.path.join(CACHE_DIR, "cost.lock")
USAGE_LOCK = os.path.join(CACHE_DIR, "usage.lock")

COST_TTL = 60      # seconds before a background cost recompute is triggered
USAGE_TTL = 60     # seconds before a background usage refresh is triggered
LOCK_STALE = 600   # a lock older than this is considered abandoned

# --- Claude API pricing, USD per 1M tokens (standard tier) -------------------
# Keep this table current with https://platform.claude.com/docs/en/about-claude/pricing
# Opus 4.5+ (4.5/4.6/4.7/4.8) dropped to $5/$25; only Opus 4.1 and the
# deprecated Opus 4 are still on the legacy $15/$75 schedule.
PRICING = {
    "opus":        {"in": 5.0,  "out": 25.0, "read": 0.50, "w5": 6.25,  "w1h": 10.0},
    "opus_legacy": {"in": 15.0, "out": 75.0, "read": 1.50, "w5": 18.75, "w1h": 30.0},
    "sonnet":      {"in": 3.0,  "out": 15.0, "read": 0.30, "w5": 3.75,  "w1h": 6.0},
    "haiku":       {"in": 1.0,  "out": 5.0,  "read": 0.10, "w5": 1.25,  "w1h": 2.0},
}

DAY = 86400


# --- small utilities ---------------------------------------------------------

def ensure_cache_dir():
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
    except OSError:
        pass


def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def lock_is_free(path):
    """True if no fresh lock is held (stale locks are ignored)."""
    try:
        age = time.time() - os.path.getmtime(path)
        return age > LOCK_STALE
    except OSError:
        return True


def take_lock(path):
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        if lock_is_free(path):
            try:
                os.replace(path, path)  # touch is unnecessary; just reuse
            except OSError:
                pass
            try:
                os.remove(path)
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except OSError:
                return False
        return False
    except OSError:
        return False


def release_lock(path):
    try:
        os.remove(path)
    except OSError:
        pass


def spawn_background(mode):
    """Detach a background refresh process and return immediately."""
    try:
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), mode],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def parse_iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def fmt_remaining(epoch):
    """Format seconds-until-epoch as e.g. '2h13m' or '47m'."""
    if not epoch:
        return "?"
    secs = int(epoch - time.time())
    if secs <= 0:
        return "now"
    h, m = secs // 3600, (secs % 3600) // 60
    return f"{h}h{m:02d}m" if h else f"{m}m"


# --- cost computation (background) -------------------------------------------

def model_rates(model_id):
    m = (model_id or "").lower()
    if "opus" in m:
        # Opus 4.1 and the original Opus 4 stayed on the legacy $15/$75 rates;
        # everything 4.5 and newer is on the $5/$25 schedule.
        if "opus-4-1" in m or m.endswith("opus-4") or "opus-4-0" in m:
            return PRICING["opus_legacy"]
        return PRICING["opus"]
    if "haiku" in m:
        return PRICING["haiku"]
    if "sonnet" in m:
        return PRICING["sonnet"]
    return None


def message_cost(usage, rates):
    if not usage or not rates:
        return 0.0
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    read = usage.get("cache_read_input_tokens", 0) or 0
    creation = usage.get("cache_creation_input_tokens", 0) or 0
    w5 = w1h = 0
    cc = usage.get("cache_creation")
    if isinstance(cc, dict):
        w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
        w1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    if (w5 + w1h) == 0:
        w5 = creation  # fall back to treating all cache writes as 5-minute
    return (
        inp * rates["in"]
        + out * rates["out"]
        + read * rates["read"]
        + w5 * rates["w5"]
        + w1h * rates["w1h"]
    ) / 1_000_000.0


def iter_transcript_lines():
    if not os.path.isdir(PROJECTS_DIR):
        return
    for root, _dirs, files in os.walk(PROJECTS_DIR):
        for name in files:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            try:
                with open(path, errors="ignore") as f:
                    for line in f:
                        yield line
            except OSError:
                continue


def recompute_cost():
    if not take_lock(COST_LOCK):
        return
    try:
        now = time.time()
        d1 = d7 = d30 = 0.0
        horizon = 5 * 3600
        seen = set()
        events = []          # (ts, cost) for every usage-bearing message in 30d
        for line in iter_transcript_lines():
            line = line.strip()
            if not line or '"usage"' not in line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") or {}
            usage = msg.get("usage")
            if not usage:
                continue
            # dedup identical assistant messages across transcript copies
            key = (msg.get("id"), obj.get("requestId"))
            if key != (None, None):
                if key in seen:
                    continue
                seen.add(key)
            ts = parse_iso(obj.get("timestamp"))
            if ts is None:
                continue
            age = now - ts
            if age > 30 * DAY:
                continue
            rates = model_rates(msg.get("model"))
            if not rates:
                continue
            cost = message_cost(usage, rates)
            d30 += cost
            if age <= 7 * DAY:
                d7 += cost
            if age <= DAY:
                d1 += cost
            events.append((ts, cost))
        # Fixed 5h-block anchor (matches Claude's real session model): a window
        # starts at the first activity and runs exactly 5h; a message arriving
        # more than 5h after the current anchor opens a fresh window. The reset
        # is therefore a fixed point for the whole window, not a rolling value.
        events.sort()
        anchor = None
        for ts, _cost in events:
            if anchor is None or ts > anchor + horizon:
                anchor = ts
        if anchor is not None and anchor + horizon > now:
            block = sum(c for ts, c in events if ts >= anchor)
            block_reset = anchor + horizon
        else:
            block = 0.0                  # no active window (idle > 5h)
            block_reset = now + horizon
        write_json_atomic(COST_CACHE, {
            "ts": now, "d1": d1, "d7": d7, "d30": d30,
            "block": block, "block_reset": block_reset,
        })
    finally:
        release_lock(COST_LOCK)


# --- plan usage % (background) -----------------------------------------------

# Candidate OAuth usage endpoints. The real one is undocumented; the first that
# returns parseable 5h-window data is remembered in the cache.
USAGE_ENDPOINTS = [
    "https://api.anthropic.com/api/oauth/usage",
    "https://api.anthropic.com/api/oauth/claude_cli/usage",
    "https://api.anthropic.com/api/claude_cli/usage",
    "https://api.anthropic.com/v1/oauth/usage",
]

FIVE_HOUR_KEYS = ["five_hour", "fiveHour", "5h", "five_hour_limit", "session"]
RESET_KEYS = ["resets_at", "reset_at", "resetsAt", "reset", "resetTime"]
UTIL_KEYS = ["utilization", "percent", "percentage", "used_percent", "pct"]


def read_oauth_token():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        oauth = data.get("claudeAiOauth", data)
        return oauth.get("accessToken")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def fetch_usage_raw(token, url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "anthropic-version": "2023-06-01",
        "User-Agent": "claude-statusline",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=8) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def find_five_hour(data):
    """Return (pct, reset_epoch) from an undocumented usage payload, or None."""
    if not isinstance(data, dict):
        return None

    def extract(window):
        if not isinstance(window, dict):
            return None
        util = None
        for k in UTIL_KEYS:
            if k in window and isinstance(window[k], (int, float)):
                util = float(window[k])
                break
        if util is None:
            return None
        if util <= 1.0:
            util *= 100.0
        reset = None
        for k in RESET_KEYS:
            if k in window:
                reset = parse_iso(window[k]) if isinstance(window[k], str) else None
                break
        return (round(util), reset)

    # 1. direct known keys
    for k in FIVE_HOUR_KEYS:
        if k in data:
            r = extract(data[k])
            if r:
                return r
    # 2. one level of nesting (e.g. {"limits": {...}}, {"usage": {...}})
    for v in data.values():
        if isinstance(v, dict):
            for k in FIVE_HOUR_KEYS:
                if k in v:
                    r = extract(v[k])
                    if r:
                        return r
    return None


def write_usage_fail(prev):
    """Record a failed usage refresh, but keep the last validated reset.

    The 5h reset is a fixed point for the whole window, so once OAuth has
    confirmed it we keep showing it (until it actually passes) even while the
    undocumented endpoint is failing.
    """
    out = {"ts": time.time(), "ok": False}
    reset = (prev or {}).get("reset")
    if reset and reset > time.time():
        out["reset"] = reset
    write_json_atomic(USAGE_CACHE, out)


def recompute_usage():
    if not take_lock(USAGE_LOCK):
        return
    try:
        cached = read_json(USAGE_CACHE) or {}
        token = read_oauth_token()
        if not token:
            write_usage_fail(cached)
            return
        # try the endpoint that worked last time first
        order = USAGE_ENDPOINTS[:]
        last = cached.get("endpoint")
        if last in order:
            order.remove(last)
            order.insert(0, last)
        for url in order:
            try:
                status, body = fetch_usage_raw(token, url)
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if status != 200:
                continue
            try:
                data = json.loads(body)
            except ValueError:
                continue
            found = find_five_hour(data)
            if found:
                pct, reset = found
                write_json_atomic(USAGE_CACHE, {
                    "ts": time.time(), "ok": True,
                    "pct": pct, "reset": reset, "endpoint": url,
                })
                return
        write_usage_fail(cached)
    finally:
        release_lock(USAGE_LOCK)


def debug_usage():
    token = read_oauth_token()
    if not token:
        print("Could not read OAuth token from Keychain "
              "('Claude Code-credentials').")
        print("Note: run this directly in your shell, not through Claude "
              "(its hook redacts the credential).")
        return
    print(f"Token read OK (length {len(token)}).\n")
    for url in USAGE_ENDPOINTS:
        print(f"--- {url}")
        try:
            status, body = fetch_usage_raw(token, url)
            print(f"  HTTP {status}")
            snippet = body[:800]
            print("  " + snippet.replace("\n", "\n  "))
            if status == 200:
                try:
                    print("  parsed 5h:", find_five_hour(json.loads(body)))
                except ValueError:
                    print("  (not JSON)")
        except Exception as e:  # noqa: BLE001 - debug helper
            print(f"  ERROR: {e}")
        print()


# --- local 5h-block fallback (no network) ------------------------------------

def local_block():
    """Real current 5h-window cost + reset, read from the cost cache.

    The values are produced by the background recompute_cost pass so this stays
    a cheap cache read and never blocks a render.
    """
    cache = read_json(COST_CACHE) or {}
    total = cache.get("block", 0.0)
    reset = cache.get("block_reset", time.time() + 5 * 3600)
    return total, reset


# --- rendering ---------------------------------------------------------------

def bar(pct, width=10):
    """ASCII progress bar, e.g. '[=======---]'."""
    pct = max(0.0, min(100.0, float(pct)))
    filled = int(round(pct / 100.0 * width))
    return "[" + "=" * filled + "-" * (width - filled) + "]"


def usage_segment():
    """Real /usage %; falls back to local 5h-block cost."""
    cache = read_json(USAGE_CACHE)
    stale = (not cache) or (time.time() - cache.get("ts", 0) > USAGE_TTL)
    if stale and lock_is_free(USAGE_LOCK):
        spawn_background("--recompute-usage")

    if cache and cache.get("ok"):
        pct = cache.get("pct")
        reset = cache.get("reset")
        rs = f" · resets in {fmt_remaining(reset)}" if reset else ""
        return f"{bar(pct)} {pct}% 5h{rs}"

    # fallback: real local block cost (uses cost-cache horizon scan)
    if cache is None:
        return "[..........] 5h"  # first run, background refresh pending
    total, local_reset = local_block()
    # Prefer the last validated OAuth reset if it is still in the future;
    # it's authoritative and fixed for the window. Otherwise use the locally
    # computed fixed-block anchor.
    reset = cache.get("reset")
    if not reset or reset <= time.time():
        reset = local_reset
    return f"5h ${total:.2f} resets {fmt_remaining(reset)}"


def cost_segment():
    cache = read_json(COST_CACHE)
    stale = (not cache) or (time.time() - cache.get("ts", 0) > COST_TTL)
    if stale and lock_is_free(COST_LOCK):
        spawn_background("--recompute-cost")

    if not cache:
        return "$..."
    d1 = cache.get("d1", 0.0)
    d7 = cache.get("d7", 0.0)
    d30 = cache.get("d30", 0.0)
    return f"24h ${d1:,.2f} · 7d ${d7:,.2f} · 30d ${d30:,.2f}"


def render():
    ensure_cache_dir()
    try:
        json.load(sys.stdin)
    except (ValueError, OSError):
        pass

    segments = [usage_segment(), cost_segment()]
    sys.stdout.write(" · ".join(segments))


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--recompute-cost":
        recompute_cost()
    elif arg == "--recompute-usage":
        recompute_usage()
    elif arg == "--debug-usage":
        debug_usage()
    else:
        render()


if __name__ == "__main__":
    main()
