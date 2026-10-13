# How the agent works

> **AI-written — verify before you rely on it.** This document, the agent code, and
> the research behind them were produced with a large language model. Where a claim
> was checked, the check is named nearby; where something was verified against a live
> system, it says so. Everything else is prose a model wrote, and mistakes are
> possible — so confirm anything you intend to depend on.

A walkthrough of the agent. For setup and commands, see `README.md`; this is
about the code — what it does, in what order, and why each decision is the way
it is.

## The shape of the program

Small modules, split so each reads on its own:

| file | owns | read it to learn |
|---|---|---|
| `src/agent/config.py` | every environment setting | where a value comes from |
| `src/agent/lib/plow.py` | the Plow HTTP contract — identity, chats, ticket, history, send, inference | the wire |
| `src/agent/commands/` | the replies — one file per command | what the agent says |
| `src/agent/lib/router.py` | which command a message maps to | how it decides |
| `src/agent/lib/latch.py` | the relay to the owner's Mac — one MCP `tools/call` (`Latch.call`) | how a command reaches a Mac |
| `src/agent/runtime.py` | the connection and its lifecycle | how it stays online, and who may use it |

`runtime.py` is the shell around the rest, and `__main__.py` starts it. A
message's path, top to bottom:

```
main()             config guard, signal handlers, run()
  Agent.run()      the reconnect loop; owns one session at a time
    Agent.session()  one connection: identify -> reach -> ticket -> socket -> backfill -> frames
      Agent.on_frame()   route one WebSocket frame
      Agent.on_message() filter, authorize, reply, checkpoint
        Agent._respond_with_ack()  ack first if the turn outlasts ACK_DELAY
        Agent.respond()  hand the text to the router
          router.route()   -> a command in commands/
            story()          -> plow.Plow.complete()   (the one inference call)
            agenda()/messages() -> lib/latch.py        (MCP to the owner's Mac)
```

Every arrow is a module boundary, and each module can be read without the others.

## The lifecycle, start to finish

```
 boot
  │  PLOW_API_BASE + PLOW_AGENT_TOKEN read from the environment
  │
  ├─ identify()        GET  /v1/agents/me        who am I        (best effort)
  │
  ├─ refresh_chats()   GET  /v1/chats            what may I serve
  │
  ├─ plow.ticket()     POST /v1/ws/ticket        single-use ticket (60s)
  │
  ├─ ws_connect()      WS   /v1/ws?ticket=...    the inbound channel
  │
  ├─ backfill()        GET  /v1/chats/{uid}/messages  per chat, catch up
  │
  └─ ┌─────────── frame loop ───────────┐
     │  message_received                │
     │    └─ on_message()               │
     │         ├─ respond()  -> text    │
     │         ├─ send()     -> POST    │
     │         └─ checkpoint()          │
     └──────────────┬───────────────────┘
                 socket closes
                    │
              run() backs off, reconnects
```

## Startup

`main()` does three things:

1. **Guards config.** No `PLOW_AGENT_TOKEN` is a fatal, explicit error (exit 2)
   rather than a confusing network failure later.
2. **Installs signal handlers.** `SIGINT` (Ctrl-C) and `SIGTERM` (`docker stop`)
   both set one `asyncio.Event`. That is the only way the process stops — it is
   cooperative, so the connection is closed cleanly inside the 35s grace period
   `compose.yml` allows.
3. **Runs.** `asyncio.run(agent.run())`.

## The reconnect loop — `run()`

This is the outer structure of the agent. It runs one session at a time and,
when a session ends, decides how long to wait before trying again.

```python
while not self._stop.is_set():
    try:
        async with aiohttp.ClientSession(...) as http:
            await self.session(http)          # blocks until the socket closes
    except CredentialRefused:
        return                                # terminal — stop the process
    except Exception:
        ...                                   # any other error: fall through and retry

    if session lasted >= 30s: attempt = 0     # only a healthy session resets the curve
    attempt += 1
    delay = immediate-if-first-time else 30 * 2**(attempt-2), capped at 300
    await sleep(delay)                        # interrupted early by _stop
```

Three deliberate details:

- **The first retry is immediate** (jittered under 250ms). The common cause of a
  dropped socket is a deploy — the backend is already serving from elsewhere, so
  waiting 30s is 30s of a dead line.
