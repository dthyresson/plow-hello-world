# Comparisons

Plow and Latch against three neighbours, across purpose, technology, UX and
users. Written from the public pages fetched **2026-10-13** and this repo's own
probing — so treat vendor feature claims as claims, and note that
`open-instinct` is v0.1 and describes itself as beta.

The useful frame is **which layer** each thing occupies, because none of these
are direct substitutes.

| | Inkbox | Maritime | Open Instinct | Plow + Latch |
|---|---|---|---|---|
| layer | identity & messaging | agent compute | an assembled application | platform + device access |
| shape | cloud service | cloud infra | MIT repo you run | hosted platform + Mac app |

---

## Latch vs Inkbox

Both are "an agent needs more than intelligence" products, and they are **the
two halves of one trust boundary, from opposite sides.**

- **Inkbox** gives an agent **reach into the world**: its own email inbox, phone
  and iMessage, an internet address (a tunnel), and an Agent2Agent endpoint — all
  under one identity. Email, texts and calls with the same person stay joined, so
  the agent keeps memory of *the person*, not the thread.
- **Latch** gives *the world's* agent **reach into your Mac**: files, commands,
  AppleScript, a real logged-in browser, and a vault it can use but not read.

| axis | Latch | Inkbox |
|---|---|---|
| **purpose** | let an agent *do* things on your machine, safely | let people and other agents *reach* your agent |
| **direction** | agent → your stuff (outbound capability) | world → agent (inbound reach) |
| **technology** | a Mac app: local MCP server, reached through Plow's relay | a cloud service: mailboxes, numbers, tunnel, A2A wire |
| **permission model** | per-request *intents* — approval, a capability-derived sandbox, an audit log, Gatekeeper as an optional AI reviewer | per-agent, per-channel *access control* — who may reach it on email / phone / iMessage / A2A |
| **the secrets** | a vault your agent may use and never read | the identity itself: the mailbox and number *are* the credential |
| **UX** | approval dialogs on your Mac; rules and audit in the app | a console (create an identity in seconds), a CLI/SDK/MCP/Skill |
| **who it's for** | an owner who wants their *own* machine used | a developer who needs their agent addressable |
| **where it runs** | your laptop; nothing listens publicly | their cloud; the Mac dials out |

**Overlap worth noting:** both end up as "agent identity + a policy in front of
capability", and both integrate with any harness. An agent can plausibly want
both — which is exactly what `open-instinct` does below: Inkbox for the phone
line, and something else for the computer. Plow pairs its own lines with Latch,
so it supplies the pair itself.

---

## Plow vs Maritime

Both host agents in VMs. They sell **different units**.

- **Maritime** sells **machines**: a Firecracker micro-VM per *end user*,
  on bare metal, with a persistent disk — new in 0.5s, and a sleeping one back in
  0.6s because it snapshots the guest to disk and maps the snapshot back on
  demand, so a parked agent costs only storage. Inside: a headful Linux desktop
  that opens a browser and clicks, which you can watch or take over. It also
  offers the same as **one MCP** ("a desktop per user id") so any MCP client —
  Cursor, Claude Code, your own agent — can drive a computer.
- **Plow** sells **a textable agent**: a phone line and an email line, a model
  proxy, the Latch path to *your* Mac, payments, and a marketplace that publishes
  agents to other people.

| axis | Plow | Maritime |
|---|---|---|
| **unit of tenancy** | one agent per phone line, for you | one VM per *your user* — "give each of your users their own VM, forever" |
| **the artifact** | your public image, pulled by digest | your image too (`--image ghcr.io/…`), or hosted "Computers" |
| **the "computer"** | *your* Mac, via Latch — the human's real machine | the agent's **own** VM with its own desktop |
| **isolation story** | credential scoping, grants, approval, sandbox | hardware-level: real kernel, micro-VM per user |
| **persistence** | a VM per agent; the docs describe warm-pool VMs holding the image in host cache | the headline: snapshot/restore, files + logins + packages survive sleep |
| **interface** | phone, email, dashboard, marketplace | dashboard, CLI, SDK, one MCP endpoint |
| **audience** | agent builders who want it textable, and end users installing one by text | developers building agent startups (their credits page says so) |
| **model** | Plow's own proxy, fixed allowlist | bring your own; the app above it defaults to Claude |

