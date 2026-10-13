"""A minimal, Hermes-free Plow agent.

The package, and the map of it:

    config.py          every environment setting, in one place
    runtime.py         the connection and its lifecycle, plus the guard
    lib/plow.py        the Plow HTTP contract -- the wire
    lib/router.py      routing: which command answers a message
    commands/          the replies, one file per command

Entry point: `python -m agent` (see __main__.py).

AI-written, like the rest of this repo -- mistakes are possible, so verify anything
you depend on.
"""
