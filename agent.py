#!/usr/bin/env python3
"""The connection and its lifecycle -- the transport shell.

This file keeps the socket alive and decides what may reach the agent:

  * the reconnect loop and its backoff (run)
  * one session: reach -> ticket -> socket -> backfill -> frames (session)
  * the per-chat checkpoint, written atomically
  * the guard: who may invoke the agent (the owner, plus PLOW_ALLOWED_HANDLES)
  * each authorized message is handed to the router, whose command answers it

It deliberately does *not* contain the wire or the replies. The modules around
it, in reading order:

  config.py    every environment setting, in one place
  plow.py      the Plow HTTP contract: identity, chats, ticket, history, send,
               and inference -- the wire, and nothing above it
  commands.py  the replies themselves: one small function per command
  router.py    which command a message is routed to
  agent.py     this file: the connection, and who may use it

Start at the module docstring in each and follow the imports.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import time

import aiohttp

import config
from plow import CredentialRefused, Plow
from router import route

log = logging.getLogger("agent")


def _solo_dm(chat):
    """True for a chat with no peer agent and at most one human -- the owner's
    1:1 with this agent.

    Used only to pick a home chat when PLOW_HOME_CHANNEL is unset. A peer agent
    in the room makes it a group even if only one human is present.
    """
    participants = chat.get("participants") or []
    if any(p.get("type") == "agent" and p.get("relationship") == "peer" for p in participants):
        return False
    return sum(1 for p in participants if p.get("type") == "member") <= 1


def _handle(sender):
    """A sender's handle: the phone number or email Plow keys them by."""
    return (sender.get("provider_key") or "").strip().lower()


def _authorized(sender):
    """Whether this sender may invoke the agent.

    Only the owner, or a handle explicitly listed in PLOW_ALLOWED_HANDLES. The
    sender type gates first: a peer agent or a Plow system event is not a person
    asking, so neither invokes the agent.

    A member's `role` is "owner" for the account owner and unprivileged
    otherwise -- the contract defines no other role to trust (a "relationship"
    like "wife" is a label the owner recorded, not an authority). So everything
    that is not the owner is denied unless its handle is named.
    """
    if sender.get("type") != "member":
        return False
    if sender.get("role") == "owner":
        return True
    return _handle(sender) in config.ALLOWED_HANDLES


def _describe(person):
    """A sender or participant rendered for logs: type, role, relationship, name
    and handle.

    Identity lives in different places by type -- a member carries
    `display_name`/`provider_key`, an agent carries them on its nested `line` --
    so both are read here and the log line reads the same either way.
    """
    bits = [str(person.get("type") or "unknown")]
    if person.get("role"):
        bits.append(f"role={person['role']}")
    if person.get("relationship"):
        bits.append(f"rel={person['relationship']}")
    line = person.get("line") or {}
    name = person.get("display_name") or line.get("display_name")
    handle = person.get("provider_key") or line.get("provider_key")
    bits.append(f"{name} ({handle})" if name and handle
                else name or handle or person.get("uid") or "?")
    return " ".join(bits)


def _roster(chat):
    """One line naming every participant of a chat, for DEBUG logs."""
    return "; ".join(_describe(p) for p in chat.get("participants") or []) or "(no participants)"