- **Short sessions do not reset the curve.** A server that accepts and instantly
  drops would otherwise keep the delay pinned at the jitter, i.e. four
  handshakes a second against something already broken. Only a session that
  stayed up ≥30s earns a fresh start.
- **401 ends everything.** Every retry would present the same revoked token, so
  sleeping is pointless — the process exits and tells you to re-mint.

## One session — `session()`

```python
await self.plow.identify(http)          # who am I, and is a Mac attached?
await self.refresh_chats(http)          # re-read the grant on every connection
ticket = await self.plow.ticket(http)    # fresh, single-use, 60s
url = self.plow.ws_url(ticket)
async with http.ws_connect(url, heartbeat=30) as ws:
    self.up_since = time.monotonic()    # used by run()'s health check
    for chat_uid in self.chats:
        await self.backfill(http, chat_uid)
    async for msg in ws:
        ... handle TEXT frames, break on close/error
```

Reach is re-read here rather than cached across runs, because the grant can
change while you were disconnected. The ticket is minted immediately before
dialing because it is single-use and expires in 60 seconds — minting it earlier
would waste it.

`total=None` on the session timeout (set in `run()`) matters: a default request
timeout would kill a long-lived WebSocket.

## Identity and reach

**`identify()` / `GET /v1/agents/me`** — logs the agent uid and line number, and
re-reads `mcp_url`, i.e. whether a Mac is attached (`mac=connected|none`). That is
asked on every connect rather than once per process because a Mac can attach after
the agent booted. A `404` is the documented "this token is not one agent" (e.g. a
multi-line grant); it is swallowed because it is not a failure, just no identity
answer. Anything else goes through `check()` in `lib/plow.py` — the one place a
status code becomes a decision.

**`refresh_chats()` / `GET /v1/chats`** — caches the granted chats on `self` and
picks a home. The grant **is** the access control; there is no per-chat config
anywhere. Two decisions:

- `has_more` is treated as an error. Serving half a grant silently would let the
  agent miss chats it should be in.
- **An empty grant is not an error.** A freshly minted agent has no chat at all
  until its line is first texted. The socket still connects, and the first
  inbound frame re-reads the grant (`on_frame`). This is what lets a brand-new
  agent wait for first contact instead of crash-looping.

Home selection: `PLOW_HOME_CHANNEL` if set, else the owner's 1:1 (`_solo_dm`:
no peer agent, at most one human).

## The inbound path

### `on_frame()` — routing

A frame is either `{"type": "connected"}` (the connection acknowledgement, no
message) or a typed event. Only `message_received` carries a chat message.

If a frame names a chat not in the cache, the chat is newer than the last reach
read, so it re-reads the grant exactly once before deciding. Event ids are
deduped in `seen_events`.

### `on_message()` — filtering, authorizing, replying, checkpointing

The filters each exist for a reason:

| check | why |
|---|---|
| `direction != "inbound"` | drops the echo of our own outbound sends |
| `sender.type` not member/agent/system | drops shapes we do not serve |
| agent sender without `relationship == "peer"` | an outbound agent sender carries no `uid`; only peer agents are real inbound |
| `(chat_uid, uid)` already seen | the socket/backfill overlap — never handle twice |
| `_authorized(sender)` | only the owner or an allow-listed handle is answered — see below |
| empty body and no attachments | nothing to answer |

**Authorization.** The line is a real phone number, so reachability is not
privacy. `_authorized()` answers only the owner (`sender.role == "owner"`) and
any handle named in `PLOW_ALLOWED_HANDLES`; a peer agent or a `system` sender is
never a person asking, so neither invokes the agent. A refused message is
**logged and ignored, but still checkpointed**, so a restart does not replay it.
The README's "Sender fields" table lists every `type` / `role` / `relationship`
value and what each means.

**Logging.** Every invocation logs the sender's full identity through
`_describe()` — `type`, `role`, `relationship`, name and handle — and `DEBUG`
additionally prints the whole chat roster (`_roster()`). That is what makes "who
texted, and were they allowed" a question the logs answer without a debugger.

Then: `_respond_with_ack()` runs the command (acking first if it runs long — see
below), `plow.send()` posts the reply, and finally `_save_checkpoint()` records
the uid. The checkpoint is written **last**, after the reply — a crash mid-turn
replays the message rather than dropping it.

### `_respond_with_ack()` — the acknowledgement bubble

