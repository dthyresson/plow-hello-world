"""Time: the current time in UTC, locally, and a few places worth knowing.

"Local" is the owner's zone when TZ is set (an IANA name, e.g.
"America/New_York"); otherwise it is the container's own local time, which is
UTC unless the container's TZ is set.

Zones come from the standard library's zoneinfo, which needs a tz database. This
image's Debian base carries /usr/share/zoneinfo, so nothing extra is installed;
on a base without one, `pip install tzdata` supplies it.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from agent import config

log = logging.getLogger(__name__)

# The places this agent answers for, in the order they are shown. Wellington is
# Pacific/Auckland -- the IANA zone for New Zealand, daylight saving included.
CITIES = (
    ("Berlin", "Europe/Berlin"),
    ("London", "Europe/London"),
    ("Wellington", "Pacific/Auckland"),
    ("San Francisco", "America/Los_Angeles"),
    ("Boston", "America/New_York"),
)

# Matching is deliberately generous. A wrong "yes" costs the owner a clock they
# did not ask for; a wrong "no" loses their question to the catch-all. So: the
# word "time" must be present, it must not be a phrase that uses the word for
# something else, and either the message is short or it carries a cue.
TIME_WORD = re.compile(r"\btime\b")
CUES = (
    "what time", "the time", "time in", "time now", "time is", "time please",
    "current time", "local time", "got the time",
)
NOT_ASKING = ("no time", "time zone", "timezone", "time difference", "time off")
SHORT_WORDS = 3

# Easter egg. The lyric's own question gets the lyric's answer first, then the
# real times underneath -- so the joke never costs anyone the actual clock.
SONG_ASKS = frozenset({"what time is it", "what time is it now"})
SONG = ("What time is it? 4:30 -- not late, no no, just early, early, early. "
        "(Spin Doctors)\n\nBut for real:")


def _normalize(text):
    """Lowercase, drop apostrophes, reduce everything else to plain words."""
    lowered = text.lower().replace("'", "").replace("\u2019", "")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", lowered).split())


def matches(text):
    """Does this message ask for the time? (The shape of a "yes" is in CUES.)"""
    normalized = _normalize(text)
    if not TIME_WORD.search(normalized) or any(phrase in normalized for phrase in NOT_ASKING):
        return False
    return len(normalized.split()) <= SHORT_WORDS or any(cue in normalized for cue in CUES)


def _offset(moment):
    """The zone's offset as UTC, UTC+2 or UTC-4:30, for the right column."""
    minutes = int(moment.utcoffset().total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    hours, rest = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def _row(label, moment, width):
    return f"{label:<{width}} {moment:%H:%M}  {moment:%a}  ({_offset(moment)})"


def _local():
    """(label, moment) for the Local row: the configured zone, else the host's."""
    if config.TZ:
        try:
            return f"Local ({config.TZ})", datetime.now(ZoneInfo(config.TZ))
        except Exception:
            log.warning("TZ=%r is not a known IANA zone; using the container's local time",
                        config.TZ)
    return "Local (container)", datetime.now().astimezone()


async def run(plow, http, text):
    now = datetime.now(timezone.utc)
    local_label, local_moment = _local()
    rows = [("UTC", now), (local_label, local_moment)]
    rows += [(name, now.astimezone(ZoneInfo(zone))) for name, zone in CITIES]
    header = f"It's {now:%H:%M} UTC on {now:%a %d %b %Y}."
    # The label column follows the longest label, so a long zone name like
    # "Local (America/New_York)" does not push its row out of line.
    width = max(len(label) for label, _moment in rows)
    lines = []
    if _normalize(text) in SONG_ASKS:
        lines += [SONG, ""]
    lines += [header, ""] + [_row(label, moment, width) for label, moment in rows]
    return "\n".join(lines)
