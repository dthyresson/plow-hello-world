# hello-world — a minimal, Hermes-free Plow agent

A single-file agent that answers over Plow Chat. It reads `PLOW_API_BASE` and
`PLOW_AGENT_TOKEN`, learns who it is and which chats it may serve, holds the
granted WebSocket, and replies to each message: a small command router that
echoes by default, lists what it can do on `help`, and tells a short animal story
— through Plow's own inference — when asked.

No Hermes runtime, no framework — one file, one dependency (`aiohttp`). The
whole Plow contract is implemented directly, so you can see exactly what an
agent does.

## What's here

The code is five small modules, split so each reads on its own — `config.py`
(settings), `plow.py` (the Plow API), `commands.py` (the replies), `router.py`
(which command answers), `agent.py` (the connection). `HOW-IT-WORKS.md` walks
them in order.

| file | what it is |
|---|---|
| `agent.py` | the connection and its lifecycle: socket, reconnect, checkpoint, the guard |
| `plow.py` | the Plow HTTP contract: identity, chats, ticket, history, send, inference |
| `commands.py` | the replies — one function per command (echo, help, story) |
| `router.py` | which command a message is routed to |
| `config.py` | every environment setting, in one place |
| `Dockerfile` | `python:3.11-slim`, runs `agent.py` as PID 1 |
| `requirements.txt` | `aiohttp` (HTTP **and** WebSocket in one dep) |
| `compose.yml` | how it runs locally: builds this dir, loads `./plow-credentials`, `/data` volume |
| `.gitignore`, `.dockerignore` | both exclude `/plow-credentials` — the credential never enters git or the image |
| `HOW-IT-WORKS.md` | a walkthrough of the code, top to bottom |
| `LIFECYCLE.md` | dev → test → prod, and how a change reaches the live agent |
| `README.md` | this file: setup, launch, start/stop, logs, deploy, troubleshooting |

## Prerequisites

- **Docker** (a daemon the `docker` CLI can reach)
- **The `plow-agents` CLI** — https://github.com/plow-pbc/plow-agents
- **A Plow account**, activated by text (`plow-agents login`)

### Docker on macOS (Colima)

Docker Desktop is not required. Colima runs a lightweight Linux VM and gives you
the same `docker` CLI:

```sh
brew install colima docker docker-compose docker-buildx
mkdir -p ~/.docker/cli-plugins
ln -sfn "$(brew --prefix)/opt/docker-compose/bin/docker-compose" ~/.docker/cli-plugins/docker-compose
ln -sfn "$(brew --prefix)/opt/docker-buildx/bin/docker-buildx" ~/.docker/cli-plugins/docker-buildx
colima start

docker run --rm hello-world        # sanity check
```

`docker compose` and `docker buildx` only appear once the plugin symlinks above
exist — Homebrew installs the binaries but does not wire them into the CLI.
Colima is on the default `docker` context after `colima start`.

### The `plow-agents` CLI

```sh
git clone https://github.com/plow-pbc/plow-agents.git ~/projects/plow/plow-agents
export PATH="$HOME/projects/plow/plow-agents/bin:$PATH"   # or call it by full path
plow-agents --help
```

## Start and stop Docker

There are two layers, and it is worth keeping them straight. Nothing runs
without the **engine**, and here the engine is a VM (Colima) — so stopping the
engine stops every container with it.

**The engine (Colima)**

```sh
colima start      # boot the VM; the `docker` CLI needs this up
colima status     # is it running?
colima stop       # shut the VM down — ALL containers stop with it
brew services start colima   # optional: start it automatically at login
```

**Your agent (the compose project — run these from this repo directory)**

```sh
docker compose up -d        # start it (add --build after editing agent.py)
docker compose ps           # what is running
docker compose logs -f      # follow the agent's output; Ctrl-C stops watching, not the agent
docker compose stop         # stop the container, keep it and the /data volume
docker compose start        # start it again
docker compose down         # stop and remove the container + network (keeps the volume)
docker compose down -v      # ...and delete the /data volume (fresh checkpoint next boot)
```

So `docker compose down` stops only your agent, while `colima stop` stops Docker
itself. If `docker` commands suddenly fail with "cannot connect to the Docker
daemon", the engine is down — `colima start`.

## One-time: log in

```sh
plow-agents login
```

It prints a code and a number, then **waits**:

```
  Text  Plow Activate: XXXXX  to  +1XXXXXXXXXX

Waiting for that text ...
```

**The CLI does not send that text — you do.** From your phone, in Messages (or
any SMS app), text the code exactly as printed to that number:

```
Text  Plow Activate: XXXXX
```

