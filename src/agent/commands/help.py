"""Help: list what this agent can do. Free."""

HELP_TEXT = (
    "I can: echo anything you send, tell you the time, tell a short story about "
    'an animal, or check what\'s next on your calendar ("what\'s next?") -- that '
    "last one reaches your Mac and asks you to approve it there."
)


async def run(plow, http, text):
    return HELP_TEXT
