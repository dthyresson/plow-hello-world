"""The Plow HTTP contract, in one place.

Every call this agent makes to Plow lives here: who it is, which chats it may
serve, the WebSocket ticket, history, sending a reply, and inference. Nothing in
this file knows about commands or routing -- it is the wire, so reading it top to
bottom is reading the API the agent speaks.

The shapes match plow-pbc/hermes-plugin-plow, the Hermes implementation of the
same contract.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger("agent")


class CredentialRefused(Exception):
    """A 401: the token itself was rejected.

    Terminal, not retryable -- every retry would present the same dead token. A
    403 is different: it is resource-scoped (removed from one chat, say) and is
    retried like any other error.
    """


class ModelUnavailable(Exception):
    """Inference could not be had; str() is a sentence for the owner."""


def check(resp):
    """The one status seam on an authed call: 401 stops, others raise."""
    if resp.status == 401:
        raise CredentialRefused
    resp.raise_for_status()


class Plow:
    """The client. One instance holds the base URL and the bearer."""

    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    @property
    def auth(self) -> dict:
        """One header for every call that presents the credential."""
        return {"Authorization": "Bearer " + self.token}

    def ws_url(self, ticket: str) -> str:
        """The socket URL for a minted ticket; https -> wss mirrors http -> ws."""
        return f"{self.base.replace('http', 'ws', 1)}/v1/ws?ticket={ticket}"

    # --- reads ---------------------------------------------------------------

    async def identify(self, http):
        """GET /v1/agents/me -- log who this agent is. Best effort.

        A 404 is the documented "this token is not one agent" (a wildcard or
        multi-line grant); it answers no identity but is not a failure, so it is
        swallowed. Anything else goes through the 401 seam.
        """
        async with http.get(f"{self.base}/v1/agents/me", headers=self.auth) as resp:
            if resp.status == 200:
                me = await resp.json(content_type=None)
                log.info("identity: agent=%s line=%s", (me.get("agent") or {}).get("uid"),
                         (me.get("line") or {}).get("provider_key"))
            elif resp.status != 404:
                check(resp)

    async def chats(self, http) -> dict:
        """GET /v1/chats -- the granted chats, as {uid: chat}.

        The grant *is* the access control: there is no per-chat config. A
        truncated listing is an error rather than a partial cache, because
        serving half a grant silently is worse than failing.
        """
        async with http.get(f"{self.base}/v1/chats", headers=self.auth) as resp:
            check(resp)
            body = await resp.json(content_type=None)
        if body.get("has_more"):
            raise RuntimeError("the granted chat listing is truncated")
        return {c["uid"]: c for c in body.get("data", []) if c.get("status") == "active"}

    async def ticket(self, http) -> str:
        """POST /v1/ws/ticket -- single-use, 60s.

        Minted immediately before dialing on purpose: a cached one would be
        rejected at consume.
        """
        async with http.post(f"{self.base}/v1/ws/ticket", json={}, headers=self.auth) as resp:
            check(resp)
            return (await resp.json(content_type=None))["ticket"]

    async def history(self, http, chat_uid: str, limit: int) -> list:
        """GET /v1/chats/{uid}/messages -- one page, newest first."""
        async with http.get(f"{self.base}/v1/chats/{chat_uid}/messages",
                            params={"limit": limit}, headers=self.auth) as resp:
            check(resp)
            return (await resp.json(content_type=None)).get("data") or []

    # --- writes --------------------------------------------------------------

    async def send(self, http, chat_uid: str, body: str) -> bool:
        """POST /v1/chats/{uid}/messages -- post a reply into one chat.

        A 408/424/5xx is reported as *delivery unknown*, not failure: the POST
        may have been accepted before the error surfaced, and blindly resending
        is how you double-text a real phone. Anything else non-2xx raises.
        """
        async with http.post(f"{self.base}/v1/chats/{chat_uid}/messages",
                             json={"body": body}, headers=self.auth) as resp:
            if resp.status in (408, 424) or resp.status >= 500:
                log.warning("send to %s: HTTP %s (delivery unknown, not resending)",
                            chat_uid, resp.status)
                return False
            check(resp)
            log.info("outbound %s: %r", chat_uid, body)
            return True

    # --- inference -----------------------------------------------------------

    async def complete(self, http, *, model: str, system: str, user: str,
                       max_tokens: int) -> str:
        """POST /v1/chat/completions -- OpenAI-shaped, on the same bearer.

        No SDK, no API key, no provider config: the request is what any OpenAI
        client would send, and the reply is read from choices[0].message.content.
        Billed to the Plow account.

        Raises ModelUnavailable with a sentence the owner can act on -- 402 is an
        empty balance, 400 is a model id the allowlist refuses -- rather than
        swallowing the failure. A 401 is still terminal and propagates.
        """
        log.debug("inference request: model=%s system=%r user=%r", model, system, user)
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        started = time.monotonic()
        try:
            async with http.post(f"{self.base}/v1/chat/completions", json=payload,
                                 headers={**self.auth, "Content-Type": "application/json"}) as resp:
                if resp.status == 402:
                    raise ModelUnavailable("the Plow account is out of credits")
                if resp.status == 400:
                    raise ModelUnavailable(f"Plow refused the model {model!r}")
                check(resp)
                body = await resp.json(content_type=None)
        except (CredentialRefused, ModelUnavailable):
            raise
        except Exception as exc:
            log.warning("inference failed after %.0fms: %s: %s",
                        (time.monotonic() - started) * 1000, type(exc).__name__, exc)
            raise ModelUnavailable("the model request failed") from exc
        elapsed_ms = (time.monotonic() - started) * 1000
        choices = body.get("choices") or []
        choice = (choices[0] if choices else {}) or {}
        text = ((choice.get("message") or {}).get("content") or "").strip()
        self._log_inference(body, choice, text, elapsed_ms)
        return text

    @staticmethod
    def _log_inference(body, choice, text, elapsed_ms):
        """What the call cost and how fast, then the reply itself.

        Tokens and cache counts come from the response's `usage`; latency is
        measured around the request. There is **no cost field** in the response
        -- Plow's dashboard owns spend -- so the useful cost signal is the token
        counts, `cached` in particular: a cached prompt is billed far cheaper
        than a cold one.
        """
        usage = body.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        log.info("inference ok: model=%s finish=%s tokens in/out/total=%s/%s/%s cached=%s in %.0fms",
                 body.get("model"), choice.get("finish_reason"),
                 usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 usage.get("total_tokens"), cached, elapsed_ms)
        log.debug("inference response: id=%s created=%s usage=%r",
                  body.get("id"), body.get("created"), usage)
        log.info("inference reply (%d chars): %s", len(text), text)