It then polls Plow until your text lands, flips `pending` → `verified`, writes
your **account token** to `~/.config/plow/token` (mode 600), and exits. Sitting
at `Waiting for that text ...` does nothing by itself — it is waiting on *your*
text, so open your phone and send it while the command is still running. (You
have 15 minutes before the code expires.)

> **The trap that eats most of the time here.** Every `plow-agents login`
> mints a *fresh* code **and** a secret, and the running process only accepts
> the text for *its own* pair. If you text a code from an earlier attempt (or
> from the Plow app) — or you Ctrl-C and re-run `login` — the message that code
> pairs with is gone, so Plow may reply "Plow activated!" in iMessage while the
> terminal sits at "Waiting for that text ..." forever. **Run `login` once, text
> the code it just printed, and don't restart it.** If it expires, run `login`
> again for a new code and text *that* one.

## Launch everything (local)

```sh
plow-agents lines                 # pick a row whose STATUS is "free"
plow-agents deploy --local --line ln_xxx
```

`deploy --local` does three things from this directory:

1. checks `.dockerignore` excludes `plow-credentials` (it must, before building)
2. `mint`s a line-scoped agent credential into `./plow-credentials`
3. runs `docker compose up --build -d`

Watch it start:

```sh
docker compose logs -f
```

A healthy boot looks like:

```
starting against https://api.plow.co
identity: agent=<uid> line=+1XXXXXXXXXX
chats: none yet (home=None)
websocket connected
```

`chats: none yet` is normal for a brand-new agent — it has no chat until you
first text its line. The socket is already open and waiting.

## Test it

Text the number `plow-agents lines` shows for your line. What you get back:

| text this | get |
|---|---|
| anything at all | `hello world. You said: …` (echo) |
| `help` | the command list |
| `tell me a story about a penguin` | a short story, generated with Plow inference |
| `i want a bedtime tale about a lion` | …same, any animal |

No inbound ports are published — the agent dials out to Plow, which is why
`compose.yml` exposes none.

## Run it without Docker

Fastest loop, same code, no container. Needs Python 3.11+.

```sh
plow-agents mint ln_xxx           # writes ./plow-credentials (needs a free line)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

set -a; . ./plow-credentials; set +a    # exports PLOW_API_BASE + PLOW_AGENT_TOKEN
AGENT_STATE_DIR=./plow-state python3 agent.py
```

Both paths run the identical code — the container just supplies the same two
environment variables from `./plow-credentials`.

## Configuration