iMessage's typing indicator is emitted by the *sending device*, and this agent
never touches iMessage — its only outbound primitive is a whole message
(`plow.send`), with no edit or delete. So there is no way to show real dots. The
stand-in is a short message: start the turn as a task, wait `config.ACK_DELAY`
(`0.75s`), and if the turn is still running, post `config.ACK_TEXT` (`On it…`)
while it continues. The reply is returned to `on_message`, which sends it as
usual — so the ack lands first and the answer follows it.

Three consequences worth stating:

- **Only slow turns ack.** Every free command (`echo`, `help`, `time`) finishes
  well inside the window, so the bubble appears only when the owner would
  otherwise sit in silence — a story (~3.7s) or a `what's next` waiting on the
  Mac. Nothing decides *which* commands ack; the clock does.
- **`ACK_DELAY <= 0` disables it**, and a failed ack send is logged and swallowed
  — losing the acknowledgement is not worth losing the answer.
- **The bubble is permanent.** With no edit or delete, `On it…` cannot be
  retracted once the real reply lands. That is the price of a channel with no
  typing indicator; the open question is whether the platform should offer one
  instead — see `docs/QUESTIONS.md`.

### `lib/router.py` + `commands/` — what a message is

The table lives with the commands; `router.py` is only the mechanism that walks
it. Routing happens *before* anything costly, and **order is the policy** — first
match wins, so the catch-all comes last:

```python
# src/agent/commands/__init__.py -- order IS the policy: first match wins
COMMANDS = (
    ("help",     lambda text: text.lower() in ("help", "commands", "?"), help_cmd.run),
    ("story",    STORY_REQUEST.search,             story.run),
    ("time",     time_cmd.matches,                 time_cmd.run),
    ("agenda",   agenda.matches,                   agenda.run),
    ("messages", messages.matches,                 messages.run),
    ("echo",     lambda text: True,                echo.run),   # catch-all, last
)
```

…and `Agent.respond()` is just "hand it to the router, run what it picks":

```python
name, handler = route(COMMANDS, text)
return await handler(self.plow, http, text)
```

Routing **first** is the point: only a story request reaches the model, and only
`agenda`/`messages` reach your Mac, so the default reply costs nothing and touches
nothing. The match is word-bounded — `\b(story|stories|tale|tales|bedtime)\b` —
because "history" contains "story" and a substring test would send it to the model.

### How Plow inference is called — `plow.Plow.complete()`

The entire model integration is one method in `lib/plow.py`: one HTTP request,
OpenAI-shaped, on the credential the agent already holds. No SDK, no API key, no
provider config:

```
POST {PLOW_API_BASE}/v1/chat/completions
Authorization: Bearer <PLOW_AGENT_TOKEN>          # the one credential this holds
{
  "model": "anthropic/claude-sonnet-5",           # PLOW_MODEL overrides
  "max_tokens": 500,
  "messages": [
    {"role": "system", "content": <the story instructions>},
    {"role": "user",   "content": <the owner's text>}
  ]
}
-> {"choices": [{"message": {"content": "<the story>"}}], ...}
```

The reply is read from `choices[0].message.content` and then travels back over
the socket as an ordinary message. The call is **billed to the Plow account**;
the agent token carries `llm:chat` (so it may call this) but not `llm:models`
(so it cannot list the catalog — the README carries the model list).

Failures are mapped to a sentence the owner can act on rather than swallowed:
`402` is an empty Plow balance, `400` is a model id the allowlist refuses,
anything else transient becomes one line back in the chat. A `401` is still
terminal and propagates — the whole agent stops, which is `run()`'s rule
everywhere.

Two consequences worth stating: what the model returns is **untrusted text** —
it is sent to a phone and nothing more — and every message routed to the model
is **spend**, which is why the guard in `_authorized()` (and the router itself)
matter more once a model is wired in.

To add a command: write a module in `src/agent/commands/` exposing
`async def run(plow, http, text) -> str`, then add one row to `COMMANDS` in that
directory's `__init__.py`. Nothing in `runtime.py` or `router.py` changes — that
boundary is the point of the split.

### How the owner's Mac is reached — `lib/latch.py`

The last outbound integration, and the only one that leaves the platform.
`GET /v1/agents/me` returns an `mcp_url` — that is the relay — and calling through
it is one JSON-RPC `tools/call`:

