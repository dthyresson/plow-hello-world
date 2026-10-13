"""Match an inbound message to a command.

Routing happens *before* any model call, and that ordering is the whole point:
the default reply is free, so only the routes that ask for inference spend
anything.

Order matters -- first match wins -- so put specific routes above the catch-all.
Add a command by importing it here and adding one row.
"""
from __future__ import annotations

import re

from commands import echo, help_command, story

# Word-bounded on purpose: "history" contains "story", and a substring test would
# send it to the model.
STORY_REQUEST = re.compile(r"\b(story|stories|tale|tales|bedtime)\b", re.IGNORECASE)

# (name, does-this-message-match?, the command that answers it). `echo` is last
# and always matches -- the catch-all.
ROUTES = (
    ("help", lambda text: text.lower() in ("help", "commands", "?"), help_command),
    ("story", STORY_REQUEST.search, story),
    ("echo", lambda text: True, echo),
)


def route(text: str):
    """The (name, handler) of the first route whose matcher accepts `text`."""
    for name, matches, handler in ROUTES:
        if matches(text):
            return name, handler
    return ROUTES[-1][0], ROUTES[-1][2]