| env var | required | meaning |
|---|---|---|
| `PLOW_API_BASE` | no | API root, no `/v1` (default `https://api.plow.co`). In `plow-credentials`. |
| `PLOW_AGENT_TOKEN` | **yes** | the line-scoped bearer. In `plow-credentials`. |
| `PLOW_HOME_CHANNEL` | no | a `cht_…` home chat; otherwise the agent picks its solo owner DM |
| `PLOW_MODEL` | no | inference model id (default `anthropic/claude-sonnet-5`) — any id from the allowlist, see [Inference](#inference-the-model) |
| `AGENT_STATE_DIR` | no | where the checkpoint file lives (default `./plow-state`; compose sets `/data`) |
| `LOG_LEVEL` | no | `DEBUG`, `INFO` (default), `WARNING` or `ERROR` — see [Logs](#logs) |
| `PLOW_ALLOWED_HANDLES` | no | comma-separated handles besides the owner allowed to invoke the agent — see [Who may invoke the agent](#who-may-invoke-the-agent) |

## Who may invoke the agent

The line is a real phone number, so anyone who knows it can text it and Plow
opens a chat for them. This agent answers **only the owner** unless you name
others:

```sh
PLOW_ALLOWED_HANDLES="+14155550000,alice@example.com" docker compose up -d --build
```

An unauthorized message is logged and ignored — and still checkpointed, so a
restart does not replay it. Use a throwaway line for experiments;
`plow-agents revoke ln_…` retires the agent and frees the line.

### Sender fields and every value they take

Each inbound message carries a `sender`, and each chat lists its `participants`.
The fields that decide authority:

| field | on | possible values | meaning |
|---|---|---|---|
| `type` | sender, participant | `member`, `agent`, `system` | a human; another Plow agent; a Plow system event |
| `role` | member | `owner`, or *absent* | the account owner; absent = unprivileged |
| `relationship` | agent | `self`, `peer` | this agent; another agent in the room |
| `relationship` | member | free text (`wife`, `landlord`) | a label **your** owner recorded — attribution, not authority |
| `provider_key` | member, line | a phone number or email | the handle the allowlist matches |
| `provider_type` | participant | `imessage`, `email` | which line the participant is on |

`type` gates first: a peer agent or a system event is not a person asking, so
neither invokes the agent. Among members, only `role: "owner"` is privileged;
everyone else must be named in `PLOW_ALLOWED_HANDLES` by `provider_key`.

Observed live from `GET /v1/chats` on a solo owner DM (the agent's own name and
number live on its nested `line`):

```jsonc
"participants": [
  {"type": "agent",  "relationship": "self",
   "line": {"display_name": "Willow", "provider_key": "+15550100002", "provider_type": "imessage"}},
  {"type": "member", "role": "owner",
   "display_name": "+15550100001", "provider_key": "+15550100001", "provider_type": "imessage"}
]
```

Two things worth knowing: `role` has only ever been `owner` in what we have seen
(a non-owner member simply carries no owner role), and a member's `relationship`
is something the owner said, not something the platform verified.

## Deploy it to Plow (hosted)

The local stack is for development. A hosted agent runs on Plow, pulled by
digest. Plow's VMs are amd64, and it pulls publicly, so:

`YOUR_ACCOUNT` (and `YOUR_GITHUB_USERNAME`) is **your GitHub username or
organization** — the namespace your image lives under on GHCR (GitHub Container
Registry). It is not something Plow gives you. Find yours with
`gh api user -q .login`, then substitute it everywhere below.

```sh
# 1. build for the cloud architecture (macOS arm64 cross-builds via qemu, so it is slow)
plow-agents image build ghcr.io/YOUR_ACCOUNT/hello-world-agent:v1

# 2. push. GHCR needs the write:packages scope, which a plain `gh auth login`
#    token does not carry -- grant it and reuse the token (or pass a classic
#    PAT with write:packages as the password instead):
gh auth refresh -s write:packages
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
plow-agents image push ghcr.io/YOUR_ACCOUNT/hello-world-agent:v1

# 3. make the package PUBLIC in your registry, then copy repository@sha256:… from the last line

# 4. deploy by digest on a free line
plow-agents deploy ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:… --line ln_xxx
plow-agents agents            # poll until status is running
```

Plow deploys **by digest**, never by tag — a tag names different bytes next week.

### GHCR (GitHub Container Registry)

GHCR is GitHub's container registry. A **registry is distribution, not
execution**: it stores container images and hands them out, and runs nothing.
Think of it as npm for a JavaScript package, or a GitHub release for a binary —
it ships the artifact; the artifact runs somewhere else. Nothing about it is
Plow-specific; it is just a public place your image can live.

**How a registry works.** An image is a stack of content-addressed layers plus a
manifest. You `push` it once; anyone with read access `pull`s it. It has two
names: a mutable **tag** you choose (`v1`, `latest`), and an immutable
**digest** (`sha256:…`) computed from the bytes. The tag is a convenience; the
digest is the identity, and it is what makes "the same image tomorrow" a real
guarantee rather than a hope.

**How Plow uses it.** Plow's involvement is a single anonymous `pull` at
provision time — one step in the deploy, not part of runtime:

```
you build ──push──▶ GHCR ──pull(by digest)──▶ Plow provisions a VM ──▶ your agent runs
(your Mac)      (stores bytes)   at deploy time only                (on Plow's host)
```

Plow fetches the image once, by digest, when it creates the agent's VM, and
never talks to GitHub again. The agent runs on Plow's infrastructure and the
phone line is served from there. Two things follow from that shape: the image
must be **public** (Plow pulls anonymously), and an **update only takes effect
on a new deploy** — a running agent keeps the digest it booted with.

| who | does what |
|---|---|
| you | `build` the image (on your machine) and `push` it |
| GHCR | stores the layers and serves them; executes nothing |
| Plow | pulls by digest at deploy time, runs the container, serves the chat |

A reference breaks down like this:

```
ghcr.io / <owner> / <image> : <tag>
   │         │        │         └ human label: v1, latest — Plow ignores it
   │         │        └ package name: hello-world-agent
   │         └ your GitHub username or org (e.g. octocat)
   └ the registry host
```

**Find your owner** — `gh api user -q .login` (or the name at the top of
github.com). Substitute it for `YOUR_ACCOUNT` everywhere.

**Log in.** Pushing needs a token with the `write:packages` scope, which a normal
`gh auth login` token does not carry. Grant it and reuse the token:

```sh
gh auth refresh -s write:packages
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

Or create a **classic** personal access token with `write:packages` and use it as
the password. (Fine-grained PATs cannot push to GHCR.)

**Make the package public.** Plow pulls anonymously, so the image must be public
— it is private by default, and a private package makes Plow's pull fail:

1. push once, then open
   `https://github.com/users/YOUR_ACCOUNT/packages/container/hello-world-agent`
2. **Package settings** → **Danger Zone** → **Change visibility** → **Public**

(You can also reach it from your profile → **Packages**.)

**Shipping a new version.** Build and push again — a new tag, or the same tag
with different bytes — note the new `sha256:…` that `image push` prints, and
deploy that digest. Plow pins digests, so agents already running keep the image
they were deployed with until you deploy again or promote the new one.

**GHCR is not required.** Plow only needs a *publicly pullable* image referenced
by digest; the registry is your choice. Any host works — Docker Hub
(`docker.io/you/img`), Quay (`quay.io/you/img`), public ECR
(`public.ecr.aws/you/img`) — and `plow-agents` asks only for an explicit registry
host, so substitute it for `ghcr.io` in every command above. GHCR is the default
here because it is free and needs no new account if you already have GitHub; pick
another and you need *that* provider's account and push token instead, not a
GitHub one. To skip registries entirely, deploy a Plow-ready listing:
`plow-agents deploy exe:hermes --line ln_xxx`.

## The wire contract

Everything the agent talks to, in one place (from
`plow-pbc/hermes-plugin-plow`, which is the Hermes implementation of the same
contract):

| call | purpose |
|---|---|
| `GET /v1/agents/me` | identity: agent uid, line number, signup. `404` = token is not one agent |
| `GET /v1/chats` | the credential's granted chats (the grant *is* the access control) |
| `GET /v1/chats/{uid}/messages?limit=N` | history, **newest first** |
| `POST /v1/chats/{uid}/messages` `{"body": "…"}` | send a reply → `{"uid": "…"}` |
| `POST /v1/ws/ticket` | → `{"ticket": …}`; single-use, 60s life — mint right before dialing |
| `WS /v1/ws?ticket=…` | inbound frames |

Socket frames:

```jsonc
{"type": "connected"}
{"event_type": "message_received", "event_id": "…", "chat_id": "cht_…",
 "data": {"message": {"uid": "…", "direction": "inbound", "body": "hi",
                      "sender": {"type": "member", "role": "owner", "display_name": "…"},
                      "created_at": "…", "attachments": [], "reply_to": null}}}
```

Behavior the agent implements, and why:

- **401 is terminal.** Every retry would present the same dead token, so it
  stops; `403` is resource-scoped and retried.
- **Reconnect curve.** First retry is immediate (jittered <250ms); then 30s
  doubling to a 5-minute cap. The curve resets only after a session that stayed
  up ≥30s — otherwise a server accepting-and-dropping would be hammered.
- **Checkpointing.** The last handled message uid is written atomically per
  chat. On a later connect it backfills what was missed while the socket was
  down (the socket does not replay frames).
- **Delivery-unknown.** A `408`/`424`/`5xx` on send may have been accepted, so
  it is reported and **never** blindly resent — that is how you double-text a
  real phone.

## Latch (reaching your Mac)

**Latch is a local MCP server, running on your Mac, that does approved work on
that Mac for a remote AI agent — and gives the agent the *use* of your logins
without ever giving it the *contents*.**

[Latch](https://plow.co/latch) ships as a Mac app; underneath it are three
stacked parts — an MCP **capability broker** (the tools), an **approval +
sandbox gate**, and a local **password vault**. **This agent does not use it**,
and it is **Mac only**; the rest of this section is what it is and how you would
wire this agent to it if you wanted to.

### How it fits together

Your **Mac dials out** to the **Plow relay**; the relay authenticates the calling
agent (your `PLOW_AGENT_TOKEN`) and forwards MCP calls to an MCP server running
in the Mac app. Nothing on your Mac listens publicly.

```
your agent ──HTTP (JSON-RPC)──▶ Plow relay ──▶ Mac app (Latch) ──▶ intent → approval → sandbox → run
   (here)                       (auth: your token)                 (Gatekeeper, or you, decides)
```

Every operation becomes an **intent** the owner approves — or the Gatekeeper
reviewer decides against a task you approved — and then runs inside a seatbelt
sandbox. Saved passwords are filled into sites without being returned to the
agent.

### How its reviewer gets inference

Gatekeeper is a **hosted model call**, not local code:

- **Where:** Plow's OpenAI-shaped `POST /v1/chat/completions`. Your **Mac dials
  out** with its relay credential; nothing runs the model on the Mac.
- **Which model:** `anthropic/claude-sonnet-5` — the `anthropic/` prefix is the
  litellm-native id Plow's allowlist requires. It is pinned as `REVIEWER_MODEL`
  in [`adversarialAgent.ts`](https://github.com/plow-pbc/latch/blob/main/apps/desktop/src/adversarialAgent.ts)
  and recorded on every `adversarial_review_started` audit row.
- **Shape:** a strict JSON verdict (`allow`/`deny`/`ask`), adaptive thinking, a
  90-second budget — and it is shown **one operation and no history**,
  deliberately: feeding it the audit stream made it ratchet, where a pile of
  denials became evidence to deny more.
- **Fails closed:** no verdict → `ask`, and with nobody behind the reviewer
  (adversarial mode) → **deny**. A `402` surfaces as `no_credits`.
- **Billed** to the same Plow account as your agent's own inference — so the
  reviewer is a service you pay for, not code on the machine it guards.

### What it can actually reach

Not just a shell. Three families of tools, and the mix is what matters:

| family | tools | reaches |
|---|---|---|
| files | `plow_read_file`, `plow_write_file` | documents, configs, anything on disk you allow |
| commands | `plow_run_command` | CLI tools, with streaming output |
| browser | `plow_browser_open` (+ skill-driven flows) | any site you are already signed in to |

The **browser** is usually the point: it is *your* browser, already logged in to
your mail, calendar, bank and everything else, so the agent reads and acts inside
those accounts with **no API key and no password** — the page is simply already
you.

And the command tool is not a raw terminal. You pass `argv` plus a declared
`goal` and `read_paths`, and those declarations are what the approver and
Gatekeeper actually evaluate — the structured shape exists so a human (or the
reviewer) can see *what* is being asked and *why* before it runs. The policy
gate is the product; the command runner is one of the things behind it.

### How it knows what to run

The tools are generic on purpose — `plow_read_file`, `plow_write_file`,
`plow_run_command` and `plow_browser_open` know nothing about calendars or email.
What turns "check my email" into a concrete command is three things, and none of
them is Latch:

1. **Skills — the Mac publishes recipes.** `plow_list_skills` returns a manifest
   (one description each); `plow_read_skill` returns the full text. A skill is
   the precise procedure for one store — which binary, which flags, which path.
   The agent lists them, reads the one whose description covers the ask, and
   follows it. That is where "the exact commands" for mail and calendar come
   from.
2. **The model's own knowledge** fills the gaps around the recipe.
3. **Latch stays domain-agnostic.** It gates and runs whatever `argv` it is
   handed. It has no idea it is reading a calendar.

For email and calendar specifically, a skill documents one of two routes:

- **CLI** — Google Workspace through **`plow-gog`**, a CLI Latch ships (groups
  like `gmail`, `calendar`, `contacts`, `drafts`, with list/send/reply verbs).
  The skill names the argv; the model runs it with `plow_run_command`.
- **Browser** — open `mail.google.com` / `calendar.google.com` in *your*
  logged-in browser and read the page. No key, no password.

Two examples that are real rather than hypothetical: the **contacts** skill
resolves a name by running `/usr/bin/sqlite3 -readonly -json <AddressBook>.abcddb`,
and the **iMessage** skill reads the Messages database the same way.

The consequence is worth stating plainly: because the **model** chooses the
`argv`, a mischosen command is the risk. That is why a `plow_run_command` intent
carries a declared `goal` and `read_paths` — the approver and Gatekeeper judge
*those* — and why the plugin adds a separate approval gate for `plow-gog`
**send** verbs. A read is one thing; mailing on your behalf is another.

### Where to read the skills

The skills the Mac publishes are checked into [`plow-pbc/latch`](https://github.com/plow-pbc/latch)
— and that is the same text the agent receives from `plow_read_skill`, so reading
these is reading what the agent sees:

| skill | source |
|---|---|
| `google-workspace` — Gmail + Calendar via `plow-gog` | [`providers/gogSkill.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/providers/gogSkill.ts) |
| `plow-messages` — the iMessage archive via `plow-messages` | [`plugins/messages/skill.md`](https://github.com/plow-pbc/latch/blob/main/apps/desktop/plugins/messages/skill.md) |
| contacts | [`contactsSkill.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/contactsSkill.ts) |
| iMessage *sending* | [`imessageSkill.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/imessageSkill.ts) |
| WhatsApp | [`whatsappSkill.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/whatsappSkill.ts) |
| browser | [`browser/browsingSkill.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/browser/browsingSkill.ts) |
| the registry / loader (also loads owner-authored `$DOMO_HOME/device/skills/*.md`) | [`skills.ts`](https://github.com/plow-pbc/latch/blob/main/packages/device-core/src/skills.ts) |

To enumerate every skill in the repo yourself:

```sh
gh api "repos/plow-pbc/latch/git/trees/main?recursive=1" --jq '.tree[].path' | grep -i skill
```

Two notes on coverage: **mail means Gmail** (through `plow-gog`, above) or the
browser route — there is no Apple Mail skill; and a skill is *documentation, not
capability*, so reading one needs no approval.

### How an agent reaches your local info

The mechanism, end to end:

1. **The Mac holds an outbound connection** to the Plow relay — no inbound port,
   no public address on your Mac.
2. **The agent posts a tool call** to the relay, authenticated with its token;
   the relay forwards it down that connection to the Mac.
3. **You approve** it (or Gatekeeper decides against a task you authorized), and
   it runs inside the derived sandbox.
4. **The result travels back** up the same connection: file contents, command
   stdout, or the text of a page.

So your local information does leave the Mac — that is the whole point, and it is
why the approval step is the gate that matters. The one exception is the vault:
Latch fills saved passwords into approved pages without ever returning them, so
the agent can act *as* you without being able to read the credential.

### The one thing the agent needs

The relay endpoint, plus the bearer it already has:

- **relay URL** — exported as `PLOW_MCP_URL` on hosted agents, and carried as
  `mcp_url` in the cloud identity shape. **Absent means no Mac**, so the agent
  should say it cannot reach the Mac rather than pretend.
- **bearer** — `PLOW_AGENT_TOKEN`, the same credential the Plow API uses.

A call is then just JSON-RPC `tools/call` over HTTP:

```python
MCP_URL = os.environ.get("PLOW_MCP_URL")          # None = no Mac connected

async def relay_tool(http, name, arguments):
    """One JSON-RPC tools/call through Latch's relay to the Mac."""
    headers = {**AUTH, "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments}}
    async with http.post(MCP_URL, json=body, headers=headers) as resp:
        raw = await resp.text()
    if raw.lstrip().startswith("event:") or "data:" in raw:     # SSE-wrapped reply
        raw = "\n".join(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
    result = json.loads(raw)["result"]
    return result.get("structuredContent") or json.loads(
        next(c["text"] for c in result["content"] if c.get("type") == "text"))
```

Then a trigger in `respond()` — the crudest useful version:

```python
if text.startswith("run:"):
    out = await relay_tool(http, "plow_run_command",
                           {"argv": ["/bin/echo", text[4:].strip()], "goal": "echo"})
    return out.get("output", "(no output)")
```

Text `run: hi from my Mac`, approve the prompt on the Mac, and the agent answers
with the Mac's output. A real agent lets a model pick the tool from
`plow_list_skills` instead of a `run:` prefix — the plumbing is identical.

### Tools Latch exposes

From `packages/mcp-server/src/tools.ts` (the authoritative list):

| group | tools |
|---|---|
| files | `plow_read_file`, `plow_write_file` |
| commands | `plow_run_command`, `plow_run_applescript`, `plow_get_output` |
| browser | `plow_browser_open`, `plow_browser_request`, `plow_browser`, `plow_browser_close` |
| vault | `plow_vault` (lists/describes items; never returns a value) |
| skills | `plow_list_skills`, `plow_read_skill` |
| audit / status | `plow_history`, `plow_device_status` |
| long-running / deferred | `plow_get_result` |

Two behaviors worth knowing, because they are not uniform:

- **Reading and sending take different transports.** A read on a store goes
  through a bundled CLI (e.g. `plow-messages` queries `chat.db`, decoding the
  `attributedBody` blob SQL cannot); a *send* goes through
  `plow_run_applescript`, which runs outside the sandbox because Messages
  refuses Apple events from a sandboxed sender (`-10004`).
- **Reads can be remembered; sends never are.** An unattended read like
  `plow-messages search` can be approved once as a rule (collapsed to the
  subcommand); an AppleScript is decided fresh every time — no always-allow rule
  is ever stored for one.

### Caveats

- **Every call is an approval.** It blocks on the intent and the owner can deny
  it, so the reply path must handle "pending or refused", not just success.
- **Send a truthful `goal`.** `plow_run_command` takes `argv`, `read_paths` and a
  `goal`; the goal and paths are what the approver and Gatekeeper evaluate.
- **Never follow redirects** on the relay call. It carries your bearer, and a
  compromised Mac could redirect you to a host that harvests it — the plugin
  ships a no-redirect opener for exactly this reason.
- **The Mac must be awake, with Latch running**, and this agent granted access.
- **Running commands is the sharp edge.** A Mac-relay tool is not a read-only
  convenience; treat anything the *model* chooses to run as untrusted input.

## Logs

The agent logs to stdout, so Docker captures it — no log config, nothing written
inside the container:

```sh
docker compose logs              # everything so far
docker compose logs -f           # follow / tail (Ctrl-C stops watching, not the agent)
docker compose logs --tail=100   # the last 100 lines
docker compose logs -f --timestamps
docker compose logs > agent.log  # capture to a file
```

A healthy boot, one echo, and one story looks like this — each invocation logs
the sender's full identity (`type`, `role`/`relationship`, name, handle), and an
inference call logs what was asked and what came back:

```
INFO starting against https://api.plow.co (allowlist: owner only)
INFO identity: agent=… line=+1…
INFO chats: … (home=cht_…)
INFO websocket connected
INFO inbound cht_… from member role=owner Sam (+15550100001): 'Dt'
INFO route: echo
INFO outbound cht_…: 'hello world. You said: Dt'

INFO inbound cht_… from member role=owner Sam (+15550100001): 'tell me a story about a penguin'
INFO route: story
INFO inference ok: model=claude-sonnet-5 finish=stop tokens in/out/total=126/209/335 cached=0 in 3728ms
INFO inference reply (1096 chars): Little Pip the penguin waddled across the snow…
INFO outbound cht_…: 'Little Pip the penguin waddled across the snow…'
```

At `LOG_LEVEL=DEBUG` you also get the chat roster on every invocation, the exact
prompt sent to the model, the raw `usage` block, and a refused sender:

```
DEBUG cht_… roster: agent rel=self Willow (+15550100002); member role=owner Sam (+15550100001)
DEBUG inference request: model=anthropic/claude-sonnet-5 system="You tell very short…" user='tell me a story about a penguin'
DEBUG inference response: id=chatcmpl-… created=1791909592 usage={'prompt_tokens': 126, …}
INFO ignored cht_… from member rel=wife Anna (+14155550000): not the owner and not allow-listed
```

What the inference lines tell you:

- **`inference ok`** — the model Plow **actually returned** (not the id you asked
  for), `finish` (`stop` = complete; `length` = it hit `max_tokens` and the story
  was cut off), tokens `in/out/total`, `cached` prompt tokens, and the wall-clock
  latency of the request.
- **No cost figure.** The response carries tokens and cache counts but no price —
  Plow's dashboard owns spend. `cached` is the cost signal worth watching: a
  cached prompt is billed far cheaper than a cold one.
- **`inference reply`** — the **full text**, so a story is a multi-paragraph log
  entry. It legitimately appears twice: once as what the model said, once as
  `outbound`, which is what was sent after trimming.

Set how much it logs with `LOG_LEVEL` — `DEBUG`, `INFO` (default), `WARNING`, or
`ERROR`:

```sh
LOG_LEVEL=DEBUG docker compose up -d     # recreated with the new level
```

Running without Docker, it is just `LOG_LEVEL=DEBUG python3 agent.py`. Changing
the level needs the container recreated, which the `up -d` above does.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `login` prints a code, iMessage says "Plow activated!", terminal still waits | code/secret mismatch — you texted a code from an earlier run or the app. Run `login` once, text *that* code, don't restart. |
| `plow-agents: no account token at ~/.config/plow/token` | not logged in — run `plow-agents login` |
| `line:ln_x is in use by another account` | pick a `free` row from `plow-agents lines` |
| `plow-credentials already exists` | `plow-agents rotate` to replace, or `plow-agents revoke` to retire |
| logs show `chats: none yet` and nothing happens | normal until the first text; text the line and a chat is born |
| logs show `credential refused (401)` | the token is dead — `plow-agents rotate` (or re-mint) and recreate the container |
| `docker compose` / `docker buildx` unknown command | plugin symlinks missing — see the Homebrew block above, then `colima start` |
| `Cannot connect to the Docker daemon` | `colima start` |
| amd64 build is very slow | qemu emulation; expected. Give Colima more: `colima stop && colima start --cpu 4 --memory 4` |
| `denied` / `unauthorized` on `image push` | the token lacks `write:packages` — `gh auth refresh -s write:packages`, then log in to `ghcr.io` again |
| Plow's pull fails; agent never reaches `running` | the GHCR package is still private — set it **Public** (see GHCR above) |
| `deploy` rejects the image reference | deploy takes `repository@sha256:…`, not a `:tag` — paste the digest `image push` printed |

## Rebuild after editing `agent.py`

```sh
docker compose up --build -d      # rebuild and restart
docker compose down -v            # stop and remove the /data state volume
```

`down -v` wipes the checkpoint volume, so the next boot re-anchors instead of
trying to backfill.

## Teardown

```sh
plow-agents revoke                # retire this credential-file agent
docker compose down -v            # stop the container and drop its state volume
```

## Inference (the model)

The agent already uses **Plow's inference** — for story requests, and nothing
else. `respond()` routes first (echo / help / story) and only the story branch
calls the model, so the default reply stays free. This section is how that call
works, and where to point it instead.

### How the agent calls Plow inference

`Plow.complete()` in `plow.py` is the entire integration — one HTTP request,
OpenAI-shaped, on the credential the agent already holds. No SDK, no API key, no
provider config:

```
POST {PLOW_API_BASE}/v1/chat/completions
Authorization: Bearer <PLOW_AGENT_TOKEN>          # the agent's own credential (== AUTH)
{
  "model": "anthropic/claude-sonnet-5",           # PLOW_MODEL overrides this
  "max_tokens": 500,
  "messages": [
    {"role": "system", "content": <the story instructions>},
    {"role": "user",   "content": <the owner's text>}
  ]
}
-> {"choices": [{"message": {"content": "<the story>"}}], ...}
```

The reply is read from `choices[0].message.content` and goes back over the socket
as an ordinary message. It works from this agent's own token right now:

```sh
curl -sS "$PLOW_API_BASE/v1/chat/completions" \
  -H "Authorization: Bearer $PLOW_AGENT_TOKEN" -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"Reply with exactly: pong"}],"max_tokens":20}'
# -> {"model":"claude-sonnet-5", "choices":[{"message":{"content":"pong"}}], ...}
```

It is billed to your Plow account, and failures are mapped to a sentence rather
than swallowed: `402` = out of credits, `400` = the allowlist refused the model
id, `401` = terminal (the agent stops), anything else transient = one line back
to the chat. The model's output is **untrusted text** you are about to send to a
phone.

### Picking a model — the catalog

`PLOW_MODEL` chooses it (default `anthropic/claude-sonnet-5`):

```sh
PLOW_MODEL=z-ai/glm-5.2 docker compose up -d --build   # or set it in ./plow-credentials
```

Ids are **provider-prefixed** and drawn from a fixed allowlist — a bare or unknown
name is refused with `"Model '…' is not allowed"`:

| id | notes |
|---|---|
| `anthropic/claude-sonnet-5` | **the default**; 1M context |
| `anthropic/claude-opus-5`, `anthropic/claude-opus-4-8` | 1M |
| `anthropic/claude-sonnet-4-6` | 1M |
| `openai/gpt-6-astra`, `openai/gpt-6-sol`, `openai/gpt-6-luna`, `openai/gpt-5.6-luna` | 1.05M; accept files |
| `moonshotai/kimi-k3` | 1M; text/image/video |
| `moonshotai/kimi-k2.5` | 256k |
| `z-ai/glm-5.2` | 1M; text only |
| `z-ai/glm-5.3-flash` | 1.3M |
| `google/gemini-3.8-flash` | 1M; all modalities |

Two notes from a live check. The `model=` in the `inference ok` log is what Plow
**returned**, which is not always the id you sent — `anthropic/claude-sonnet-5`
comes back bare as `claude-sonnet-5`, while `z-ai/glm-5.2` and
`google/gemini-3.8-flash` come back prefixed. And `finish=length` means the model
hit `max_tokens` and was cut off — not that it finished.

Scope note: the **agent** token may call completions (`llm:chat`) but may **not**
list models — `GET /v1/models` answers `403 … llm:models`. Read the catalog live
with your account token (`~/.config/plow/token`), or use the table above.

### Adding more commands

Commands live in `commands.py`; which message reaches which one lives in
`router.py`. A "joke" command is two small additions:

```python
# commands.py — a command is `async def handler(plow, http, text) -> str`
async def joke(plow, http, text):
    return await plow.complete(http, model=config.MODEL,
                               system="Tell one short, clean joke.", user=text,
                               max_tokens=200)

# router.py — one row, above the echo catch-all
("joke", lambda t: t.lower().startswith("joke"), joke),
```

Nothing in `agent.py` changes. Whatever a command routes to `complete()` spends
inference; whatever it answers inline is free. And the guard still matters: a
stranger's text routed to the model is billed to you — see
[Who may invoke the agent](#who-may-invoke-the-agent).

### Option B — your own provider

Nothing forces you onto Plow's inference. This is your code: call Anthropic,
OpenAI, a local model, anything, with whatever key you like. Plow only requires
that the container *runs*; how it thinks is yours.
