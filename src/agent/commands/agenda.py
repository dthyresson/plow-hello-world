"""Agenda: what is next on the owner's calendar.

The calendar lives in the owner's Google account, which Latch's bundled
`plow-gog` CLI can read on their behalf. So this is the agent's one real trip
through the relay: the call reaches the Mac, the owner approves it there, and
gog answers with the events.

Nothing here is a Plow *chat* call. It is Latch, over MCP, on the same
credential -- which is why a Mac has to be connected and awake.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from agent.lib.latch import Latch, LatchError

log = logging.getLogger(__name__)

LOOKAHEAD_HOURS = 24
MAX_EVENTS = 5

# Phrases, not the bare word "next" -- "next" alone is too common.
ASKS = (
    "whats next", "what is next", "what s next", "next up", "up next", "agenda",
    "next meeting", "next event", "next appointment", "next calendar",
    "my calendar", "any meetings", "whats on my calendar", "what s on my calendar",
)


def matches(text):
    """Does this message ask what is coming up?"""
    normalized = text.lower().replace("'", "").replace("\u2019", "")
    normalized = " ".join(re.sub(r"[^a-z0-9 ]", " ", normalized).split())
    return any(ask in normalized for ask in ASKS)


def _events(argv):
    """The gog argv for the next window. Times are UTC; gog renders them in the
    owner's zone (and the skill's `startDayOfWeek` is the weekday to trust)."""
    now = datetime.now(timezone.utc)
    until = now + timedelta(hours=LOOKAHEAD_HOURS)
    return argv + ["--from", now.isoformat(), "--to", until.isoformat(),
                   "--max", str(MAX_EVENTS)]


def _summarize(payload):
    """The tool result as a reply. Handles the three endings a relay call has:
    still-deciding, ran-and-failed, and ran-and-answered."""
    status = payload.get("status")
    if status and status != "completed":
        # The Mac is waiting on the owner (an approval card) or still running.
        return (f"Your Mac is still deciding that ({status}) -- approve it there "
                "and ask me again.")
    if payload.get("exit_code"):
        detail = (payload.get("output") or "").strip().splitlines()
        return ("The command ran on your Mac but failed"
                + (f": {detail[-1][:200]}" if detail else "."))
    output = (payload.get("output") or "").strip()
    try:
        data = json.loads(output)
    except ValueError:
        return output[:800] or "Your Mac returned nothing."
    items = data.get("items") or []
    if not items:
        missing = data.get("degraded") or []
        note = f" ({', '.join(missing)} could not be reached)" if missing else ""
        return f"Nothing on your calendar in the next {LOOKAHEAD_HOURS} hours{note}."
    lines = [f"Next {len(items[:MAX_EVENTS])} on your calendar (next {LOOKAHEAD_HOURS}h):", ""]
    for item in items[:MAX_EVENTS]:
        when = f"{item.get('startDayOfWeek') or ''} {item.get('startLocal') or ''}".strip()
        lines.append(f"  {when} -- {item.get('summary') or '(no title)'}")
    if data.get("degraded"):
        lines += ["", f"(could not check: {', '.join(data['degraded'])})"]
    return "\n".join(lines)


async def run(plow, http, text):
    try:
        payload = await Latch(plow).call(http, "plow_run_command", {
            "argv": _events(["plow-gog", "calendar", "events"]),
            "goal": "Look at the owner's calendar for what is next",
            # The paths the approval card shows: gog reads no files here, and
            # `network` is implied by a provider command, so neither is declared.
        })
    except LatchError as exc:
        # LatchError is already a sentence, and it covers both "never got there"
        # and "your Mac said no" -- so do not prepend a claim about which.
        return f"I can't check your calendar: {exc}."
    return _summarize(payload)
