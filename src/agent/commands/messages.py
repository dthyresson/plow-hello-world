"""Messages: the last thing someone said, read from the owner's Mac.

Two Latch trips, in the order the Mac's own skills prescribe. **Contacts first**,
because the archive files every message under a phone number or an email and
never under a name -- so "mom" has to become a handle before Messages can be
asked anything. Then `plow-messages` for the newest message on each handle.

Nothing here is a Plow call. It is the owner's Mac, through the relay, with the
owner approving each request there.
"""
from __future__ import annotations

import json
import logging
import os
import re

from agent.lib.latch import Latch, LatchError

log = logging.getLogger(__name__)

# Who the message is about -- the person, from the way the owner asked.
LAST_FROM = re.compile(
    r"\b(?:the\s+)?(?:last|latest|recent)?\s*(?:messages?|texts?|msgs?|ims?)\s+"
    r"(?:from|with)\s+([^?.,!]+)", re.IGNORECASE)
WHAT_DID = re.compile(r"\bwhat\s+did\s+([^?.,!]+?)\s+(?:say|text|write|send)\b", re.IGNORECASE)
HEAR_FROM = re.compile(r"\b(?:hear|heard)\s+from\s+([^?.,!]+)", re.IGNORECASE)
REPLIED = re.compile(r"\b(?:did|has)\s+([^?.,!]+?)\s+(?:reply|replied|answer|answered|text)", re.IGNORECASE)

# The address book, as the contacts skill prints it: an absolute path, because a
# `~` in argv is not expanded on the exec path. The lookbehind and the space in
# the character class both matter -- the path runs under "Application Support",
# so a pattern that stops at whitespace matches "/AddressBook/AddressBook…" and
# then queries a directory that does not exist.
STORE_PATH = re.compile(r"(?<=[\s'\"`])(/[^'\"`\n]*?/AddressBook-v22\.abcddb)")

# Names live in their own columns; phones and emails hang off the record.
def _name_sql(person):
    """Contacts by name, matched on EQUALITY rather than LIKE.

    LIKE '%sam%' also matches "Samantha", so a stranger's phone numbers join
    the search and can crowd out the real ones. The merged clause is what lets a
    two-word name match a card whose first and last names are separate columns.
    """
    name = _quote(person)
    return (
        "select trim(coalesce(ZFIRSTNAME,'')||' '||coalesce(ZLASTNAME,'')) as name, "
        "p.ZFULLNUMBER as phone, e.ZADDRESS as email "
        "from ZABCDRECORD r "
        "left join ZABCDPHONENUMBER p on p.ZOWNER = r.Z_PK "
        "left join ZABCDEMAILADDRESS e on e.ZOWNER = r.Z_PK "
        f"where lower(coalesce(ZFIRSTNAME,'')) = {name} "
        f"or lower(coalesce(ZLASTNAME,'')) = {name} "
        f"or lower(coalesce(ZFIRSTNAME,'')||' '||coalesce(ZLASTNAME,'')) = {name}"
    )


# The address book is spread across several stores and every one is another
# approval on the Mac -- generous on purpose, because the card you want is often
# not in the first few. (Three was too few: it missed the store holding the
# person, and the command reported them absent.)
MAX_STORES = 8
MAX_HANDLES = 4    # each handle is another read of the archive

# Names resolved this process, so a person costs the Contacts sweep once rather
# than on every ask. Only non-empty results are kept, and a search that finds
# nothing drops the entry and resolves again -- a changed number should not
# become "no message history".
_RESOLVED = {}


def _person(text):
    """The person the message is about, or None if the ask was not about one."""
    for pattern in (LAST_FROM, WHAT_DID, HEAR_FROM, REPLIED):
        found = pattern.search(text)
        if found:
            return found.group(1).strip().strip("?.!, ").lower()
    return None


def matches(text):
    """Does this message ask what someone said?"""
    return _person(text) is not None


def _quote(value):
    """A SQL string literal -- an apostrophe is doubled, never left to end it."""
    return "'" + value.replace("'", "''") + "'"


def _handle(value):
    """A stored handle as the archive spells it.

    The archive keeps phones as `+<country><number>` while Contacts keeps them as
    typed, and the archive's match is literal: `--handle 15550100003` finds
    nothing where `--handle +15550100003` finds the thread (verified). A ten-digit
    number is taken to be missing its country code, the way Messages reads one on
    a US Mac. An email is passed through.
    """
    value = (value or "").strip()
    if not value or "@" in value:
        return value
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7:
        return value
    if len(digits) == 10:
        digits = "1" + digits
    return "+" + digits


