"""Shared helpers: accounts, a rule-based stand-in for the LLM, and a World wrapper."""
from __future__ import annotations

import datetime as dt
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "sim"))
from harness import Chain, Reverted, iso  # noqa: E402
from genlayer import Address  # noqa: E402

HOUR = 3600
DAY = 86400
URL = "https://blog.example.org/feed"
URL2 = "https://mirror.example.net/archive"
TITLE = "Publish one garden post every day"
COMMITMENT = "I will publish at least one new dated garden post on the feed during every checkpoint window."

# 2027-01-15 00:00:00 UTC; tests start at 08:00 that day
MIDNIGHT = 1_799_971_200
T0 = MIDNIGHT + 8 * HOUR

_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2}))?")


def stamp_to_ts(text: str) -> int:
    m = _STAMP.search(text)
    y, mo, d, h, mi = (int(x) if x else 0 for x in m.groups())
    return int(dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone.utc).timestamp())


def acct(n: int) -> Address:
    return Address(bytes([0x10 + n]) * 20)


def entry(ts: int, text: str = "Posted a new garden article about tomato care") -> str:
    return f"{dt.datetime.fromtimestamp(ts, dt.timezone.utc):%Y-%m-%d %H:%M} {text}"


def feed(*entries: str) -> str:
    return " ## ".join(entries)


def honest_llm(prompt: str) -> dict:
    """Behaves like a careful model: returns the page entries whose timestamp lies in
    the checkpoint window, each with its date. It parses the prompt the contract
    really builds."""
    window = re.search(r"CHECKPOINT WINDOW \(UTC\): from (.+?) until (.+?)\n", prompt)
    lo, hi = stamp_to_ts(window.group(1)), stamp_to_ts(window.group(2))
    want = int(re.search(r"at most (\d+) entries", prompt).group(1))
    body = prompt.split("<<<EVIDENCE>>>\n", 1)[1].split("\n<<<END EVIDENCE>>>", 1)[0]
    entries = []
    for part in body.split(" ## "):
        part = part.strip()
        m = _STAMP.match(part)
        if m and lo <= stamp_to_ts(part) < hi:
            entries.append({"quote": part, "date": m.group(0)[:10]})
    return {"entries": entries[:want], "note": f"found {len(entries)} entries"}


