# Open questions

> **AI-written — verify before you rely on it.** This document, the agent code, and
> the research behind them were produced with a large language model. Where a claim
> was checked, the check is named nearby; where something was verified against a live
> system, it says so. Everything else is prose a model wrote, and mistakes are
> possible — so confirm anything you intend to depend on.

Design questions worth answering deliberately, before they get answered by
accident. Each entry is a **question**, the context that makes it live, the
**options**, and what would actually decide it. Not a decision — a place to keep
the thinking.

Add an entry rather than solving it in a commit message.

---

## Should the router be a model instead of text matching?

> Original note: *"if we might want to have a JEV system one like decision model
> as a command router instead of parsing text"*
> (the acronym is unfilled — replace it if it named something specific)

**Question.** Today routing is string matching: `COMMANDS` in
`src/agent/commands/__init__.py` is a table of `(name, matcher, handler)`, and
the first matcher that accepts the text wins. Should a *decision model* pick the
command instead — classify the message's intent with an LLM and dispatch on that
— rather than parsing text?

**Why it comes up.** The matchers are heuristics, and each one has been patched
at least once already:

- `"history"` contains `"story"` → story needs `\b` word boundaries.
- `"I have no time today"` needed a `NOT_ASKING` list to stop it matching time.
- `"a story about a time machine"` routes correctly only because `story` sits
  *above* `time` in the table.
- The trigger list for time is nine cues wide and would still miss phrasings a
  person would use without thinking.

So the growth curve is: every new command adds another regex to get right, and
ambiguity between commands is resolved by **table order** — policy that lives in
a list's ordering, not in anything you can read.

**Options.**

1. **Keep text matching (status quo).** Free, deterministic, and `route()` is a
   pure function — testable with no network. Cost: the parsing rules and the
   ordering subtleties keep accumulating.
2. **A model decides.** One inference call per message → a label
   (`story`/`time`/`echo`/…), then dispatch. Handles paraphrase, typos and
   wording you didn't anticipate, and keeps the table tiny. Cost: inference on
   *every* message (today most are free), latency added to every reply, and
   non-determinism — the same words can route differently.
3. **Hybrid, and this is the interesting one.** Keep the free exact matchers as
   the fast path, and call the model **only when nothing matched** — i.e. in the
   place `echo` sits now. Spending happens on the ambiguous tail instead of on
   everything.
4. **Model, but offline.** Use a model once to *author* the matchers (or a
   structured config), commit that, and keep runtime free. The text-matching
   problem is then a build-time problem.

**What would decide it.**

- **How often does a real message land on `echo` that should have been a
  command?** If that is rare, option 3 buys most of the benefit for almost no
  cost. If it is common, the matchers are the wrong shape and option 2 starts to
  earn its latency.
- **Is per-message latency acceptable?** A routed reply would wait on a model
  round trip before it could even start answering.
- **Does the guard run first?** Any model in the routing path is a spend surface
  a stranger's text can reach — `_authorized()` has to gate before it, not after.
- **Is determinism wanted?** Tests today assert `route(text) == ("time", …)`
  with no network. A model router is either untestable at that level or needs a
  recorded-fixtures story.

**Related.** `plow.Plow.complete()` is the call a model router would use, and
`commands/story.py` is already the shape of "a command that spends". The
`time` command's matcher — `TIME_WORD` + `CUES` + `NOT_ASKING` — is the clearest
example of how much hand-tuning text matching already needs.

---

## Should the reviewer run on a local model instead of sending the operation to Claude?

> Original note: *"since latch uses cloud inference it might be passing personal
> info to anthropic claude right? so what if you could run some on local models via
> lm studio or local models like Liquid AI"*

**Question.** Every Latch approval that gets *reviewed* posts a description of the
operation to Plow's chat-completion endpoint, which routes it to
`anthropic/claude-sonnet-5`. The operation is the agent's own attempt to use this
Mac — so it can name a contact, a path, a command line. Should the reviewer run on a
model that never leaves the machine — LM Studio, Ollama, MLX, Liquid AI's LFM2 —
instead of on Claude?

**What actually leaves the Mac.** This is not inferred: `DESIGN.md` §4 names it, and
it is four fields per review, no more.

