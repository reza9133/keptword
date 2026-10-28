"""Points, pledge creation rules, URL safety, sponsoring and cancelling."""
import json
import unittest
from pathlib import Path

from helpers import *  # noqa: F401,F403  (also puts the SDK shim on sys.path)
from helpers import KWTest, both_modes, Reverted
from genlayer import ConsensusFailure

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "url_vectors.json").read_text())["vectors"]


@both_modes
class BasicsCases:
    def test_claim_starter_points_once(self):
        w = self.w
        fresh = acct(9)
        self.assertEqual(int(w.send(fresh, "claim_starter_points")), 1000)
        self.assertEqual(w.balance(fresh), 1000)
        self.reverts("already claimed", fresh, "claim_starter_points")
        stats = w.view("get_stats")
        self.assertEqual((stats["members"], stats["points_granted"]), (5, 5000))

    def test_unknown_address_standing(self):
        w = self.w
        self.assertEqual(w.standing(acct(30)), {"address": acct(30).as_hex, "registered": False})
        with self.assertRaises(Reverted):
            w.view("get_profile", acct(30).as_hex)

    def test_open_pledge_happy_path_and_storage(self):
        w = self.w
        pid = w.open(stake=120)
        p = w.pledge(pid)
        self.assertEqual((pid, p.owner, p.status), (1, w.alice, "ACTIVE"))
        self.assertEqual((int(p.stake), int(p.checkpoints), int(p.min_met)), (120, 3, 2))
        self.assertEqual((int(p.created_at), int(p.start_at)), (w.now, w.now + 16 * HOUR))
        s = w.standing(w.alice)
        self.assertEqual((s["balance"], s["locked"], s["active_pledges"]), (880, 120, 1))
        self.assertEqual(list(w.view("list_owner_pledges", w.alice.as_hex)), [1])
        self.assertEqual(w.view("get_stats")["points_locked"], 120)
        self.assertEqual([e[0] for e in w.last_events], ["PledgeOpened"])

    def test_text_normalisation(self):
        w = self.w
        pid = w.open(title="  Garden   posts\n every   day  ", commitment="  " + COMMITMENT + "  \n")
        self.assertEqual(w.pledge(pid).title, "Garden posts every day")
        self.assertEqual(w.pledge(pid).commitment, COMMITMENT)
        self.assertEqual(len(w.pledge(w.open(title="x" * 300)).title), 120)

    def test_open_pledge_rejections(self):
        w = self.w
        cases = [
            (dict(title="short"), "title must be at least"),
            (dict(title="       short       "), "title must be at least"),
            (dict(commitment="too short to be a commitment"), "commitment must be at least"),
            (dict(title="Bad <<< title here"), "may not contain"),
            (dict(commitment=COMMITMENT + " >>> sneaky"), "may not contain"),
            (dict(url="http://blog.example.org/feed"), "public https"),
            (dict(url="https://127.0.0.1/feed"), "public https"),
            (dict(url="https://blog.example.org:8443/feed"), "public https"),
            (dict(start_in=299), "five minutes"),
            (dict(start_in=90 * DAY + 1), "90 days"),
            (dict(interval=599), "interval_seconds"),
            (dict(interval=30 * DAY + 1), "interval_seconds"),
            (dict(checkpoints=0, min_met=1), "checkpoints must be between"),
            (dict(checkpoints=7, min_met=1), "checkpoints must be between"),
            (dict(checkpoints=3, min_met=0), "min_met"),
            (dict(checkpoints=3, min_met=4), "min_met"),
            (dict(min_items=0), "min_items"),
            (dict(min_items=6), "min_items"),
            (dict(stake=19), "stake must be between"),
            (dict(stake=501), "stake must be between"),
        ]
        for kwargs, text in cases:
            with self.subTest(kwargs=kwargs):
                a = dict(title=TITLE, commitment=COMMITMENT, url=URL, start_in=16 * HOUR, interval=DAY,
                         checkpoints=3, min_met=2, min_items=1, stake=100)
                a.update(kwargs)
                self.reverts(text, w.alice, "open_pledge", a["title"], a["commitment"], a["url"],
                             w.now + a["start_in"], a["interval"], a["checkpoints"], a["min_met"],
                             a["min_items"], a["stake"])

    def test_boundaries_are_accepted(self):
        w = self.w
        self.assertEqual(w.open(start_in=300, interval=600, checkpoints=1, min_met=1, min_items=1, stake=20), 1)
        self.assertEqual(w.open(title="Boundary two pledge", start_in=90 * DAY, interval=30 * DAY,
                                checkpoints=6, min_met=6, min_items=5, stake=500), 2)

    def test_open_requires_registration_and_balance(self):
        w = self.w
        self.reverts("claim your starter points", acct(31), "open_pledge", TITLE, COMMITMENT, URL,
                     w.now + DAY, DAY, 3, 2, 1, 100)
        w.open(stake=500)
        w.open(stake=500, title="Second big pledge here")
        self.reverts("not enough free WP", w.alice, "open_pledge", "Third big pledge here", COMMITMENT, URL,
                     w.now + DAY, DAY, 3, 2, 1, 100)

    def test_active_pledge_cap_and_release_by_cancel(self):
        w = self.w
        ids = [w.open(title=f"Cap test pledge number {i}", stake=20) for i in range(5)]
        self.reverts("too many active pledges", w.alice, "open_pledge", "Cap test pledge number 6", COMMITMENT,
                     URL, w.now + DAY, DAY, 3, 2, 1, 20)
        w.send(w.alice, "cancel_pledge", ids[0])
        self.assertEqual(w.open(title="Cap test pledge number 6", stake=20), 6)

    def test_unreachable_proof_page(self):
        w = self.w
        w.drop_page()
        msg = self.reverts("could not be fetched", w.alice, "open_pledge", TITLE, COMMITMENT, URL,
                           w.now + DAY, DAY, 3, 2, 1, 100)
        self.assertTrue(msg.startswith("[EXTERNAL]"))
        w.set_page("   \n\t  ")  # a blank page is unreadable too
        self.reverts("could not be fetched", w.alice, "open_pledge", TITLE, COMMITMENT, URL,
                     w.now + DAY, DAY, 3, 2, 1, 100)

    def test_reachability_is_a_vote(self):
        w = self.w
        w.c.nodes[2].pages.pop(URL)  # one of three cannot read: the majority decides
        self.assertEqual(w.open(), 1)
        w.c.nodes[1].pages.pop(URL)  # two of three cannot read: no agreement with the leader
        with self.assertRaises(ConsensusFailure):
            w.open(title="Another readable pledge")

    def test_url_vectors(self):
        w = self.w
        for url, expected in VECTORS:
            with self.subTest(url=url):
                self.assertIs(w.c.module._is_public_https_url(url), expected)

    def test_url_vectors_through_open_pledge(self):
        w = self.w
        for url, expected in VECTORS:
            if not expected:
                with self.subTest(url=url):
                    self.reverts("public https", w.alice, "open_pledge", TITLE, COMMITMENT, url,
                                 w.now + DAY, DAY, 3, 2, 1, 100)

    # ------------------------------------------------------------ sponsoring
    def test_sponsor_rules(self):
        w = self.w
        pid = w.open()
        self.assertEqual(int(w.send(w.bob, "sponsor_pledge", pid, 40)), 1)
        sp = w.view("list_pledge_sponsorships", pid)[0]
        self.assertEqual((sp.sponsor, int(sp.amount), sp.outcome), (w.bob, 40, "LOCKED"))
        p = w.pledge(pid)
        self.assertEqual((int(p.sponsor_count), int(p.sponsor_pool)), (1, 40))
        self.assertEqual((w.standing(w.bob)["balance"], w.standing(w.bob)["locked"]), (960, 40))
        self.reverts("already sponsors", w.bob, "sponsor_pledge", pid, 10)
        self.reverts("owner cannot sponsor", w.alice, "sponsor_pledge", pid, 10)
        self.reverts("between", w.carol, "sponsor_pledge", pid, 4)
        self.reverts("between", w.carol, "sponsor_pledge", pid, 501)
        self.reverts("claim your starter points", acct(32), "sponsor_pledge", pid, 10)
        self.reverts("pledge not found", w.carol, "sponsor_pledge", 99, 10)
        self.assertEqual(len(w.view("list_sponsor_sponsorships", w.bob.as_hex, 0, 10)), 1)
        self.assertEqual(len(w.view("list_sponsor_sponsorships", w.carol.as_hex, 0, 10)), 0)

    def test_sponsor_needs_free_balance(self):
        w = self.w
        pid = w.open()
        w.send(w.bob, "sponsor_pledge", pid, 500)
        pid2 = w.open(title="Second pledge for sponsors")
        w.send(w.bob, "sponsor_pledge", pid2, 500)
        pid3 = w.open(title="Third pledge for sponsors")
        self.reverts("not enough free WP", w.bob, "sponsor_pledge", pid3, 5)
        w.send(w.carol, "sponsor_pledge", pid, 5)
        self.assertEqual(int(w.pledge(pid).sponsor_pool), 505)

    def test_sponsor_limit_twenty(self):
        w = self.w
        pid = w.open()
        for i in range(20):
            who = acct(40 + i)
            w.claim(who)
            w.send(who, "sponsor_pledge", pid, 5)
        extra = acct(80)
        w.claim(extra)
        self.reverts("sponsor limit", extra, "sponsor_pledge", pid, 5)
        self.assertEqual(int(w.pledge(pid).sponsor_count), 20)

    def test_sponsor_window_closes_at_start(self):
        w = self.w
        pid = w.open(start_in=HOUR)
        w.warp(int(w.pledge(pid).start_at) - 1)
        w.send(w.bob, "sponsor_pledge", pid, 10)
        w.warp(int(w.pledge(pid).start_at))
        self.reverts("closes when the pledge starts", w.carol, "sponsor_pledge", pid, 10)

    def test_cannot_sponsor_closed_pledge(self):
        w = self.w
        pid = w.open()
        w.send(w.alice, "cancel_pledge", pid)
        self.reverts("not active", w.bob, "sponsor_pledge", pid, 10)

    # ---------------------------------------------------------------- cancel
    def test_cancel_refunds_everyone(self):
        w = self.w
        pid = w.open(stake=150)
        w.send(w.bob, "sponsor_pledge", pid, 60)
        w.send(w.carol, "sponsor_pledge", pid, 40)
        self.reverts("only the owner", w.bob, "cancel_pledge", pid)
        w.send(w.alice, "cancel_pledge", pid)
        self.assertEqual(w.pledge(pid).status, "CANCELLED")
        self.assertEqual([w.balance(x) for x in (w.alice, w.bob, w.carol)], [1000, 1000, 1000])
        self.assertEqual(w.standing(w.alice)["active_pledges"], 0)
        for s in w.view("list_pledge_sponsorships", pid):
            self.assertEqual((s.outcome, int(s.payout)), ("REFUNDED", int(s.amount)))
        stats = w.view("get_stats")
        self.assertEqual((stats["cancelled"], stats["points_locked"], stats["points_burned"]), (1, 0, 0))
        self.reverts("not active", w.alice, "cancel_pledge", pid)
        st = w.standing(w.alice)
        self.assertEqual((st["pledges_broken"], st["pledges_kept"], st["streak"]), (0, 0, 0))

    def test_cancel_rejected_after_start(self):
        w = self.w
        pid = w.open(start_in=HOUR)
        w.warp(int(w.pledge(pid).start_at))
        self.reverts("can no longer be cancelled", w.alice, "cancel_pledge", pid)
        self.reverts("pledge not found", w.alice, "cancel_pledge", 42)


if __name__ == "__main__":
    unittest.main()
