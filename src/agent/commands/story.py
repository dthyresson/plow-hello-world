"""Story: a short bedtime story about an animal, from Plow inference.

The only command that spends anything -- it is the sole caller of
plow.complete(), and so the only one billed.
"""
from agent import config
from agent.lib.plow import ModelUnavailable

STORY_MAX_TOKENS = 500
STORY_SYSTEM = (
    "You tell very short, gentle stories. Given a request, write ONE story of "
    "two or three short paragraphs about the animal asked for, suitable for "
    "bedtime. Plain text only: no title, no preamble like 'Here is a story', no "
    "questions and no offer to continue. If no animal is named, choose a friendly "
    "one. Give the animal an invented name, varied from story to story -- and not "
    "the obvious 'Pip'."
)


async def run(plow, http, text):
    try:
        return await plow.complete(http, model=config.MODEL, system=STORY_SYSTEM,
                                   user=text, max_tokens=STORY_MAX_TOKENS)
    except ModelUnavailable as exc:
        return f"I can't tell a story right now: {exc}."
