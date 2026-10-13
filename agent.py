#!/usr/bin/env python3
"""A minimal, Hermes-free Plow agent.

What it does
------------
Reads PLOW_API_BASE and PLOW_AGENT_TOKEN from the environment, learns who it is
and which chats it may serve, opens the grant-scoped WebSocket Plow pushes
inbound messages over, and answers each inbound message with a canned greeting.
aiohttp covers HTTP and the socket; everything else is the standard library.

This is the whole agent: no framework and no runtime to install. It implements
the Plow contract directly, so every decision is visible in this file.

Who may invoke it
-----------------
Only the owner, plus any handle listed in PLOW_ALLOWED_HANDLES. The line is a
real phone number, so "who can text it" is not a secret; this guard is what
makes reachability a permission rather than an accident. See _authorized.

The contract
------------
  GET  /v1/agents/me                          identity (404 = not one agent)
  GET  /v1/chats                              granted chats
  GET  /v1/chats/{uid}/messages?limit=N       history, newest first
  POST /v1/chats/{uid}/messages {"body": ..}  reply -> {"uid": ..}
  POST /v1/ws/ticket                          -> {"ticket": ..} (60s, single use)
  WS   /v1/ws?ticket=..                       inbound frames:
                                                {"type":"connected"}
                                                {"event_type":"message_received",
                                                 "event_id":.., "chat_id":..,
                                                 "data":{"message":{..}}}

These shapes match plow-pbc/hermes-plugin-plow, which is the Hermes
implementation of the same contract.

Reading order
-------------
  main()          config guard, signal handlers, run()
  Agent.run()     the reconnect loop; owns one session at a time
  Agent.session() one connection: reach, ticket, socket, backfill, frames
  Agent.on_frame()/on_message()  per-message work
  _authorized()   who may invoke the agent (owner + allowlist)
  Agent.respond() the "brain" (swap this for a model)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import random
import signal
import time

import aiohttp

# --- configuration, read once at import ------------------------------------
# The credential file `plow-agents mint` writes sets the first two; the container
# gets them via env_file in compose.yml.
BASE = os.environ.get("PLOW_API_BASE", "https://api.plow.co").rstrip("/")
TOKEN = os.environ.get("PLOW_AGENT_TOKEN", "")
# Optional. When unset, the agent derives a home chat (see _solo_dm). A fresh
# agent has no chats at all until its line is first texted, so this is not
# required to boot.
HOME_CHANNEL = os.environ.get("PLOW_HOME_CHANNEL") or None
# Where the per-chat checkpoint file lives. compose.yml sets /data (a volume);
# a bare `python agent.py` run defaults to ./plow-state.
STATE_DIR = pathlib.Path(os.environ.get("AGENT_STATE_DIR", "./plow-state"))
CHECKPOINTS = STATE_DIR / "last_uids.json"
# How chatty the agent is: DEBUG, INFO, WARNING or ERROR. Read in __main__.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# Comma-separated handles (phone numbers or emails) allowed to invoke the agent
# besides the owner. Empty means owner only.
ALLOWED_HANDLES = {
    handle.strip().lower()
    for handle in os.environ.get("PLOW_ALLOWED_HANDLES", "").split(",")
    if handle.strip()
}

# --- reconnect policy (matches the Hermes plugin) --------------------------
# After a session ends, retry immediately once, then back off 30s doubling to a
# 5-minute cap. The curve resets only for a session that stayed up long enough
# to count as healthy -- see Agent.run().
RECONNECT_BASE = 30.0        # seconds before backed-off retry #1
RECONNECT_CAP = 300.0        # ceiling for the doubling curve
RECONNECT_JITTER = 0.25      # spread the immediate first retry over this long
HEALTHY_SESSION_SECONDS = 30.0
HEARTBEAT_SECONDS = 30       # websocket ping interval
BACKFILL_LIMIT = 50          # messages read back per chat after a reconnect
SEEN_MAX = 512               # dedupe window for message/event ids

log = logging.getLogger("agent")
# One header for every call that presents the credential.
AUTH = {"Authorization": "Bearer " + TOKEN}


class CredentialRefused(Exception):
    """A 401: the token itself was rejected.

    Terminal, not retryable -- every retry would present the same dead token.
    A 403 is different: it is resource-scoped (removed from one chat, say) and
    is retried like any other error.
    """


def _check(resp):
    """The one seam for status on an authed call: 401 stops, others raise."""
    if resp.status == 401:
        raise CredentialRefused
    resp.raise_for_status()


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
    that is not the owner is denied unless its handle is named here.
    """
    if sender.get("type") != "member":
        return False
    if sender.get("role") == "owner":
        return True
    return _handle(sender) in ALLOWED_HANDLES


