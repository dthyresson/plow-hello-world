"""Entry point: `python -m agent`.

Logging setup, config guard, signal handlers, then runtime.Agent.run().
"""
from __future__ import annotations

import asyncio
import logging
import signal

from agent import config
from agent.lib.plow import Plow
from agent.runtime import Agent

log = logging.getLogger("agent")


async def main():
    if not config.TOKEN:
        log.error("PLOW_AGENT_TOKEN is not set")
        return 2
    agent = Agent(Plow(config.BASE, config.TOKEN))
    loop = asyncio.get_running_loop()
    # SIGTERM (docker stop) and SIGINT (Ctrl-C) both flip the stop event, so the
    # current sleep is cut short and the loop exits within the grace period.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent.stop)
    log.info("starting against %s (allowlist: %s)", config.BASE,
             ", ".join(sorted(config.ALLOWED_HANDLES)) or "owner only")
    await agent.run()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main()))