class World:
    """A chain with named accounts, convenient actions and shared page/model setup."""

    def __init__(self, nodes: int = 3, rollback: bool = True) -> None:
        self.chain = Chain(nodes=nodes, rollback=rollback, start=T0)
        self.chain.deploy()
        self.c = self.chain
        self.alice, self.bob, self.carol, self.dave = acct(1), acct(2), acct(3), acct(4)
        self.last_events = []
        self.set_model(honest_llm)
        self.set_page(feed(entry(MIDNIGHT - DAY, "Old post from before the pledge started")))

    # ---- environment ------------------------------------------------
    def set_page(self, text, url: str = URL) -> None:
        for node in self.c.nodes:
            node.pages[url] = text

    def drop_page(self, url: str = URL) -> None:
        for node in self.c.nodes:
            node.pages.pop(url, None)

    def set_model(self, fn) -> None:
        for node in self.c.nodes:
            node.llm = fn

    @property
    def now(self) -> int:
        return self.c.now

    def warp(self, ts: int) -> None:
        self.c.warp_to(ts)

    def advance(self, seconds: int) -> None:
        self.c.advance(seconds)

    # ---- actions ----------------------------------------------------
    def send(self, who, name, *args):
        self.last_events = []
        result = self.c.send(who, name, *args)
        self.last_events = self.c.events
        return result

    def view(self, name, *args):
        return self.c.view(name, *args)

    def claim(self, *whos):
        for who in whos:
            self.send(who, "claim_starter_points")

    def open(self, who=None, *, title=TITLE, commitment=COMMITMENT, url=URL, start_in=16 * HOUR,
             interval=DAY, checkpoints=3, min_met=2, min_items=1, stake=100) -> int:
        who = who or self.alice
        start_at = self.now + start_in
        return int(self.send(who, "open_pledge", title, commitment, url, start_at, interval,
                             checkpoints, min_met, min_items, stake))

    def pledge(self, pid: int):
        return self.view("get_pledge", pid)

    def standing(self, who) -> dict:
        return dict(self.view("get_standing", who.as_hex))

    def balance(self, who) -> int:
        return int(self.standing(who)["balance"])

    def window(self, pid: int, index: int):
        p = self.pledge(pid)
        start = int(p.start_at) + index * int(p.interval_seconds)
        return start, start + int(p.interval_seconds)

    def opens(self, pid: int, index: int) -> int:
        row = self.view("get_schedule", pid)["checkpoints"][index]
        return int(row["verify_opens_at"])

    def to_verify(self, pid: int, index: int) -> None:
        self.warp(max(self.now, self.opens(pid, index)))

    def feed_for(self, pid: int, indexes, extra=()):
        """A feed with one entry inside each listed window (plus optional extras)."""
        entries = []
        for i in indexes:
            start, end = self.window(pid, i)
            entries.append(entry(start + (end - start) // 2, f"Posted garden update number {i + 1} about soil"))
        entries.extend(extra)
        entries.append(entry(MIDNIGHT - DAY, "Old post from before the pledge started"))
        return feed(*entries)

    def verify(self, pid: int, who=None):
        return int(self.send(who or self.bob, "verify_checkpoint", pid))


def invariant_check(world: World) -> None:
    """Conservation of points and consistency of every counter, recomputed by brute force."""
    state = world.c.state()
    stats = world.view("get_stats")
    profiles = state["profiles"]
    free = sum(p["balance"] for p in profiles.values())
    locked = sum(p["locked"] for p in profiles.values())
    assert free + locked + stats["points_burned"] == stats["points_granted"], "points are not conserved"
    assert locked == stats["points_locked"], "locked_total drifted"
    expected_locked = 0
    open_by_owner: dict = {}
    for p in state["pledges"].values():
        if p["status"] in ("ACTIVE", "BREACHED"):
            expected_locked += p["stake"]
            open_by_owner[p["owner"]] = open_by_owner.get(p["owner"], 0) + 1
    for key, s in state["sponsorships"].items():
        pledge = state["pledges"][s["pledge_id"]] if s["pledge_id"] in state["pledges"] else None
        if pledge and pledge["status"] in ("ACTIVE", "BREACHED"):
            expected_locked += s["amount"]
            assert s["outcome"] == "LOCKED"
        else:
            assert s["outcome"] != "LOCKED"
    assert expected_locked == locked, f"locked points {locked} != open stakes+sponsorships {expected_locked}"
    for p in state["pledges"].values():
        assert p["met"] + p["missed"] == p["checked"], "met+missed != checked"
        assert p["met"] <= p["checkpoints"] and p["checked"] <= p["checkpoints"]
        assert p["sponsor_pool"] == sum(
            s["amount"] for s in state["sponsorships"].values() if s["pledge_id"] == p["id"]
        )
        assert p["sponsor_count"] == sum(1 for s in state["sponsorships"].values() if s["pledge_id"] == p["id"])
    terminal = {"KEPT": 0, "BROKEN": 0, "CANCELLED": 0, "ABORTED": 0}
    for p in state["pledges"].values():
        if p["status"] in terminal:
            terminal[p["status"]] += 1
    assert terminal["KEPT"] == stats["kept"] and terminal["BROKEN"] == stats["broken"]
    assert terminal["CANCELLED"] == stats["cancelled"] and terminal["ABORTED"] == stats["aborted"]
    met = sum(1 for cp in state["checkpoints"].values() if cp["outcome"] == "MET")
    missed = sum(1 for cp in state["checkpoints"].values() if cp["outcome"] == "MISSED")
    assert met == stats["checkpoints_met"] and missed == stats["checkpoints_missed"]
    assert sum(p["checkpoints_met"] for p in profiles.values()) == met
    assert sum(p["checkpoints_missed"] for p in profiles.values()) == missed
    for p in profiles.values():
        assert p["balance"] >= 0 and p["locked"] >= 0
    for p in profiles.values():
        owner_active = sum(
            1 for pl in state["pledges"].values()
            if pl["owner"] == p["owner"] and pl["status"] in ("ACTIVE", "BREACHED")
        )
        assert p["active_pledges"] == owner_active, "active_pledges drifted"


class KWTest(unittest.TestCase):
    """Base class: a fresh deployed World per test, and the global invariants are
    re-checked after every test that otherwise passed."""

    rollback = True
    maxDiff = None

    def setUp(self):
        self.w = World(rollback=self.rollback)
        self.w.claim(self.w.alice, self.w.bob, self.w.carol, self.w.dave)

    def _failed(self) -> bool:
        result = getattr(getattr(self, "_outcome", None), "result", None)
        for bucket in (getattr(result, "failures", []), getattr(result, "errors", [])):
            if any(test is self for test, _ in bucket):
                return True
        return False

    def tearDown(self):
        if not self._failed():
            invariant_check(self.w)

    def reverts(self, text: str, who, name: str, *args) -> str:
        """The call must revert with `text` and leave contract state exactly as it was
        (this is only a real check in the no-rollback mode, which is why both run)."""
        before = self.w.c.state()
        with self.assertRaises(Reverted) as ctx:
            self.w.send(who, name, *args)
        message = ctx.exception.message
        self.assertIn(text.lower(), message.lower())
        self.assertEqual(self.w.c.state(), before, "a rejected call changed contract state")
        return message


    def no_consensus(self, who, name: str, *args) -> None:
        """Validators refuse the round: the transaction fails and leaves no trace."""
        from genlayer import ConsensusFailure

        before = self.w.c.state()
        with self.assertRaises(ConsensusFailure):
            self.w.send(who, name, *args)
        self.assertEqual(self.w.c.state(), before, "a failed consensus round changed contract state")


def both_modes(mixin):
    """Register `<Mixin>Rollback` and `<Mixin>NoRollback` TestCases in the mixin's module."""
    module = sys.modules[mixin.__module__]
    for label, flag in (("Rollback", True), ("NoRollback", False)):
        name = mixin.__name__ + label
        setattr(module, name, type(name, (mixin, KWTest), {"rollback": flag, "__module__": module.__name__}))
    return mixin
