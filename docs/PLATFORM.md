# What Plow is

An orientation for someone who has built one agent and wants to know what the
platform around it actually is. Written from what this repo's agent used, what
the live API answered, and what the sibling repos document — not from marketing.

**Plow is a hosted agent platform where the agent is *your* container and the
interface is a phone line (and an email line) — with three things attached that
you do not build: a relay that gives that container safe reach into your Mac, a
model proxy both sides think through, and a registry plus dashboard that publish
agents to other people and bill the thinking.**

## The parts you don't build

**The relay, and Latch behind it.** `GET /v1/agents/me` hands an agent an
`mcp_url`. That is the relay: your **Mac dials out** to it, and it authenticates
the agent and forwards MCP calls to the Latch app running there. Nothing on the
Mac listens publicly, and the agent's side is one authenticated HTTP endpoint —
about sixty lines. Latch is the Mac half: a local MCP server in front of tools
(files, commands, AppleScript, a browser, the vault), gated by intents, approval
and a sandbox. **Gatekeeper**, its reviewer, is a hosted model call in its own
right — `anthropic/claude-sonnet-5` through Plow's own proxy, billed to the same
account, shown one operation and no history.

**Inference.** `POST /v1/chat/completions` is OpenAI-shaped, takes the credential
the agent already holds, and is billed to the account — no provider API key
anywhere. The ids are provider-prefixed and drawn from a fixed allowlist (13 of
them; `anthropic/claude-sonnet-5` is the default). The **agent** token carries
`llm:chat` but not `llm:models`, so it can think but cannot enumerate the catalog.

**Registry and dashboard.** Plow **runs** your image but does not **host** it —
and that split is the whole deployment model. The bytes live in a **public**
registry you own (GHCR, Docker Hub, Quay), which Plow pulls anonymously and **by
digest**, because a tag names different bytes next week. The running agent is
Plow's: it provisions the VM, boots your container and serves the line, and it
keeps only the **pin** (the `repo@sha256:…` reference, in its `agent_images`
catalog) — never the bytes. So the consequences follow mechanically: make the
package private and the pull fails, so no agent; push to the registry and nothing
deploys; promote a digest and *new* provisioning moves while running agents keep
the image they booted. The dashboard (`app.plow.co`) is the control plane:
credits and usage, lines, group trust, the payment limit, verbose output, the
Latch link.

## What you bring

The image, and everything above the contract: the reasoning loop, the command
routing, the guard that decides who may invoke it, the skills it reads, and the
policy you want where no platform policy reaches. This repo is a small, complete
example — `src/agent/`, five modules, no framework.

Worth knowing: **the default image is itself a Hermes agent.** `plow-hermes-agent`
is the Hermes runtime under s6-overlay with the `plow_chat` plugin pinned, so the
loop — sessions, tools, memory, skills, sub-agents — is borrowed, and the line, the
relay, the inference bill, the approvals, the payments and the catalog are the
product. That is the whole shape of Plow in one sentence.

## Easy to miss

| part | what it is |
|---|---|
| **Lines** | A shared pool of real phone numbers, each holding one agent. Any account can see them, so **"who can text it" is not a secret** — reachability is a permission you add. |
| **Email lines** | An agent gets its own mailbox beside its phone line (`<line-name>@plow.co`, for the line this repo was built on). |
| **Groups, trust, peers** | Threads carry a roster with `role` and `relationship`, per-chat trust (discretion vs full trust), and other Plow agents as participants — so rooms are multi-party, not DMs. |
| **Payments** | The credential carries `payments:request`, and the agent has a `payment_approval_threshold_usd` (100 by default): a path where Plow releases a banking credential at or below the threshold and sends a single-use approval link above it. |
| **Connectors** | The minted credential's scopes include `connectors:use`, `gmail:connect` and `slack:connect` — so cloud integrations sit *beside* Latch's Mac access, not inside it. |
| **The Agent Index** | Public listings for agents, installed by texting a signup phrase (`Set this up for me: aiworthusing.com/agent-index/<slug>`), with digest promotion and usage reporting to a leaderboard. |

## Latch, in one line

The inverse shape of the rest: **a local MCP server that hands an agent
capability without handing it your credentials** — the vault is encrypted on the
Mac and is *used* on the agent's behalf, never read by it. Policy in front of
tools, rather than tools with policy bolted on.

## Where the pieces live

| repo | owns |
|---|---|
| `plow-pbc/plow-agents` | the developer CLI: login, lines, mint, rotate, revoke, image, deploy |
| `plow-pbc/plow-hermes-agent` | the base image: boot, `plow-init`, the plugin pin (and the repo map) |
| `plow-pbc/hermes-plugin-plow` | the `plow_chat` plugin — the reference implementation of the chat contract |
| `plow-pbc/latch` | the Mac app: the MCP tools, the approval path, the vault |

This repo's own docs go deeper on the parts it exercises: `HOW-IT-WORKS.md` for
the code, `LIFECYCLE.md` for dev → prod, `QUESTIONS.md` for what is still open.
