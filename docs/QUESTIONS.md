# Open questions

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