```
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "plow-messages", "arguments": {"subcommand": "search", ...}}}
```

Four details, each of which cost real debugging time:

- **Stateless.** There is no `initialize` handshake to perform first; a bare
  `tools/call` is answered. That is why the whole client is about sixty lines.
- **The answer may be JSON or SSE.** Latch replies as `text/event-stream`, so the
  body is unwrapped from its `data:` lines before parsing. Both shapes are accepted.
- **A refusal is a sentence, not an error code.** A provider the Mac has disabled
  returns `isError: true` with a human explanation — and it arrives *before* any
  approval card, because a disabled provider is refused before an intent exists.
- **Redirects are never followed.** The call carries the bearer, so following one
  would hand the credential to wherever it pointed.

Two commands use it, and neither knows anything about the Mac: `agenda` ("what's
next") reads the calendar, and `messages` ("last message from X") resolves a name
to handles and then searches the message archive. Phrasing a request is the agent's
business; whether it is permitted is the Mac's.

## Backfill — catching up after a gap

A WebSocket does not replay frames, so anything sent while the socket was down
is simply missed. `backfill()` closes that hole:

```python
messages = await self.plow.history(http, chat_uid, config.BACKFILL_LIMIT)  # newest page
last = self.checkpoints.get(chat_uid)
if last is None:                                # first connect: anchor, replay nothing
    self._save_checkpoint(chat_uid, messages[0]["uid"])
    return
# walk back to the last handled uid, then replay oldest-first
```

Two decisions:

- **First connect anchors at the newest message.** Otherwise a new agent would
  answer its entire history on boot.
- **Replay is oldest-first** (`reversed(missed)`), so messages land in the order
  they were sent, not the order the API returned them.

The dedupe in `on_message` absorbs any overlap between backfill and a live frame.

## Sending — the delivery-unknown rule

```python
if resp.status in (408, 424) or resp.status >= 500:
    log.warning("... delivery unknown, not resending")
    return False
```

A `408`/`424`/`5xx` can arrive **after** Plow accepted the POST. Treating that as
a clean failure invites a retry that sends the message twice to a real phone. So the
agent reports it and stops — the safe direction. Anything else non-2xx
raises through `check()`.

## Durable state

One file: `last_uids.json` in `AGENT_STATE_DIR` (`/data` in the container, a
volume). Maps chat uid → last handled message uid.

Written with a temp file plus `os.replace` (atomic rename). A plain write
truncates first, so a stop mid-write would leave an empty cursor — which reads
back as "no baseline" and silently skips the gap. The rename means a reader sees
the old value or the new one, never neither.

`docker compose down -v` deletes this volume, so the next boot is a first
connect: it anchors at the newest message instead of trying to backfill.

## Design decisions, in one table

| decision | reason |
|---|---|
| ticket minted right before dialing | single-use, 60s life |
| first reconnect immediate, then 30s→300s | ordinary cause is a deploy; the backend is already back |
| curve resets only after a ≥30s session | a drop-loop server must not be hammered |
| 401 stops the process | the token is dead; retrying is pointless |
| checkpoint written after the reply | crash mid-turn replays instead of dropping |
| atomic checkpoint write | a truncated cursor reads as "no baseline" and skips a gap |
| 408/424/5xx = delivery unknown | never double-send to a real phone |
| empty grant is fine; socket still connects | a new agent has no chat until first text |
| re-read reach on every connect | the grant can change while you are away |
| re-read identity on every connect | a Mac can attach while the agent runs |
| the guard runs before any command | reachability is a spend surface once a model is wired in |
| a turn over `ACK_DELAY` is acked before its reply | silence reads as "not working", and iMessage has no typing indicator the agent can drive |

## What is deliberately not here

The Hermes runtime adds a lot on top of this contract: tool calling, sessions,
per-chat recall, group trust and disclosure rules, media fetch/upload, voice
memos, goals, the email line, and a full MCP client with its own tool registry.
None of it is required to be a Plow agent — this file is the floor. What this repo
does add is the *narrowest useful* version of two of them: one model call
(`Plow.complete()`) and one relay call (`Latch.call()`), about sixty lines
each, because those two are what make an agent feel like it can do anything at all.
Everything above it (the socket, the reconnect curve, the checkpoint, the send path)
is the part you would otherwise have to rebuild, and is exactly what is worth
reading before you add to it.