def _describe(person):
    """A sender or participant rendered for logs: type, role, relationship,
    name and handle.

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
    """Holds the connection state and does everything above the transport."""

    def __init__(self):
        self.chats = {}          # chat uid -> the granted chat resource
        self.home = None         # the owner's 1:1, or PLOW_HOME_CHANNEL
        self.seen = []           # (chat uid, message uid), newest last
        self.seen_events = []    # socket event ids, newest last
        self.checkpoints = self._load_checkpoints()   # chat uid -> last handled uid
        self.up_since = None     # monotonic time the current socket came up
        self._stop = asyncio.Event()                   # set by SIGINT/SIGTERM

    # --- durable state ------------------------------------------------------

    @staticmethod
    def _load_checkpoints():
        """The last-handled uid per chat, or {} on first run.

        A missing or unreadable file is not an error: it just means "no
        baseline yet", which backfill() treats as a first connect.
        """
        try:
            return json.loads(CHECKPOINTS.read_text())
        except (OSError, ValueError):
            return {}

    def _save_checkpoint(self, chat_uid, uid):
        """Record that `uid` has been handled in `chat_uid`.

        Written via a temp file and an atomic rename: a plain write truncates
        first, so a stop mid-write would leave an empty cursor that reads back
        as "no baseline" and silently skips the gap. os.replace means a reader
        sees the old uid or the new one, never neither.
        """
        self.checkpoints[chat_uid] = uid
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CHECKPOINTS.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.checkpoints))
        os.replace(tmp, CHECKPOINTS)

    # --- Plow reads and sends ----------------------------------------------

    async def identify(self, http):
        """GET /v1/agents/me -- log who this agent is. Best effort.

        A 404 is the documented "this token is not one agent" (a wildcard or
        multi-line grant); it answers no identity but is not a failure, so it is
        swallowed. Anything else goes through the 401 seam.
        """
        async with http.get(f"{BASE}/v1/agents/me", headers=AUTH) as resp:
            if resp.status == 200:
                me = await resp.json(content_type=None)
                log.info("identity: agent=%s line=%s", (me.get("agent") or {}).get("uid"),
                         (me.get("line") or {}).get("provider_key"))
            elif resp.status != 404:
                _check(resp)

    async def refresh_chats(self, http):
        """GET /v1/chats -- the credential's granted chats, cached on self.

        The grant *is* the access control: there is no per-chat config. A
        truncated listing (has_more) is an error rather than a partial cache,
        because serving half a grant silently is worse than failing.

        An empty grant is normal and not an error: a freshly minted agent has no
        chat until its line is first texted. The socket still connects, and the
        first inbound frame re-reads this (see on_frame).
        """
        async with http.get(f"{BASE}/v1/chats", headers=AUTH) as resp:
            _check(resp)
            body = await resp.json(content_type=None)
        if body.get("has_more"):
            raise RuntimeError("the granted chat listing is truncated")
        self.chats = {c["uid"]: c for c in body.get("data", []) if c.get("status") == "active"}
        self.home = HOME_CHANNEL or next(
            (uid for uid, chat in self.chats.items() if _solo_dm(chat)), None)
        log.info("chats: %s (home=%s)", ", ".join(self.chats) or "none yet", self.home)

    async def mint_ticket(self, http):
        """POST /v1/ws/ticket -- a single-use ticket, good for 60s.

        Minted immediately before dialing on purpose: a cached one would be
        rejected at consume.
        """
        async with http.post(f"{BASE}/v1/ws/ticket", json={}, headers=AUTH) as resp:
            _check(resp)
            return (await resp.json(content_type=None))["ticket"]

    async def history(self, http, chat_uid):
        """GET /v1/chats/{uid}/messages -- one page, newest first.

        Only backfill() uses this; it never needs to page, because it stops at
        the checkpoint and the interesting window is one page wide.
        """
        async with http.get(f"{BASE}/v1/chats/{chat_uid}/messages",
                            params={"limit": BACKFILL_LIMIT}, headers=AUTH) as resp:
            _check(resp)
            return (await resp.json(content_type=None)).get("data") or []

    async def send(self, http, chat_uid, body):
        """POST /v1/chats/{uid}/messages -- post a reply into one chat.

        A 408/424/5xx is reported as *delivery unknown*, not failure: the POST
        may have been accepted before the error surfaced, and blindly resending
        is how you double-text a real phone. Anything else non-2xx raises.
        """
        async with http.post(f"{BASE}/v1/chats/{chat_uid}/messages",
                             json={"body": body}, headers=AUTH) as resp:
            if resp.status in (408, 424) or resp.status >= 500:
                log.warning("send to %s: HTTP %s (delivery unknown, not resending)",
                            chat_uid, resp.status)
                return False
            _check(resp)
            log.info("outbound %s: %r", chat_uid, body)
            return True

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
        messages = await self.history(http, chat_uid)
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
        and carries no message; everything else has an event_type. Only
        message_received is handled here.

        A frame for a chat not in the cache means the chat is newer than the
        last reach read, so re-read the grant once before deciding.
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
        del self.seen_events[:-SEEN_MAX]

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
        del self.seen[:-SEEN_MAX]
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
        reply = await self.respond(message)
        if reply:
            await self.send(http, chat_uid, reply)
        self._save_checkpoint(chat_uid, uid)

    async def respond(self, message):
        """The brain. Canned for now.

        Swap this for inference when you want a real agent: Plow serves
        OpenAI-compatible completions at POST {BASE}/v1/chat/completions on the
        same bearer.
        """
        text = (message.get("body") or "").strip()
        return f"hello world. You said: {text}" if text else "hello world."

    # --- connection lifecycle ----------------------------------------------

    async def session(self, http):
        """One connection, start to finish: reach, ticket, socket, frames.

        Blocks until the socket closes or raises. Reconnection is run()'s job.
        Backfill runs after the socket is up, so nothing sent during the gap is
        lost; the dedupe in on_message absorbs any overlap.
        """
        await self.refresh_chats(http)
        ticket = await self.mint_ticket(http)
        # The ticket rides a query parameter, and https -> wss mirrors http -> ws.
        url = f"{BASE.replace('http', 'ws', 1)}/v1/ws?ticket={ticket}"
        async with http.ws_connect(url, heartbeat=HEARTBEAT_SECONDS) as ws:
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
        HEALTHY_SESSION_SECONDS. Short-lived connections do not earn a reset,
        so a server that accepts and immediately drops is not hammered at the
        jitter delay forever. A CredentialRefused ends the loop for good.
        """
        attempt = 0
        while not self._stop.is_set():
            try:
                # total=None: a websocket must not be killed by a request timeout.
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as http:
                    if attempt == 0:
                        await self.identify(http)
                    await self.session(http)
            except CredentialRefused:
                log.error("credential refused (401); stopping -- re-mint this agent's token")
                return
            except Exception as exc:
                log.warning("session ended: %s: %s", type(exc).__name__, exc)
            if self.up_since is not None and time.monotonic() - self.up_since >= HEALTHY_SESSION_SECONDS:
                attempt = 0
            self.up_since = None
            attempt += 1
            # First retry is immediate (jittered); then 30s, 60s, 120s ... capped.
            delay = (random.random() * RECONNECT_JITTER if attempt == 1
                     else min(RECONNECT_BASE * 2 ** (attempt - 2), RECONNECT_CAP))
            log.info("reconnecting in %.1fs", delay)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


async def main():
    """Guard config, install signal handlers, run."""
    if not TOKEN:
        log.error("PLOW_AGENT_TOKEN is not set")
        return 2
    agent = Agent()
    loop = asyncio.get_running_loop()
    # SIGTERM (docker stop) and SIGINT (Ctrl-C) both flip the stop event, so the
    # current sleep is cut short and the loop exits cleanly within the grace period.
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, agent._stop.set)
    log.info("starting against %s (allowlist: %s)", BASE,
             ", ".join(sorted(ALLOWED_HANDLES)) or "owner only")
    await agent.run()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main()))
