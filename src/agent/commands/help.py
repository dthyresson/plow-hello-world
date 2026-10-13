"""Help: list what this agent can do. Free."""

HELP_TEXT = (
    "I can: echo anything you send, tell you the time, tell a short story about "
    'an animal, or read your Mac -- "what\'s next" for your calendar and "last '
    'message from mom" for a thread. Both ask you to approve them on the Mac.'
)


async def run(plow, http, text):
    return HELP_TEXT
