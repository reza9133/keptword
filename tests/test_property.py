"""Randomised differential test: the contract vs an independent reference model."""
import random
import unittest

from helpers import *  # noqa: F401,F403  (also puts the SDK shim on sys.path)
from helpers import World, invariant_check, entry, feed, honest_llm, MIDNIGHT, DAY, HOUR, TITLE, COMMITMENT
from harness import Reverted
from reference import Ref

OLD = entry(MIDNIGHT - DAY, "Old post from before any pledge started")


def purl(pid: int) -> str:
    return f"https://p{pid}.example.org/feed"


def window_page(start: int, end: int, k: int) -> str:
    step = (end - start) // (k + 1)
    entries = [entry(start + (i + 1) * step, f"Garden entry number {i} written during the window") for i in range(k)]
    return feed(*entries, OLD)


class Sim:
    def __init__(self, seed: int, rollback: bool) -> None:
        self.rng = random.Random(seed)
        self.w = World(rollback=rollback)
        self.w.c.check_view_purity = False  # speed; purity is covered by the other suites
        self.ref = Ref()
        self.accts = [acct(i) for i in range(1, 8)]
        self.appeal_n = 0
        self.log: list = []
        for who in self.accts:  # most accounts start registered
            if self.rng.random() < 0.85:
                self.ref.claim(who)
                self.w.send(who, "claim_starter_points")

    # ---------------------------------------------------------------- compare
    def compare(self) -> None:
        w, ref = self.w, self.ref
        for who in self.accts:
            s = w.standing(who)
            m = ref.m.get(who)
            if m is None:
                assert not s["registered"], self.log
                continue
            got = (s["balance"], s["locked"], s["active_pledges"], s["pledges_kept"], s["pledges_broken"],
                   s["checkpoints_met"], s["checkpoints_missed"], s["streak"], s["best_streak"],
                   s["sponsored_kept"], s["sponsored_broken"])
            want = (m["balance"], m["locked"], m["active"], m["kept"], m["broken"], m["met"], m["missed"],
                    m["streak"], m["best"], m["s_kept"], m["s_broken"])
            assert got == want, f"profile mismatch {got} != {want}\n" + "\n".join(self.log[-6:])
        for pid, p in ref.p.items():
            c = w.pledge(pid)
            got = (c.status, int(c.checked), int(c.met), int(c.missed), int(c.fetch_failures), int(c.appeals_used),
                   int(c.sponsor_count), int(c.sponsor_pool), int(c.appeal_deadline) if c.status == "BREACHED" else 0)
            want = (p["status"], p["checked"], p["met"], p["missed"], p["fails"], p["appeals"],
                    len(p["spons"]), sum(a for _, a in p["spons"]), p["deadline"] if p["status"] == "BREACHED" else 0)
            assert got == want, f"pledge {pid} mismatch {got} != {want}\n" + "\n".join(self.log[-6:])
            cps = w.view("list_pledge_checkpoints", pid)
            assert [x.outcome for x in cps] == p["results"], f"checkpoint results differ for pledge {pid}"
        stats = w.view("get_stats")
        assert stats["points_burned"] == ref.burned and stats["points_granted"] == ref.granted, self.log[-6:]

    # ------------------------------------------------------------------ steps
    def attempt(self, name: str, ref_ok: bool, send) -> None:
        self.log.append(f"{name} expect={'ok' if ref_ok else 'revert'} t={self.w.now}")
        try:
            send()
            got_ok = True
        except Reverted:
            got_ok = False
        assert got_ok == ref_ok, f"{name}: contract {'ok' if got_ok else 'reverted'}, reference expected " \
            f"{'ok' if ref_ok else 'revert'}\n" + "\n".join(self.log[-8:])

    def step(self) -> None:
        rng, w, ref = self.rng, self.w, self.ref
        pids = list(ref.p)
        op = rng.choices(
            ["claim", "open", "sponsor", "cancel", "verify", "appeal", "finalize", "abort", "time"],
            weights=[2, 10, 9, 2, 24, 8, 6, 3, 12],
        )[0]
        who = rng.choice(self.accts)
        pid = rng.choice(pids) if pids and rng.random() < 0.93 else rng.randint(1, len(pids) + 3)
        now = w.now

        if op == "claim":
            self.attempt("claim", ref.claim(who), lambda: w.send(who, "claim_starter_points"))
        elif op == "open":
            url = purl(len(ref.p) + 1)
            page_ok = rng.random() > 0.07
            if page_ok:
                w.set_page(feed(OLD), url)
            else:
                w.drop_page(url)
            start = now + rng.choice([300, 3600, 16 * HOUR, DAY, 10 * DAY])
            interval = rng.choice([600, 1200, HOUR, DAY, DAY, 3 * DAY])
            cp = rng.randint(1, 5)
            min_met = rng.randint(1, cp)
            items = rng.choice([1, 1, 1, 2, 3])
            stake = rng.choice([20, 50, 100, 250, 500])
            text_ok = rng.random() > 0.03
            if rng.random() < 0.12:  # sometimes break exactly one rule
                field = rng.choice(["start", "interval", "cp", "min_met", "items", "stake"])
                if field == "start":
                    start = now + rng.choice([200, 299, 91 * DAY])
                elif field == "interval":
                    interval = rng.choice([599, 31 * DAY])
                elif field == "cp":
                    cp = rng.choice([0, 7])
                    min_met = max(1, min(min_met, 6))
                elif field == "min_met":
                    min_met = rng.choice([0, cp + 1])
                elif field == "items":
                    items = rng.choice([0, 6])
                else:
                    stake = rng.choice([19, 501])
            title = TITLE if text_ok else "short"
            expect = ref.open(who, now, start, interval, cp, min_met, items, stake, text_ok, True, page_ok)
            self.attempt("open", expect, lambda: w.send(who, "open_pledge", title, COMMITMENT, url, start,
                                                         interval, cp, min_met, items, stake))
        elif op == "sponsor":
            amount = rng.choice([4, 5, 20, 60, 200, 500, 501])
            self.attempt("sponsor", ref.sponsor(who, pid, amount, now), lambda: w.send(who, "sponsor_pledge", pid, amount))
        elif op == "cancel":
            self.attempt("cancel", ref.cancel(who, pid, now), lambda: w.send(who, "cancel_pledge", pid))
        elif op == "verify":
            p = ref.p.get(pid)
            page = None
            if p is not None and p["status"] == "ACTIVE" and p["checked"] < p["cp"]:
                idx = p["checked"]
                start = p["start"] + idx * p["interval"]
                end = start + p["interval"]
                if rng.random() > 0.12:
                    page = rng.choice([0, 1, 1, 2, 2, 3, 4])
                    w.set_page(window_page(start, end, page), purl(pid))
                else:
                    w.drop_page(purl(pid))
                if rng.random() < 0.8:  # usually wait until the checkpoint is ready
                    w.warp(max(w.now, ref.opens(p, idx)))
                    now = w.now
            sender = rng.choice(self.accts)
            self.attempt("verify", ref.verify(pid, now, page), lambda: w.send(sender, "verify_checkpoint", pid))
        elif op == "appeal":
            p = ref.p.get(pid)
            page = None
            self.appeal_n += 1
            url = f"https://a{self.appeal_n}.example.org/x"
            if p is not None and p["status"] == "BREACHED":
                idx = p["checked"] - 1
                start = p["start"] + idx * p["interval"]
                if rng.random() > 0.2:
                    page = rng.choice([0, 1, 1, 2, 3])
                    w.set_page(window_page(start, start + p["interval"], page), url)
            sender = p["owner"] if p is not None and rng.random() < 0.85 else who
            self.attempt("appeal", ref.appeal(sender, pid, now, page), lambda: w.send(sender, "appeal_breach", pid, url))
        elif op == "finalize":
            p = ref.p.get(pid)
            if p is not None and p["status"] == "BREACHED" and rng.random() < 0.5:
                w.warp(max(w.now, p["deadline"] - rng.choice([0, 1])))
                now = w.now
            self.attempt("finalize", ref.finalize(pid, now), lambda: w.send(who, "finalize_breach", pid))
        elif op == "abort":
            p = ref.p.get(pid)
            if p is not None and p["status"] == "ACTIVE" and p["checked"] < p["cp"] and rng.random() < 0.5:
                w.warp(max(w.now, ref.opens(p, p["checked"]) + 7 * DAY - rng.choice([0, 1])))
                now = w.now
            self.attempt("abort", ref.abort(pid, now), lambda: w.send(who, "abort_stalled", pid))
        else:  # time
            choice = rng.random()
            if choice < 0.5 or not pids:
                w.advance(rng.choice([1, 60, 299, 300, 301, HOUR, 6 * HOUR, DAY, 3 * DAY]))
            else:
                p = ref.p[rng.choice(pids)]
                targets = [p["start"] - 1, p["start"], p["deadline"] - 1, p["deadline"]]
                if p["checked"] < p["cp"]:
                    targets += [ref.opens(p, p["checked"]) - 1, ref.opens(p, p["checked"])]
                future = [t for t in targets if t > w.now]
                if future:
                    w.warp(rng.choice(future))
            self.log.append(f"time -> {w.now}")
        self.compare()

    def run(self, steps: int) -> None:
        for _ in range(steps):
            self.step()
        invariant_check(self.w)


class PropertyCases(unittest.TestCase):
    SEEDS = int(__import__('os').environ.get('KW_SEEDS', '40'))
    STEPS = 60

    def run_seeds(self, rollback: bool, first: int) -> None:
        for seed in range(first, first + self.SEEDS):
            with self.subTest(seed=seed, rollback=rollback):
                Sim(seed, rollback).run(self.STEPS)

    def test_random_sequences_with_rollback(self):
        self.run_seeds(True, 0)

    def test_random_sequences_without_rollback(self):
        self.run_seeds(False, 10_000)


if __name__ == "__main__":
    unittest.main()
