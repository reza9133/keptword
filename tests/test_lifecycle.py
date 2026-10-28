"""Pledge endings: KEPT, BREACHED -> BROKEN, appeals, abort, and the exact settlement maths."""
import unittest

from helpers import *  # noqa: F401,F403
from helpers import KWTest, both_modes, honest_llm, Reverted


@both_modes
class LifecycleCases:
    # ------------------------------------------------------------ helpers
    def run_windows(self, pid, plan, who=None):
        """plan: iterable of booleans, one per checkpoint, True = publish evidence."""
        w = self.w
        for i, good in enumerate(plan):
            if w.pledge(pid).status != "ACTIVE":
                break
            w.set_page(w.feed_for(pid, [i] if good else []))
            w.to_verify(pid, i)
            w.verify(pid, who)

    def bal(self, *whos):
        return [self.w.balance(x) for x in whos]

    # ------------------------------------------------------------- KEPT
    def test_kept_pays_owner_stake_plus_sponsor_pool(self):
        w = self.w
        pid = w.open(stake=100)
        w.send(w.bob, "sponsor_pledge", pid, 50)
        w.send(w.carol, "sponsor_pledge", pid, 30)
        preview = w.view("preview_outcomes", pid)
        self.assertEqual(preview["if_kept"], {"owner_receives": 180, "burned": 0})
        self.run_windows(pid, [True, True, True])
        self.assertEqual(w.pledge(pid).status, "KEPT")
        self.assertEqual(self.bal(w.alice, w.bob, w.carol), [1080, 950, 970])
        s = w.standing(w.alice)
        self.assertEqual((s["pledges_kept"], s["streak"], s["best_streak"], s["active_pledges"], s["locked"]), (1, 1, 1, 0, 0))
        self.assertEqual((s["checkpoints_met"], s["trust_bps"]), (3, 6666))
        for sp in w.view("list_pledge_sponsorships", pid):
            self.assertEqual((sp.outcome, int(sp.payout)), ("REWARDED", 0))
        self.assertEqual(w.standing(w.bob)["sponsored_kept"], 1)
        self.assertEqual(w.view("get_stats")["points_burned"], 0)
        self.assertEqual([e[0] for e in w.last_events], ["CheckpointRecorded", "PledgeClosed"])
        self.reverts("not active", w.bob, "verify_checkpoint", pid)

    def test_one_missed_window_is_tolerated_when_min_met_allows_it(self):
        w = self.w
        pid = w.open(checkpoints=3, min_met=2)
        self.run_windows(pid, [False, True, True])
        p = w.pledge(pid)
        self.assertEqual((p.status, int(p.met), int(p.missed)), ("KEPT", 2, 1))
        self.assertEqual(w.standing(w.alice)["checkpoint_hit_rate_bps"], 6666)

    def test_single_checkpoint_pledge(self):
        w = self.w
        pid = w.open(checkpoints=1, min_met=1, interval=1800)
        self.run_windows(pid, [True])
        self.assertEqual(w.pledge(pid).status, "KEPT")

    def test_streak_grows_and_resets(self):
        w = self.w
        for i in range(2):
            pid = w.open(title=f"Streak pledge number {i}", checkpoints=1, min_met=1)
            self.run_windows(pid, [True])
        s = w.standing(w.alice)
        self.assertEqual((s["streak"], s["best_streak"], s["pledges_kept"]), (2, 2, 2))
        pid = w.open(title="Streak breaking pledge", checkpoints=1, min_met=1)
        self.run_windows(pid, [False])
        w.warp(int(w.pledge(pid).appeal_deadline))
        w.send(w.bob, "finalize_breach", pid)
        s = w.standing(w.alice)
        self.assertEqual((s["streak"], s["best_streak"], s["pledges_kept"], s["pledges_broken"]), (0, 2, 2, 1))
        self.assertEqual(s["trust_bps"], 6000)  # (2+1)/(2+1+2)

    # ----------------------------------------------------------- BREACH
    def test_breach_then_finalize_splits_the_stake(self):
        w = self.w
        pid = w.open(stake=100, checkpoints=3, min_met=2)
        w.send(w.bob, "sponsor_pledge", pid, 50)
        w.send(w.carol, "sponsor_pledge", pid, 30)
        self.assertEqual(w.view("preview_outcomes", pid)["if_broken"],
                         {"owner_receives": 0, "sponsor_compensation": 49, "burned": 51})
        self.run_windows(pid, [False, False])
        p = w.pledge(pid)
        self.assertEqual((p.status, int(p.appeal_deadline)), ("BREACHED", w.now + 6 * HOUR))
        self.assertEqual([e[0] for e in w.last_events], ["CheckpointRecorded", "PledgeBreached"])
        self.reverts("not active", w.bob, "verify_checkpoint", pid)
        self.reverts("still open", w.bob, "finalize_breach", pid)
        w.warp(int(p.appeal_deadline) - 1)
        self.reverts("still open", w.bob, "finalize_breach", pid)
        w.warp(int(p.appeal_deadline))
        w.send(w.dave, "finalize_breach", pid)
        self.assertEqual(w.pledge(pid).status, "BROKEN")
        # alice lost 100; bob gets 50 back + 31; carol gets 30 back + 18; 51 burned
        self.assertEqual(self.bal(w.alice, w.bob, w.carol), [900, 1031, 1018])
        self.assertEqual(w.view("get_stats")["points_burned"], 51)
        outcomes = {str(s.sponsor.as_hex): (s.outcome, int(s.payout)) for s in w.view("list_pledge_sponsorships", pid)}
        self.assertEqual(outcomes[w.bob.as_hex], ("COMPENSATED", 81))
        self.assertEqual(outcomes[w.carol.as_hex], ("COMPENSATED", 48))
        self.assertEqual((w.standing(w.bob)["sponsored_broken"], w.standing(w.alice)["pledges_broken"]), (1, 1))
        self.assertEqual(w.view("get_schedule", pid)["checkpoints"][2]["state"], "NOT_RUN")
        self.reverts("not breached", w.bob, "finalize_breach", pid)

    def test_broken_without_sponsors_burns_the_whole_stake(self):
        w = self.w
        pid = w.open(stake=77, checkpoints=1, min_met=1)
        self.run_windows(pid, [False])
        w.warp(int(w.pledge(pid).appeal_deadline))
        w.send(w.bob, "finalize_breach", pid)
        self.assertEqual((w.balance(w.alice), w.view("get_stats")["points_burned"]), (923, 77))

    def test_no_tolerance_pledge_breaches_on_first_miss(self):
        w = self.w
        pid = w.open(checkpoints=3, min_met=3)
        self.run_windows(pid, [False])
        self.assertEqual(w.pledge(pid).status, "BREACHED")

    def test_unreachable_page_three_times_can_breach(self):
        w = self.w
        pid = w.open(checkpoints=1, min_met=1)
        w.to_verify(pid, 0)
        w.drop_page()
        for _ in range(2):
            self.assertEqual(w.verify(pid), 0)
            w.advance(301)
        self.assertGreater(w.verify(pid), 0)
        self.assertEqual(w.pledge(pid).status, "BREACHED")

    def test_integer_rounding_leaves_dust_in_the_burn(self):
        w = self.w
        pid = w.open(stake=21, checkpoints=1, min_met=1)
        for who, amt in ((w.bob, 7), (w.carol, 5), (w.dave, 5)):
            w.send(who, "sponsor_pledge", pid, amt)
        self.run_windows(pid, [False])
        w.warp(int(w.pledge(pid).appeal_deadline))
        w.send(w.bob, "finalize_breach", pid)
        # compensation 10 split 7:5:5 of 17 -> 4, 2, 2 ; burned 21 - 8 = 13
        self.assertEqual(self.bal(w.bob, w.carol, w.dave), [1004, 1002, 1002])
        self.assertEqual(w.view("get_stats")["points_burned"], 13)

    # ----------------------------------------------------------- APPEALS
    def breach(self, pid_kwargs=None):
        w = self.w
        pid = w.open(**(pid_kwargs or {}))
        self.run_windows(pid, [False, False])
        return pid

    def evidence_page(self, pid, index, url=URL2, n=1):
        w = self.w
        start, _ = w.window(pid, index)
        w.set_page(feed(*[entry(start + (i + 1) * HOUR, f"Late proof entry {i} about the garden") for i in range(n)]), url)

    def test_appeal_wins_and_the_pledge_continues(self):
        w = self.w
        pid = self.breach()
        self.assertEqual(w.pledge(pid).status, "BREACHED")
        self.evidence_page(pid, 1)
        free_before = w.balance(w.alice)
        cid = int(w.send(w.alice, "appeal_breach", pid, URL2))
        p = w.pledge(pid)
        self.assertEqual((p.status, int(p.met), int(p.missed), int(p.appeals_used), int(p.appeal_deadline)), ("ACTIVE", 1, 1, 1, 0))
        cp = w.view("get_checkpoint", cid)
        self.assertEqual((cp.outcome, cp.reason, cp.first_reason, cp.appealed, cp.appeal_url, int(cp.index)),
                         ("MET", "EVIDENCE", "NOT_ENOUGH_EVIDENCE", True, URL2, 1))
        self.assertEqual(w.balance(w.alice), free_before - 10)
        stats = w.view("get_stats")
        self.assertEqual((stats["appeals"], stats["appeals_won"], stats["points_burned"]), (1, 1, 10))
        self.assertEqual((stats["checkpoints_met"], stats["checkpoints_missed"]), (1, 1))
        self.assertEqual([e[0] for e in w.last_events], ["AppealResolved"])
        # the pledge goes on: the last window is delivered
        w.set_page(w.feed_for(pid, [2]))
        w.to_verify(pid, 2)
        w.verify(pid)
        self.assertEqual(w.pledge(pid).status, "KEPT")
        self.assertEqual(w.balance(w.alice), free_before - 10 + 100)

    def test_appeal_winning_the_last_checkpoint_keeps_the_pledge_at_once(self):
        w = self.w
        pid = w.open(checkpoints=1, min_met=1)
        self.run_windows(pid, [False])
        self.evidence_page(pid, 0)
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual(w.pledge(pid).status, "KEPT")
        self.assertEqual(w.standing(w.alice)["pledges_kept"], 1)
        self.assertEqual((w.standing(w.alice)["checkpoints_met"], w.standing(w.alice)["checkpoints_missed"]), (1, 0))

    def test_appeal_loses_and_breaks_immediately(self):
        w = self.w
        pid = self.breach()
        w.set_page(feed(entry(MIDNIGHT - DAY, "nothing useful here at all")), URL2)
        w.send(w.alice, "appeal_breach", pid, URL2)
        p = w.pledge(pid)
        self.assertEqual((p.status, int(p.appeals_used)), ("BROKEN", 1))
        self.assertEqual(w.balance(w.alice), 1000 - 100 - 10)
        self.assertEqual(w.view("get_stats")["points_burned"], 110)
        cp = w.view("list_pledge_checkpoints", pid)[1]
        self.assertEqual((cp.outcome, cp.appealed, cp.appeal_url, cp.first_reason), ("MISSED", True, URL2, ""))
        self.assertEqual([e[0] for e in w.last_events], ["AppealResolved", "PledgeClosed"])

    def test_appeal_with_unreadable_url_costs_nothing(self):
        w = self.w
        pid = self.breach()
        before = w.balance(w.alice)
        self.reverts("could not be read", w.alice, "appeal_breach", pid, "https://missing.example.org/page")
        self.assertEqual((w.balance(w.alice), int(w.pledge(pid).appeals_used), w.pledge(pid).status), (before, 0, "BREACHED"))
        self.evidence_page(pid, 1)  # the window is still open, a second try works
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual(w.pledge(pid).status, "ACTIVE")

    def test_appeal_with_failing_model_costs_nothing(self):
        w = self.w
        pid = self.breach()
        self.evidence_page(pid, 1)
        w.set_model(lambda p: {})
        self.no_consensus(w.alice, "appeal_breach", pid, URL2)
        w.set_model(honest_llm)
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual(int(w.pledge(pid).appeals_used), 1)

    def test_appeal_rules(self):
        w = self.w
        pid = w.open()
        self.reverts("nothing to appeal", w.alice, "appeal_breach", pid, URL2)
        self.run_windows(pid, [False, False])
        self.evidence_page(pid, 1)
        self.reverts("only the owner", w.bob, "appeal_breach", pid, URL2)
        self.reverts("public https", w.alice, "appeal_breach", pid, "http://blog.example.org/x")
        self.reverts("pledge not found", w.alice, "appeal_breach", 9, URL2)
        deadline = int(w.pledge(pid).appeal_deadline)
        w.warp(deadline)
        self.reverts("window is closed", w.alice, "appeal_breach", pid, URL2)
        w.send(w.bob, "finalize_breach", pid)  # complementary boundary: finalize opens exactly then
        self.reverts("nothing to appeal", w.alice, "appeal_breach", pid, URL2)

    def test_appeal_fee_needs_free_balance(self):
        w = self.w
        w.open(title="Big first pledge here", stake=500, checkpoints=3, min_met=1)
        pid = w.open(title="Big second pledge here", stake=500)
        self.assertEqual(w.balance(w.alice), 0)
        self.run_windows(pid, [False, False])
        self.evidence_page(pid, 1)
        self.reverts("costs 10 WP", w.alice, "appeal_breach", pid, URL2)

    def test_appeal_works_with_exactly_the_fee_in_the_wallet(self):
        w = self.w
        w.open(title="Big first pledge here", stake=500, checkpoints=3, min_met=1)
        pid = w.open(title="Second pledge, 490 stake", stake=490)
        self.assertEqual(w.balance(w.alice), 10)
        self.run_windows(pid, [False, False])
        self.evidence_page(pid, 1)
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual((w.balance(w.alice), w.pledge(pid).status), (0, "ACTIVE"))

    def test_a_second_breach_after_a_won_appeal_is_final(self):
        w = self.w
        pid = w.open(checkpoints=4, min_met=2)
        self.run_windows(pid, [False, False, False])  # third miss exceeds the 2 allowed
        self.assertEqual(w.pledge(pid).status, "BREACHED")
        self.evidence_page(pid, 2)
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual(w.pledge(pid).status, "ACTIVE")
        w.set_page(w.feed_for(pid, []))
        w.to_verify(pid, 3)
        w.verify(pid)
        self.assertEqual(w.pledge(pid).status, "BROKEN")  # no second appeal exists

    def test_appeal_with_two_items_needed(self):
        w = self.w
        pid = w.open(min_items=2, checkpoints=3, min_met=2)
        self.run_windows(pid, [False, False])
        self.evidence_page(pid, 1, n=1)
        w.send(w.alice, "appeal_breach", pid, URL2)
        self.assertEqual(w.pledge(pid).status, "BROKEN")  # one entry was not enough

    # ------------------------------------------------------------ ABORT
    def test_abort_stalled_refunds_everyone(self):
        w = self.w
        pid = w.open(stake=120)
        w.send(w.bob, "sponsor_pledge", pid, 40)
        self.run_windows(pid, [True])
        stall = w.opens(pid, 1) + 7 * DAY
        w.warp(stall - 1)
        self.reverts("not stalled", w.carol, "abort_stalled", pid)
        w.warp(stall)
        w.send(w.carol, "abort_stalled", pid)
        self.assertEqual(w.pledge(pid).status, "ABORTED")
        self.assertEqual(self.bal(w.alice, w.bob), [1000, 1000])
        stats = w.view("get_stats")
        self.assertEqual((stats["aborted"], stats["points_locked"], stats["points_burned"]), (1, 0, 0))
        self.assertEqual(w.standing(w.alice)["pledges_broken"], 0)
        self.assertEqual(w.view("get_schedule", pid)["checkpoints"][1]["state"], "NOT_RUN")
        self.reverts("not active", w.carol, "abort_stalled", pid)

    def test_abort_not_possible_on_breached_or_new_pledges(self):
        w = self.w
        pid = w.open(checkpoints=1, min_met=1)
        self.reverts("not stalled", w.bob, "abort_stalled", pid)
        self.run_windows(pid, [False])
        w.warp(w.now + 30 * DAY)
        self.reverts("not active", w.bob, "abort_stalled", pid)
        self.reverts("pledge not found", w.bob, "abort_stalled", 77)

    def test_model_outage_for_a_week_ends_in_a_refund(self):
        w = self.w
        pid = w.open()
        w.set_page(w.feed_for(pid, [0]))
        w.to_verify(pid, 0)
        w.set_model(lambda p: {})
        self.no_consensus(w.bob, "verify_checkpoint", pid)
        w.warp(w.opens(pid, 0) + 7 * DAY)
        w.send(w.bob, "abort_stalled", pid)
        self.assertEqual(w.balance(w.alice), 1000)

    # ------------------------------------------------------------ views
    def test_schedule_view_follows_the_lifecycle(self):
        w = self.w
        pid = w.open()
        rows = w.view("get_schedule", pid)
        self.assertEqual((rows["allowed_misses"], [r["state"] for r in rows["checkpoints"]]), (1, ["PENDING"] * 3))
        self.run_windows(pid, [True, False])
        self.assertEqual([r["state"] for r in w.view("get_schedule", pid)["checkpoints"]], ["MET", "MISSED", "PENDING"])
        w.set_page(w.feed_for(pid, [2]))
        w.to_verify(pid, 2)
        w.verify(pid)
        done = w.view("get_schedule", pid)
        self.assertEqual((done["status"], [r["state"] for r in done["checkpoints"]]), ("KEPT", ["MET", "MISSED", "MET"]))

    def test_pagination(self):
        w = self.w
        for i in range(4):
            w.open(title=f"Pagination pledge {i}", stake=20)
        self.assertEqual([int(p.id) for p in w.view("list_pledges", 0, 3)], [1, 2, 3])
        self.assertEqual([int(p.id) for p in w.view("list_pledges", 3, 10)], [4])
        self.assertEqual(list(w.view("list_pledges", 9, 10)), [])
        self.assertEqual(len(w.view("list_pledges", 0, 1000)), 4)
        self.assertEqual(len(w.view("list_profiles", 0, 2)), 2)
        self.assertEqual(len(w.view("list_profiles", 0, 1000)), 4)
        self.assertEqual(list(w.view("list_pledges", 0, 0)), [])

    def test_get_config_matches_the_constants(self):
        cfg = self.w.view("get_config")
        m = self.w.c.module
        self.assertEqual(cfg["starter_grant"], m.STARTER_GRANT)
        self.assertEqual(cfg["appeal_fee"], m.APPEAL_FEE)
        self.assertEqual(cfg["appeal_window_seconds"], 6 * HOUR)


if __name__ == "__main__":
    unittest.main()
