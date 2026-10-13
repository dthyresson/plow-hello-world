"""Every setting this agent reads from the environment, in one place.

Nothing here does work -- it is the answer to "where does that value come from".
"""
from __future__ import annotations

import os
import pathlib

# --- from the credential file that `plow-agents mint` writes -----------------
BASE = os.environ.get("PLOW_API_BASE", "https://api.plow.co").rstrip("/")
TOKEN = os.environ.get("PLOW_AGENT_TOKEN", "")

# --- optional ----------------------------------------------------------------
# Home chat for cron/default output; unset means derive it (see agent._solo_dm).
HOME_CHANNEL = os.environ.get("PLOW_HOME_CHANNEL") or None
# Which model inference uses. Any id from Plow's allowlist; see the README.
MODEL = os.environ.get("PLOW_MODEL", "anthropic/claude-sonnet-5")
# Handles (phone numbers or emails) besides the owner that may invoke the agent.
ALLOWED_HANDLES = {
    handle.strip().lower()
    for handle in os.environ.get("PLOW_ALLOWED_HANDLES", "").split(",")
    if handle.strip()
}
# DEBUG, INFO, WARNING or ERROR (read in agent.__main__).
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
# Where the per-chat checkpoint lives. compose.yml sets /data (a volume); a bare
# `python agent.py` run defaults to ./plow-state.
STATE_DIR = pathlib.Path(os.environ.get("AGENT_STATE_DIR", "./plow-state"))
CHECKPOINTS = STATE_DIR / "last_uids.json"

# --- connection tuning (matches the Hermes plugin) ---------------------------
# After a session ends, retry immediately once, then back off 30s doubling to a
# 5-minute cap. The curve resets only for a session that stayed up long enough to
# count as healthy -- see agent.Agent.run.
RECONNECT_BASE = 30.0        # seconds before backed-off retry #1
RECONNECT_CAP = 300.0        # ceiling for the doubling curve
RECONNECT_JITTER = 0.25      # spread the immediate first retry over this long
HEALTHY_SESSION_SECONDS = 30.0
HEARTBEAT_SECONDS = 30       # websocket ping interval
BACKFILL_LIMIT = 50          # messages read back per chat after a reconnect
SEEN_MAX = 512               # dedupe window for message/event ids
