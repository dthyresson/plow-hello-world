# Development lifecycle for a Plow agent

How a change to the agent code travels from your laptop to a live agent people text
on Plow — and specifically, **what happens to the running "prod" agent when you
change the code**. (Short answer: nothing, until you redeploy. Why is below.)

## The mental model

There are two axes, and only one of them is about *where the container runs*:

1. **Where the container runs** — your Mac (Compose under Colima), or Plow's VMs.
2. **Which Plow line it serves** — the phone number it answers on.

The part people get wrong: **running locally is not a sandbox.** The same image
talks to the real Plow API with a real line credential and answers a real phone
line. So your environments are separated by **which line** an agent holds, not by
"local versus cloud".

You have a pool of lines (`plow-agents lines`), and **a line holds one agent at a
time**. Give each stage its own line:

```
                      one immutable image (by digest)
                                 │
      dev ───────────────────────┼────────────── tst ────────────── prod
   (your Mac)                    │             (Plow)              (Plow)
   line ln_…dev                             line ln_…tst        line ln_…prod
   fast loop, real line                     hosted, amd64       the real one
```

| stage | runs on | purpose | answers to |
|---|---|---|---|
| **dev** | your Mac | iterate fast on code | your own texts on the dev line |
| **tst** | Plow | prove the *hosted* image boots and answers | your own texts on the test line |
| **prod** | Plow | the line real people use | everyone |

The codename of the game: build **one** image, and move *that same digest* up the
chain. Do not rebuild between test and prod — the bytes you tested are the bytes
you ship.

## Stage 1 — dev (local loop)

Edit, rebuild, text, read logs. All local, all fast.

```sh
# once: claim a dev line and write ./plow-credentials
plow-agents lines                       # note a free ln_…dev
plow-agents deploy --local --line ln_…dev

# the loop, every change
#   ...edit src/agent/...
docker compose up -d --build            # rebuild + recreate (a plain `restart` keeps the OLD image)
docker compose logs -f                  # watch it boot and answer
```

Text the dev line; you should see `inbound …` then `outbound …`. Faster still,
skip Docker while iterating on pure logic:

```sh
set -a; . ./plow-credentials; set +a
PYTHONPATH=src AGENT_STATE_DIR=./plow-state python3 -m agent
```

Checklist before promoting: it boots, it answers, your `LOG_LEVEL` is sane, and
`docker compose down -v` leaves no surprise state.

## Stage 2 — tst (prove the hosted image)

Local runs arm64 on your Mac; Plow runs **amd64** and provisions it from the
registry. Those are different enough to be worth one real deploy before you touch
the prod line — this stage is what catches "it worked on my machine".

```sh
plow-agents image build ghcr.io/YOUR_ACCOUNT/hello-world-agent:v1   # builds --platform linux/amd64
gh auth token | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
plow-agents image push  ghcr.io/YOUR_ACCOUNT/hello-world-agent:v1   # prints repository@sha256:…
# make the package Public in GHCR (first time only)

plow-agents deploy ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:… --line ln_…tst
plow-agents agents                      # poll until STATUS is running
```

Text the test line. If it answers, the *image* is good — not just your checkout.

## Stage 3 — prod (the line people text)

Same digest, different line:

```sh
plow-agents deploy ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:… --line ln_…prod
plow-agents agents
```

Nothing about the artifact changes here. "Promotion" is just pointing a different
line at the same bytes.

## Updating prod after a code change

**Yes — you redeploy. An edit alone does nothing, and a push to GHCR does nothing
either.** That is the whole trick, and it is easy to be surprised by:

- Plow records the **digest** it was told to run when it provisioned the agent.
- A running agent keeps that image. Pushing a new tag or new bytes to GHCR does
  **not** reach it — Plow is not polling the registry.
- The only thing that changes what prod runs is issuing a **new deploy** with the
  new digest on that line.

So the update loop is:

```sh
# 1. change and test locally (stage 1)
# 2. build + push a NEW digest
plow-agents image build ghcr.io/YOUR_ACCOUNT/hello-world-agent:v2
plow-agents image push  ghcr.io/YOUR_ACCOUNT/hello-world-agent:v2   # note the new sha256:…
# 3. point prod at the new digest
plow-agents revoke ln_…prod                                        # retire the agent currently on the line
plow-agents deploy ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:… --line ln_…prod
plow-agents agents                                                 # poll until running
```

Tag naming is your choice; what Plow respects is the **digest**. Tagging by the
git commit is the habit that keeps this legible later.

> The `revoke` step retires whatever agent currently holds the line, which is how
> you free it for the replacement. Confirm the state with `plow-agents agents`
> (and `plow-agents lines`, which shows the line's `agent_uid`) between steps.

### Rollback

Because every version is still an immutable digest in GHCR, rollback is the same
operation with an older digest — no rebuild:

```sh
plow-agents revoke ln_…prod
plow-agents deploy ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:<previous> --line ln_…prod
```

Keep the digests you ship. A tag like `v1` can be repointed at different bytes;
the digest cannot.

## What each stage changes

| action | affects dev | affects tst | affects prod |
|---|---|---|---|
| edit the agent code | no (until rebuild) | no | no |
| `docker compose up -d --build` | **yes** | no | no |
| `image build` + `image push` | no | no | no |
| `deploy … --line ln_…tst` | no | **yes** | no |
| `deploy … --line ln_…prod` | no | no | **yes** |

The two "no"s in every row are the point: the stages are independent, and only a
`deploy` against a given line moves that line.

## Automating it (optional)

A CI job (GitHub Actions) can do stage 2 for you: on a tag or merge, build
`linux/amd64`, push to GHCR, and deploy the digest to the test line. Two things to
know before you reach for it:

- **Actions uses GitHub compute minutes** (unlike GHCR, which is storage). Building
  amd64 under emulation is the slow part.
- It needs a repository secret with `write:packages`, and (to deploy) your Plow
  account token — treat it as a real secret.

None of this is required; the manual loop above is the floor.

## Your own lines vs publishing an image

Everything above is about agents **you** run on **your** lines. There is a second,
different flow for distributing an agent to other people: publishing it to Plow
as an *agent image* and having new agents boot it.

```sh
plow-agents image set my-agent --name "…" --blurb "…" --repo https://github.com/YOU/repo
plow-agents image promote my-agent ghcr.io/YOUR_ACCOUNT/hello-world-agent@sha256:…   # admits/updates the pin
```

The important difference: **promotion affects new provisionings; running agents
keep their images.** So it, too, is "digest pinning" — you choose when the world
moves, and moving it never mutates an agent that is already up.
