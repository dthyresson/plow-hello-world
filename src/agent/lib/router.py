"""Routing: pick the command that answers a message.

The mechanism only. The table it walks -- which message reaches which command --
lives with the commands, in agent/commands/__init__.py.
"""
from __future__ import annotations


def route(commands, text):
    """The (name, handler) of the first command whose matcher accepts `text`.

    Order is the policy: the table is walked in order and first match wins, so
    the last row is expected to be a catch-all. A table without one is a bug, and
    says so rather than silently answering nothing.
    """
    for name, matches, handler in commands:
        if matches(text):
            return name, handler
    raise RuntimeError("no command matched and the routing table has no catch-all")