**The distinction:** Maritime is an **infrastructure layer you would build a
Plow-shaped product on**. In fact the repo below does exactly that — it deploys
onto Maritime. Plow is vertically integrated: it is the line, the inference, the
relay, and the store, bundled.

---

## Plow vs open-instinct

This is the closest comparison in *intent* and the furthest apart in *position in
the stack*.

`open-instinct` is **an application assembled from the layer**: a personal agent
you text on iMessage, with its own computer and a trusted network of other
agents. It is explicitly "built on Pi, Inkbox, Maritime and Composio", with a
Stripe Link wallet for payments, MIT-licensed, BYO-keys, nine packages, 837
tests, v0.1.

| axis | Plow (+Latch) | open-instinct |
|---|---|---|
| **purpose** | publish and run textable agents; reach a Mac safely | one person's own agent, running anywhere you like |
| **text UX** | phone line + email line, from a pool | iMessage via Inkbox; `instinct init/dev/chat` on a laptop first |
| **the computer** | your Mac (Latch) — the *human's* machine | the agent's own microVM desktop on Maritime |
| **tools** | Latch skills + connectors | Composio app router ("connect my Notion") |
| **approvals** | Latch intents + Gatekeeper; payment threshold and approval links | a code-level policy guard, desktop takeover for logins/2FA, Stripe Link one-tap card approval |
| **trust tiers** | owner / member / peer, plus per-chat trust | six rings — owner, partner, family, friend, contact, stranger — with plain-English grants |
| **payments** | Plow can release a banking credential under a threshold | the agent never holds the card: it asks for a single-use card for the exact amount, dead after |
| **agent-to-agent** | peer agents in a room; the relay | A2A over Inkbox, with per-tier rules |
| **openness** | hosted; your image, their platform | MIT, self-hostable, every key yours |
| **distribution** | Agent Index listings + signup phrases | a gateway that provisions one agent per person at signup |

**The distinction:** they tell the *same user story* — text a personal agent, it
has a computer, it can talk to your friends' agents — and sit at **opposite ends
of the stack**. Plow *is* the platform (lines, inference, Mac access, payments,
marketplace); open-instinct *uses* platforms and is the proof of what assembling
one costs: five external services and their keys, wired together.

Two differences that are philosophical rather than technical: their computer is
**the agent's own VM**, Plow's is **your actual Mac** — one isolates the agent
from you, the other gives it your context; and their six trust rings are a
richer, code-enforced social model than Plow's owner/member/peer + per-chat
trust.

---

## The stack, side by side

Same layers, two ways to get them:

| layer | the assembled way (`open-instinct`) | the bundled way (`Plow`) |
|---|---|---|
| identity & messaging | Inkbox | Plow's line pool (+ mailbox) |
| compute | Maritime micro-VM | Plow's VM per agent |
| capability / tools | Composio + the agent's own desktop | **Latch** (the owner's Mac) + connectors |
| loop | Pi | the `plow_chat` plugin, or your own container |
| model | bring your own (default Claude) | Plow's proxy and allowlist |
| payments | Stripe Link | Plow payments |
| distribution | their gateway | the Agent Index |

## What the comparison says about Plow's position

- **Vertically integrated, and that is the product.** The convenience is real:
  one login, one bill, a line, a model, a Mac path. The cost is that you take
  Plow's choices — its lines, its model allowlist, a Mac-only device path.
- **The novel piece is Latch.** Inkbox deliberately supplies identity, not
  capability; Maritime deliberately supplies a machine, not *your* machine.
  Latch is the only one of the three whose whole point is reaching the machine
  you already use, with a vault the agent can't read.
- **The assembled route buys control.** MIT, self-hosted, your keys, your model,
  your computer — at the price of wiring five services and owning their failure
  modes.
- **They compose more than they compete.** `open-instinct` already uses one of
  these services for each layer; a Plow-shaped product could be built on
  Maritime, and an Inkbox-identified agent could still want Latch.

## Sources

- <https://inkbox.ai/> (YC; identity: email, phone/iMessage, internet address, A2A)
- <https://maritime.sh/> (YC; micro-VMs per user, snapshot/restore, desktop, one MCP)
- <https://github.com/mariagorskikh/open-instinct> (MIT, v0.1; built on Pi, Inkbox, Maritime, Composio, Stripe Link)
- this repo: `docs/PLATFORM.md`, and the live probing behind it