def _output(payload):
    """A command's stdout, or a LatchError naming why it failed.

    A non-zero exit is the useful part -- "No such file or directory", "unable to
    open database" -- and swallowing it makes a failure look like an empty
    result, which is how this command first reported "no address book" when the
    real problem was a bad path.
    """
    if payload.get("exit_code"):
        detail = (payload.get("output") or "").strip().splitlines()
        raise LatchError(detail[-1][:200] if detail
                         else f"the command exited {payload['exit_code']}")
    return payload.get("output") or ""


def _lines(output):
    """Parsed records. `plow-messages` prints one JSON object per line, and
    sqlite is asked for JSON -- an array of rows, or nothing at all for none."""
    text = output.strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    except ValueError:
        records = []
        for line in text.splitlines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records


async def _handles(latch, http, person):
    """The phones and emails that person is reachable at, from Contacts."""
    skill = await latch.call(http, "plow_read_skill", {"name": "contacts"})
    match = STORE_PATH.search(skill.get("body") or "")
    if not match:
        raise LatchError("the contacts skill named no address book")
    root = os.path.dirname(match.group(1))

    found = await latch.call(http, "plow_run_command", {
        "argv": ["/usr/bin/find", root, "-maxdepth", "4", "-name", "AddressBook*.abcddb"],
        "read_paths": [root],
        "goal": f"Find the address books, to look up {person}",
    })
    stores = [line.strip() for line in _output(found).splitlines()
              if line.strip().endswith(".abcddb")][:MAX_STORES]
    if not stores:
        raise LatchError("no address book was found on the Mac")

    sql = _name_sql(person)
    handles = []
    for store in stores:
        # A store on another schema version answers with an error, not rows.
        out = await latch.call(http, "plow_run_command", {
            "argv": ["/usr/bin/sqlite3", "-readonly", "-json", store, sql],
            "read_paths": [root],
            "goal": f"Look up {person} in the owner's contacts",
        })
        for row in _lines(_output(out)):
            for value in (_handle(row.get("phone")), row.get("email")):
                if value and value not in handles:
                    handles.append(value)
        if handles:
            # One card carries every handle for a person, so the first store that
            # has the name is enough -- each further store is another approval on
            # the Mac. The trade-off: a person whose cards are split across two
            # sources (a second phone only in the other store) can miss a handle.
            break
    return handles[:MAX_HANDLES]


async def _newest(latch, http, person, handles):
    """The most recent message across every handle, in ONE read.

    `--handle` repeats, so all of the card's numbers go into a single search and
    `--limit 1` is the newest of them -- which is the question. (A search per
    handle was four round trips for the same answer.)
    """
    argv = ["plow-messages", "search"]
    for handle in handles:
        argv += ["--handle", handle]
    argv += ["--limit", "1"]
    out = await latch.call(http, "plow_run_command", {
        "argv": argv,
        "read_paths": ["~/Library/Messages"],
        "goal": f"Read the last message from {person}",
    })
    records = _lines(_output(out))
    return records[0] if records else None


async def run(plow, http, text):
    person = _person(text)
    latch = Latch(plow)
    try:
        cached = _RESOLVED.get(person)
        handles = cached or await _handles(latch, http, person)
        newest = await _newest(latch, http, person, handles) if handles else None
        if newest is None and cached:
            # The remembered card went stale (a changed number, a re-synced
            # store). One miss earns a fresh sweep before saying there is none.
            _RESOLVED.pop(person, None)
            handles = await _handles(latch, http, person)
            newest = await _newest(latch, http, person, handles) if handles else None
        if not handles:
            return f"I could not find {person} in your contacts."
        if newest is None:
            return f"There is no message history with {person}."
        _RESOLVED[person] = handles
        who = "you" if newest.get("is_from_me") else person
        return (f"Last message in your thread with {person} "
                f"({newest.get('at')}), from {who}:\n\n{newest.get('body') or '(no text)'}")
    except LatchError as exc:
        # LatchError is already a sentence, and it covers both "never got there"
        # and "your Mac said no" -- so do not prepend a claim about which.
        return f"I can't read your messages: {exc}."
