"""The things this agent can say -- one function each.

A command is `async def handler(plow, http, text) -> str`: it is handed the
message text and returns the reply. Nothing here connects, routes or
checkpoints -- that is agent.py's job, and keeping it out is what makes a command
readable on its own.

To add one, write the function and register it in router.py.
"""
from __future__ import annotations

import config
from plow import ModelUnavailable

STORY_MAX_TOKENS = 500
STORY_SYSTEM = (
    "You tell very short, gentle stories. Given a request, write ONE story of "
    "two or three short paragraphs about the animal asked for, suitable for "
    "bedtime. Plain text only: no title, no preamble like 'Here is a story', no "
    "questions and no offer to continue. If no animal is named, choose a friendly "
    "one and name it in the first sentence."
)
HELP_TEXT = (
    "I can: echo anything you send, or tell a short story about an animal "
    '("tell me a story about a penguin", "a bedtime tale about a lion").'
)


async def echo(plow, http, text):
    """The default: repeat what was said. Free -- it never touches the model."""
    return f"hello world. You said: {text}" if text else "hello world."


async def help_command(plow, http, text):
    """List what this agent can do."""
    return HELP_TEXT


async def story(plow, http, text):
    """Tell a short story. Spends inference -- this is the only command that does."""
    try:
        return await plow.complete(http, model=config.MODEL, system=STORY_SYSTEM,
                                   user=text, max_tokens=STORY_MAX_TOKENS)
    except ModelUnavailable as exc:
        return f"I can't tell a story right now: {exc}."
