"""The commands, and which message reaches which one.

A command is one file exposing `async def run(plow, http, text) -> str`: handed
the message text, it returns the reply. Nothing here connects, routes or
checkpoints -- that is runtime.py's job, and keeping it out is what lets a command
be read on its own.

COMMANDS is the routing table, and its **order is the policy**: first match wins,
so the most specific routes come first and the catch-all (`echo`) comes last. To
add a command, drop a module beside these and add one row here.
"""
from __future__ import annotations

import re

from agent.commands import agenda, echo, messages, story
from agent.commands import help as help_cmd
from agent.commands import time as time_cmd

# Word-bounded on purpose: "history" contains "story", and a substring test would
# send it to the model.
STORY_REQUEST = re.compile(r"\b(story|stories|tale|tales|bedtime)\b", re.IGNORECASE)

COMMANDS = (
    # Order is the policy -- first match wins, most specific first. `time` sits
    # ABOVE the catch-all so a clock question is never swallowed by echo, and
    # BELOW story so "a story about a time machine" still routes to story.
    ("help", lambda text: text.lower() in ("help", "commands", "?"), help_cmd.run),
    ("story", STORY_REQUEST.search, story.run),
    ("time", time_cmd.matches, time_cmd.run),
    ("agenda", agenda.matches, agenda.run),
    ("messages", messages.matches, messages.run),
    ("echo", lambda text: True, echo.run),
)
