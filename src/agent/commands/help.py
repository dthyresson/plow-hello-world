"""Help: list what this agent can do. Free."""

HELP_TEXT = (
    "I can: echo anything you send, tell you the time (UTC and a few places), or "
    'tell a short story about an animal ("tell me a story about a penguin").'
)


async def run(plow, http, text):
    return HELP_TEXT