| sent | looks like | built by |
|---|---|---|
| the agent's display name and id | `Family Coordinator`, `agentId` | `buildPrompt` |
| the request, composed on this Mac from the tool call | `last message from …`, `run: sips -Z 1600 …` | `buildPrompt` |
| the requested capability bounds — the sandbox that will be enforced | `fs.read /Users/x/Pictures`, `process.exec argv: [sips, -Z, 1600]` | `buildPrompt` |
| **the owner's own `agentPurpose` text** | *"manage my SSH keys and my mother's care"* | the **system** message |

Deliberately **not** sent: the agent's goal text, any audit history (`reviewPolicy.ts`
passes `history: []`), vault values, and the relay credential, which rides in the
`Authorization` header and nowhere else.

So the answer to the note is **yes, by design, and it is written down** — the request
and the capability bounds are the agent's own values, and the purpose statement is
the *owner's*. That is the price of having a reviewer at all: something outside the
Mac has to see the operation to judge it.

**Where it already does not happen.** Three paths spend no review — `deny` mode,
`approve` mode, and operations **confined to `~/Plow`**, granted with no reviewer and
no dialog. So "local" is not all-or-nothing; there is already a carve-out where the
gate is deterministic rather than a model call.

**Options.**

1. **Status quo.** The best judgment available, one bill, and the exposure is
   bounded and documented. Cost: two of the four fields are personal.
2. **Redact, don't relocate.** Keep the cloud reviewer but generalize identifiers
   before the call — phone numbers to `<phone>`, `/Users/<name>/…` to `<home>/…`,
   addresses to `<address>` — with the mapping held locally. The reviewer is told to
   judge what the operation *does*, and a phone-shaped token carries the same meaning
   as the digits do. Cheapest change to what leaves; needs a test proving the
   verdicts don't move.
3. **Local reviewer.** Latch already speaks OpenAI-shaped HTTP, and LM Studio serves
   `/v1/chat/completions` and `/v1/models` on localhost with structured output — so
   the transport swap is nearly free. The contract is not (below). Note the base URL
   is **baked into the build, never a setting**, so this is a build-level decision,
   not a preference.
4. **Tiered.** A local model for the low-stakes majority (reads, paths under home),
   the cloud reviewer only when the local verdict is unsure or the operation is
   destructive — the `~/Plow` carve-out turned into a policy instead of a special case.
5. **Local for the revision coach first.** The Gatekeeper revision coach sees the
   owner's purpose *and* the denied request, and its output is editable text the owner
   approves before it is saved. A weaker model is survivable there in a way it is not
   in the live gate, which makes it the honest place to start.

**Why a local reviewer is harder than it looks.**

- **`strict: true` is a security property, not formatting.** The verdict is
  constrained to a JSON schema, and in adversarial mode `ask` is *removed from the
  enum* — the code says removing it is the point, because mapping an `ask` afterwards
  would put the app back on the automatic-deny path it exists to close. A local
  runtime that only *suggests* schema compliance silently deletes that guarantee.
- **`thinking: {type: "adaptive"}` is Anthropic-shaped.** A `budget_tokens` variant
  comes back as a provider 400, and in adversarial mode a 400 is a **deny**. A
  per-provider request shape is therefore a correctness requirement, not polish.
- **A timeout is a deny.** The budget is 90s against a p90 of 16s on Sonnet. A local
  model sharing the Mac with the agent can be slower than that — and slower here does
  not mean "wait", it means refuse.
- **A weaker reviewer is not neutral.** It fails in one of two directions and both
  are bad: over-allow is the hole the reviewer exists to close; over-deny makes the
  agent useless. There is no close enough.
- **The on-device options are already here.** Apple's on-device foundation models and
  MLX need no install; Liquid AI's LFM2 is open-weight, edge-first, with day-one
  llama.cpp and MLX support, and their LFM2.5 DSpark drafters report roughly 2.3–3.1×
  faster decoding with MLX on Apple silicon. None of that answers the judgment
  question — it only answers whether a 1–3B reviewer is *fast enough*.

**What would decide it.**

- **Does the proxy retain what it forwards?** `DESIGN.md` records what leaves; it says
  nothing about what Plow or Anthropic keep. If prompts are retained, option 2 is
  worth more than option 3.
