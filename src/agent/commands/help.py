"""Help: list what this agent can do. Free."""

HELP_TEXT = (
    "I can: echo anything you send, or tell a short story about an animal "
    '("tell me a story about a penguin", "a bedtime tale about a lion").'
)


async def run(plow, http, text):
    return HELP_TEXT
