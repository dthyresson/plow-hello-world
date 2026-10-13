"""Latch: the owner's Mac, reached through Plow's relay.

Latch is a local MCP server on the Mac. The Mac dials out to Plow; the relay
authenticates this agent and forwards MCP calls to it. So this module is the
whole client -- one JSON-RPC `tools/call` over HTTP, on the credential the agent
already holds. No SDK, no second connection, no listening port on the Mac.

Three things about the shape are load-bearing, and all three come from
plow-pbc/hermes-plugin-plow, which is the reference implementation:

  * the relay is STATELESS -- there is no `initialize`, straight to the call;
  * a reply may be JSON **or** SSE (`text/event-stream`);
  * a tool that failed says so in `result.isError`, and a run the Mac has not
    decided yet comes back with a non-`completed` `status` rather than a value.

The URL comes from the agent's own identity (`GET /v1/agents/me` -> `mcp_url`);
absent means no Mac is connected, which is a sentence, not an error to retry.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


class LatchError(Exception):
    """Anything that stopped the call; str() is a sentence for the owner."""


def _payload(result):
    """A tool's answer: `structuredContent` when it supplies one, else the JSON
    in its first text block, else that block's text as `output`."""
    payload = result.get("structuredContent")
    if payload is not None:
        return payload
    for block in result.get("content") or []:
        if block.get("type") == "text":
            try:
                return json.loads(block["text"])
            except ValueError:
                return {"output": block["text"]}
    return {}


def _refusal(payload):
    """The most useful sentence a refusal carries.

    Devices answer in different shapes -- a diagnosis (a cause, sometimes a
    sentence meant for the owner), a plain error string, or the verdict's own
    fields -- so take the first that says something, and fall back to the payload
    itself rather than to a vague sentence.
    """
    diagnosis = payload.get("diagnosis") or {}
    for candidate in (diagnosis.get("cause"), payload.get("error"),
                      diagnosis.get("owner_action"), payload.get("detail")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return json.dumps(payload)[:300] or "your Mac refused it"


class Latch:
    """One client, holding the Plow client it borrows the URL and bearer from."""

    def __init__(self, plow):
        self.plow = plow

    @property
    def available(self) -> bool:
        """Is a Mac connected to this account? (No URL means no Mac.)"""
        return bool(self.plow.mcp_url)

    async def call(self, http, name: str, arguments: dict) -> dict:
        """One `tools/call`, decoded. Raises LatchError with something to say."""
        if not self.available:
            raise LatchError("no Mac is connected to this account")
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {**self.plow.auth, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        log.info("latch call: %s", name)
        try:
            # allow_redirects is off on purpose: this request carries the bearer,
            # so following a redirect would hand the credential to whatever the
            # redirect points at. A redirect becomes a parse failure instead.
            async with http.post(self.plow.mcp_url, json=body, headers=headers,
                                 allow_redirects=False) as resp:
                if resp.status >= 400:
                    raise LatchError(f"the relay answered HTTP {resp.status}")
                raw = await resp.text()
        except LatchError:
            raise
        except Exception as exc:
            log.warning("latch call failed: %s: %s", type(exc).__name__, exc)
            raise LatchError("could not reach the relay") from exc
        if raw.lstrip().startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
            raw = "\n".join(line[5:].strip() for line in raw.splitlines()
                            if line.startswith("data:"))
        try:
            result = json.loads(raw).get("result") or {}
        except ValueError as exc:
            raise LatchError("the relay's answer was not JSON") from exc
        payload = _payload(result)
        if result.get("isError"):
            raise LatchError(_refusal(payload))
        return payload