- **Does a candidate local model pass a regression suite?** The audit log is already
  the test oracle and the reviewer has already been measured with 20-run controls, so
  the rig exists to score a local model against recorded allow/deny cases. That number
  decides this, not a benchmark.
- **Does the local runtime enforce the schema?** If not, this is a change inside the
  reviewer, not a base-URL swap.
- **Can `adversarial` mode survive without a Plow account?** Today no credential means
  no reviewer, and adversarial mode denies every request (`DENIAL_SOURCE_NO_REVIEWER`).
  A local reviewer would decouple the gate from the bill — a real argument for option 3
  that has nothing to do with privacy.
- **Does the threat model change?** Not if the reviewer stays a separate process the
  agent cannot write to and the local endpoint stays unreachable from the agent's
  container.

**Related.** The agent's own inference has the same shape — `Plow.complete()` posts the
owner's message text to the same endpoint — so a local reviewer closes only half the
door. `docs/PLATFORM.md` describes the proxy; `LIFECYCLE.md` covers what runs where.

---

## Should the platform expose a typing indicator, so the agent can drop the acknowledgement bubble?

> Original note: *"something i noticed is that the roundtrip from message to agent and
> even to latch and back there is a delay and no feedback. imessage shows ... dots to
> show if someone is replying. is that possible? if not should there be if this is
> taking longer than a .75 second or so some acknowledge that stuff is going on othewise
> feels slow and not sure if working"*

**Question.** iMessage shows a "…" bubble while the other side composes, and every
human reads it as "they are working". A Plow agent cannot ask for one: it never speaks
iMessage, and its only outbound primitive (`POST /v1/chats/{uid}/messages`) is a whole
message — with no edit or delete. So the agent now approximates the dots with a real
message: a turn still running after `PLOW_ACK_DELAY` (0.75s) sends `PLOW_ACK_TEXT`
(`On it…`) before its reply, and that bubble is permanent
(`runtime.Agent._respond_with_ack`). Should the platform expose a typing indicator
instead, so the agent can show progress without leaving a message behind?

**Context.** The bubble belongs to the *sending device*, so only Plow — which owns the
line and its sending Mac — can emit one; the same is true of read receipts and tapbacks.
That is the same boundary that makes the dots impossible from the agent and possible in
principle from Plow, which is why this is a platform question, not an agent one. The
current stand-in is cheap and needs no contract change, but it costs a second bubble the
owner cannot retract, and it fires only *after* the delay — a real indicator would start
before it.

**Options.**

1. **Keep the acknowledgement bubble (status quo).** No platform change; the agent stays
   self-contained. Cost: a permanent extra message, and a poor fit for group chats, where
   it reads as noise rather than reassurance.
2. **A typing endpoint.** e.g. `POST /v1/chats/{uid}/typing` (start/stop, or a short TTL),
   so the agent — or the platform on its behalf — drives the real indicator. Cost: a new
   wire primitive to design, and the pool line's device must emit it per call.
3. **Platform-side, automatic.** Plow shows typing whenever it is waiting on an agent for
   a turn (it already knows a message was delivered and none came back). No agent change
   at all. Cost: it must not fire for turns the agent ignores (an unauthorized sender, an
   empty body), so the trigger has to duplicate the agent's own guard.
4. **A replaceable acknowledgement in the contract.** Let a send declare itself a
   *provisional* reply the platform may supersede when the real one arrives, so the bubble
   does not pile up. Cost: edit/delete semantics on a channel that has none, against a
   real phone.

**What would decide it.**

- **Is the win only perceived latency once the frame loop is concurrent?** Today a slow
  turn also delays the *next* message, so an ack helps twice; fix the loop and the ack is
  purely about perception, which makes option 3 the cheap choice.
- **What does a pool line cost per indicator?** If every typing event is a send through
  the shared device, the platform carries that load for every agent — a reason to prefer
  option 3 over 2.
- **Do group chats need it?** They are where the permanent-bubble cost is highest; if the
  answer is no, the indicator can be DM-scoped and the trade shrinks.

**Related.** `runtime._respond_with_ack` and `config.ACK_DELAY`/`ACK_TEXT` are the current
stand-in; `docs/HOW-IT-WORKS.md` ("the acknowledgement bubble") says why. The frame loop's
sequentiality — the other half of "feels slow" — is `Agent.session()`.