class Agent:
    """Holds the connection state; talks to Plow through a `plow.Plow`."""

    def __init__(self, plow: Plow):
        self.plow = plow
        self.chats = {}          # chat uid -> the granted chat resource
        self.home = None         # the owner's 1:1, or config.HOME_CHANNEL
        self.seen = []           # (chat uid, message uid), newest last
        self.seen_events = []    # socket event ids, newest last
        self.checkpoints = self._load_checkpoints()   # chat uid -> last handled uid
        self.up_since = None     # monotonic time the current socket came up
        self._stop = asyncio.Event()                   # set by SIGINT/SIGTERM

    # --- durable state ------------------------------------------------------

    @staticmethod
    def _load_checkpoints():
        """The last-handled uid per chat, or {} on first run.

        A missing or unreadable file is not an error: it just means "no baseline
        yet", which backfill treats as a first connect.
        """
        try:
            return json.loads(config.CHECKPOINTS.read_text())
        except (OSError, ValueError):
            return {}

    def _save_checkpoint(self, chat_uid, uid):
        """Record that `uid` has been handled in `chat_uid`.

        Written via a temp file and an atomic rename: a plain write truncates
        first, so a stop mid-write would leave an empty cursor that reads back as
        "no baseline" and silently skips the gap. os.replace means a reader sees
        the old uid or the new one, never neither.
        """
        self.checkpoints[chat_uid] = uid
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = config.CHECKPOINTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.checkpoints))
        os.replace(tmp, config.CHECKPOINTS)

    # --- reach ---------------------------------------------------------------

    async def refresh_chats(self, http):
        """Re-read the credential's granted chats, cached on self.

        An empty grant is normal and not an error: a freshly minted agent has no
        chat until its line is first texted. The socket still connects, and the
        first inbound frame re-reads this (see on_frame).
        """
        self.chats = await self.plow.chats(http)
        self.home = config.HOME_CHANNEL or next(
            (uid for uid, chat in self.chats.items() if _solo_dm(chat)), None)
        log.info("chats: %s (home=%s)", ", ".join(self.chats) or "none yet", self.home)

    async def backfill(self, http, chat_uid):
        """Replay messages that arrived while the socket was down.

        A WebSocket does not replay frames, so a disconnect loses anything sent
        in the gap. Read the newest page, walk it back to the last handled uid,
        then replay the missed ones oldest-first (so they arrive in the order
        they were sent).

        A chat with no checkpoint is a first connect: anchor at the newest
        message and replay nothing, so an agent does not answer its entire
        history on boot.
        """
        messages = await self.plow.history(http, chat_uid, config.BACKFILL_LIMIT)
        last = self.checkpoints.get(chat_uid)
        if last is None:
            if messages:
                self._save_checkpoint(chat_uid, messages[0]["uid"])
            return
        missed = []
        for message in messages:
            if message.get("uid") == last:
                break
            missed.append(message)
        for message in reversed(missed):
            await self.on_message(http, message, chat_uid)
        if missed:
            log.info("backfilled %d missed message(s) into %s", len(missed), chat_uid)

    # --- inbound path -------------------------------------------------------

    async def on_frame(self, http, frame):
        """Route one WebSocket frame.

        Frames are typed: {"type":"connected"} is the connection acknowledgement
        and carries no message; everything else has an event_type, and only
        message_received is handled here.

        A frame for a chat not in the cache means the chat is newer than the last
        reach read, so re-read the grant once before deciding.
        """
        if frame.get("type") == "connected":
            return
        chat_uid = frame.get("chat_id")
        if chat_uid not in self.chats:
            await self.refresh_chats(http)
            if chat_uid not in self.chats:
                return
        if frame.get("event_type") != "message_received":
            return
        event_id = frame.get("event_id")
        if event_id in self.seen_events:
            return
        message = (frame.get("data") or {}).get("message") or {}
        await self.on_message(http, message, chat_uid)
        self.seen_events.append(event_id)
        del self.seen_events[:-config.SEEN_MAX]

    async def on_message(self, http, message, chat_uid):
        """Handle one inbound message: filter, authorize, reply, checkpoint.

        The filters matter: `direction` drops the echo of our own sends;
        `sender.type` drops outbound/system shapes, and an agent sender is only
        accepted when it is a peer (an outbound agent sender carries no uid).
        The (chat, uid) pair is the socket/backfill dedupe.

        Then the guard: a message that is not from the owner or an allow-listed
        handle is *not answered*, but it is still checkpointed, so the cursor
        advances and a restart does not replay it.

        The message is acknowledged (checkpointed) only after the reply is sent,
        so a crash mid-turn replays it rather than dropping it.
        """
        if message.get("direction") != "inbound":
            return
        sender = message.get("sender") or {}
        if sender.get("type") not in ("member", "agent", "system"):
            return
        if sender.get("type") == "agent" and sender.get("relationship") != "peer":
            return
        uid = message.get("uid")
        if not uid or (chat_uid, uid) in self.seen:
            return
        self.seen.append((chat_uid, uid))
        del self.seen[:-config.SEEN_MAX]
        log.debug("%s roster: %s", chat_uid, _roster(self.chats.get(chat_uid, {})))
        if not _authorized(sender):
            log.info("ignored %s from %s: not the owner and not allow-listed",
                     chat_uid, _describe(sender))
            self._save_checkpoint(chat_uid, uid)
            return
        body = (message.get("body") or "").strip()
        if not body and not message.get("attachments"):
            return
        log.info("inbound %s from %s: %r", chat_uid, _describe(sender), body)
        reply = await self.respond(http, message)
        if reply:
            await self.plow.send(http, chat_uid, reply)
        self._save_checkpoint(chat_uid, uid)

    async def respond(self, http, message):
        """Hand the text to the router and run whichever command matched.

        The router decides; the command answers. Routing before any model call
        is what keeps the default reply free -- see router.py.
        """
        text = (message.get("body") or "").strip()
        name, handler = route(text)
        log.info("route: %s", name)
        return await handler(self.plow, http, text)

    # --- connection lifecycle ----------------------------------------------

    async def session(self, http):
        """One connection, start to finish: reach, ticket, socket, frames.

        Blocks until the socket closes or raises. Reconnection is run's job.
        Backfill runs after the socket is up, so nothing sent during the gap is
        lost; the dedupe in on_message absorbs any overlap.
        """
        await self.refresh_chats(http)
        ticket = await self.plow.ticket(http)
        async with http.ws_connect(self.plow.ws_url(ticket),
                                   heartbeat=config.HEARTBEAT_SECONDS) as ws:
            self.up_since = time.monotonic()
            log.info("websocket connected")
            for chat_uid in self.chats:
                await self.backfill(http, chat_uid)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self.on_frame(http, json.loads(msg.data))
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE,
                                  aiohttp.WSMsgType.ERROR):
                    break

    async def run(self):
        """Reconnect loop: keep one session alive, forever, until told to stop.

        The backoff resets only for a session that stayed up >=
        HEALTHY_SESSION_SECONDS. Short-lived connections do not earn a reset, so
        a server that accepts and immediately drops is not hammered at the jitter
        delay forever. A CredentialRefused ends the loop for good.
        """
        attempt = 0
        while not self._stop.is_set():
            try:
                # total=None: a websocket must not be killed by a request timeout.
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as http:
                    if attempt == 0:
                        await self.plow.identify(http)
                    await self.session(http)
            except CredentialRefused:
                log.error("credential refused (401); stopping -- re-mint this agent's token")
                return
            except Exception as exc:
                log.warning("session ended: %s: %s", type(exc).__name__, exc)
            healthy = (self.up_since is not None
                       and time.monotonic() - self.up_since >= config.HEALTHY_SESSION_SECONDS)
            if healthy:
                attempt = 0
            self.up_since = None
            attempt += 1
            # First retry is immediate (jittered); then 30s, 60s, 120s ... capped.
            delay = (random.random() * config.RECONNECT_JITTER if attempt == 1
                     else min(config.RECONNECT_BASE * 2 ** (attempt - 2), config.RECONNECT_CAP))
            log.info("reconnecting in %.1fs", delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


async def main():
    """Guard config, install signal handlers, run."""
    if not config.TOKEN:
        log.error("PLOW_AGENT_TOKEN is not set")
        return 2
    agent = Agent(Plow(config.BASE, config.TOKEN))
    loop = asyncio.get_running_loop()
    # SIGTERM (docker stop) and SIGINT (Ctrl-C) both flip the stop event, so the
    # current sleep is cut short and the loop exits cleanly within the grace period.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent._stop.set)
    log.info("starting against %s (allowlist: %s)", config.BASE,
             ", ".join(sorted(config.ALLOWED_HANDLES)) or "owner only")
    await agent.run()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main()))
