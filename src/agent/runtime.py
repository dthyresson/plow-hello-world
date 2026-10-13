"""The connection and its lifecycle -- the transport shell.

This module keeps the socket alive and decides what may reach the agent:

  * the reconnect loop and its backoff (run)
  * one session: reach -> ticket -> socket -> backfill -> frames (session)
  * the per-chat checkpoint, written atomically
  * the guard: who may invoke the agent (the owner, plus PLOW_ALLOWED_HANDLES)
  * each authorized message is handed to the router, whose command answers it
  * a turn that outlasts ACK_DELAY is acknowledged before its reply (_respond_with_ack)

It deliberately contains neither the wire nor the replies:

  agent/config.py       every environment setting
  agent/lib/plow.py     the Plow HTTP contract -- the wire
  agent/lib/router.py   routing: which command answers a message
  agent/commands/       the replies, one file per command
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time

import aiohttp

from agent import config
from agent.commands import COMMANDS
from agent.lib.plow import CredentialRefused, Plow
from agent.lib.router import route

log = logging.getLogger(__name__)


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
        self._stop = asyncio.Event()                   # set by stop()

    def stop(self):
        """Ask run() to exit at the next opportunity (SIGINT/SIGTERM)."""
        self._stop.set()

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
        reply = await self._respond_with_ack(http, chat_uid, message)
        if reply:
            await self.plow.send(http, chat_uid, reply)
        self._save_checkpoint(chat_uid, uid)

    async def _respond_with_ack(self, http, chat_uid, message):
        """Run the turn, acknowledging first if it runs long.

        iMessage has no typing indicator this agent can drive -- its only
        outbound primitive is a whole message (`plow.send`), with no edit or
        delete. So the stand-in is an acknowledgement bubble: start the turn,
        and if it is still running after `config.ACK_DELAY`, post
        `config.ACK_TEXT` while it continues. A turn that finishes in time --
        every free command -- never acks, so the bubble appears only when the
        owner would otherwise sit in silence.

        The reply is returned to on_message, which sends it as usual, so the ack
        lands first and the answer follows it. An ack that fails to send is
        logged and swallowed: losing the acknowledgement is not worth losing the
        answer.
        """
        if config.ACK_DELAY <= 0:
            return await self.respond(http, message)
        turn = asyncio.ensure_future(self.respond(http, message))
        done, _ = await asyncio.wait({turn}, timeout=config.ACK_DELAY)
        if not done:
            log.info("turn still running after %.2fs; acknowledging %s with %r",
                     config.ACK_DELAY, chat_uid, config.ACK_TEXT)
            try:
                await self.plow.send(http, chat_uid, config.ACK_TEXT)
            except Exception as exc:
                log.warning("ack send failed: %s: %s", type(exc).__name__, exc)
        return await turn

    async def respond(self, http, message):
        """Hand the text to the router and run whichever command matched.

        The router decides; the command answers. Routing before any model call is
        what keeps the default reply free -- see commands/__init__.py.
        """
        text = (message.get("body") or "").strip()
        name, handler = route(COMMANDS, text)
        log.info("route: %s", name)
        return await handler(self.plow, http, text)

    # --- connection lifecycle ----------------------------------------------

    async def session(self, http):
        """One connection, start to finish: reach, ticket, socket, frames.

        Blocks until the socket closes or raises. Reconnection is run's job.
        Backfill runs after the socket is up, so nothing sent during the gap is
        lost; the dedupe in on_message absorbs any overlap.
        """
        await self.plow.identify(http)      # also refreshes mcp_url (the Mac)
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
