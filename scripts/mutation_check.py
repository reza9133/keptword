#!/usr/bin/env python3
"""Mutation check: plant one bug at a time in the contract and make sure the test suite
notices. A mutant that SURVIVES points at a missing test.

    python3 scripts/mutation_check.py            # all mutants, 4 at a time
    python3 scripts/mutation_check.py 3 17       # only mutants 3 and 17
"""
import concurrent.futures as cf
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "contracts" / "kept_word.py").read_text()

MUTANTS = [
    ("breach threshold > becomes >=", "if int(pledge.missed) > allowed_misses:", "if int(pledge.missed) >= allowed_misses:"),
    ("KEPT needs one extra checkpoint", "elif int(pledge.checked) >= count:", "elif int(pledge.checked) > count:"),
    ("compensation share doubled", "compensation = (stake * BROKEN_COMPENSATION_PERCENT) // 100", "compensation = (stake * BROKEN_COMPENSATION_PERCENT * 2) // 100"),
    ("sponsoring stays open at start", "if now >= int(pledge.start_at):\n            raise gl.vm.UserError(\"[EXPECTED] sponsoring closes", "if now > int(pledge.start_at):\n            raise gl.vm.UserError(\"[EXPECTED] sponsoring closes"),
    ("verify opens one second early", "if now < self._verify_opens(pledge, index):", "if now < self._verify_opens(pledge, index) - 1:"),
    ("grace too long", "return min(interval // 4, MAX_GRACE_SECONDS)", "return min(interval // 2, MAX_GRACE_SECONDS)"),
    ("cooldown off by one", "if last and now - last < MIN_ATTEMPT_INTERVAL_SECONDS:", "if last and now - last <= MIN_ATTEMPT_INTERVAL_SECONDS:"),
    ("one more fetch failure allowed", "if failures < MAX_FETCH_FAILURES:", "if failures <= MAX_FETCH_FAILURES:"),
    ("validator ignores the threshold side", "return (len(leader_quotes) >= min_items) == (len(mine[\"quotes\"]) >= min_items)", "return True"),
    ("validator skips quote grounding", "if _ground_quotes(leader_quotes, mine_snapshot, want) != leader_quotes:", "if False:"),
    ("window dates not enforced", "if day is None or day < first_day or day > last_day:", "if day is None:"),
    ("overlapping quotes allowed", "if any(start < other_end and other_start < end for other_start, other_end in spans):", "if False:"),
    ("appeal window off by one", "if now >= int(pledge.appeal_deadline):", "if now > int(pledge.appeal_deadline):"),
    ("finalize off by one", "if now < int(pledge.appeal_deadline):\n            raise gl.vm.UserError(\"[EXPECTED] the appeal window is still open\")", "if now <= int(pledge.appeal_deadline):\n            raise gl.vm.UserError(\"[EXPECTED] the appeal window is still open\")"),
    ("appeal fee not burned", "self.burned_total = u256(int(self.burned_total) + APPEAL_FEE)", "self.burned_total = u256(int(self.burned_total))"),
    ("abort one second early", "if now < opens + STALL_GRACE_SECONDS:", "if now < opens + STALL_GRACE_SECONDS - 1:"),
    ("streak survives a broken pledge", "owner.streak = u256(0)\n            self.n_broken", "owner.streak = u256(int(owner.streak))\n            self.n_broken"),
    ("equal split instead of pro rata", "shares = [(compensation * amount) // pool for amount in amounts]", "shares = [compensation // len(amounts) for amount in amounts]"),
    ("trust score without smoothing", "return ((kept + 1) * BPS_SCALE) // (kept + broken + 2)", "return (kept * BPS_SCALE) // (kept + broken + 1)"),
    ("owner not paid the sponsor pool", "owner.balance = u256(int(owner.balance) + stake + pool)", "owner.balance = u256(int(owner.balance) + stake)"),
    ("anyone can appeal", "if sender != pledge.owner:\n            raise gl.vm.UserError(\"[EXPECTED] only the owner can appeal\")", "if False:\n            raise gl.vm.UserError(\"[EXPECTED] only the owner can appeal\")"),
    ("validator believes any fetch failure", "return leader_data.get(\"reason\") == \"fetch\" and mine_snapshot is None", "return leader_data.get(\"reason\") == \"fetch\""),
    ("validator tolerates unreadable page on success", "if mine_snapshot is None:\n                return False\n            leader_quotes", "leader_quotes"),
    ("fetch counter never resets", "pledge.fetch_failures = u256(0)\n        pledge.last_attempt_at = u256(0)\n        if outcome == C_MET:", "pledge.last_attempt_at = u256(0)\n        if outcome == C_MET:"),
    ("appeal needs balance > fee", "if int(profile.balance) < APPEAL_FEE:", "if int(profile.balance) <= APPEAL_FEE:"),
    ("active cap off by one", "if int(profile.active_pledges) >= MAX_ACTIVE_PLEDGES_PER_OWNER:", "if int(profile.active_pledges) > MAX_ACTIVE_PLEDGES_PER_OWNER:"),
    ("sponsor cap off by one", "if int(pledge.sponsor_count) >= MAX_SPONSORS_PER_PLEDGE:", "if int(pledge.sponsor_count) > MAX_SPONSORS_PER_PLEDGE:"),
    ("owner may sponsor own pledge", "if sender == pledge.owner:\n            raise gl.vm.UserError(\"[EXPECTED] the owner cannot sponsor", "if False:\n            raise gl.vm.UserError(\"[EXPECTED] the owner cannot sponsor"),
    ("locked total drifts", "self.locked_total = u256(int(self.locked_total) - stake - pool)", "self.locked_total = u256(int(self.locked_total) - stake)"),
    ("prompt delimiters not neutralised", "return text.replace(\"<<<\", \"\\u2039\\u2039\\u2039\").replace(\">>>\", \"\\u203a\\u203a\\u203a\")", "return text"),
    ("short quotes accepted", "if len(quote) < MIN_QUOTE_CHARS:\n            continue\n        start", "if len(quote) < 1:\n            continue\n        start"),
    ("evidence hash not validated", "if not _is_hex64(evidence_hash):", "if False:"),
    ("second breach can be appealed again", "if int(pledge.appeals_used) == 0:\n                pledge.status = P_BREACHED", "if True:\n                pledge.status = P_BREACHED"),
    ("won appeal leaves pledge breached", "pledge.status = P_ACTIVE\n            pledge.appeal_deadline = u256(0)\n            AppealResolved", "pledge.appeal_deadline = u256(0)\n            AppealResolved"),
    ("won appeal keeps the miss", "pledge.missed = u256(int(pledge.missed) - 1)", "pledge.missed = u256(int(pledge.missed))"),
    ("cancel allowed at start", "if now >= int(pledge.start_at):\n            raise gl.vm.UserError(\"[EXPECTED] a started", "if now > int(pledge.start_at):\n            raise gl.vm.UserError(\"[EXPECTED] a started"),
    ("window end day off by one", "last_day = _day_of(window_end - 1)", "last_day = _day_of(window_end)"),
    ("unreachable page counts as met", "            met = False\n            reason = R_UNREACHABLE", "            met = True\n            reason = R_UNREACHABLE"),
    ("quote cap ignores min_items", "return min(MAX_QUOTES, min_items + 1)", "return MAX_QUOTES"),
    ("appeal fee not charged", "profile.balance = u256(int(profile.balance) - APPEAL_FEE)", "profile.balance = u256(int(profile.balance))"),
    ("refund skipped on cancel", "            owner.balance = u256(int(owner.balance) + stake)\n            shares = [0 for _ in amounts]\n            if status == P_CANCELLED:", "            shares = [0 for _ in amounts]\n            if status == P_CANCELLED:"),
]


def run(index: int):
    label, old, new = MUTANTS[index]
    if SRC.count(old) != 1:
        return index, label, "BAD-MUTANT (pattern found %d times)" % SRC.count(old)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kept_word.py"
        path.write_text(SRC.replace(old, new))
        env = dict(os.environ, KW_CONTRACT=str(path), KW_SEEDS="6")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-f", "-s", str(ROOT / "tests"), "-p", "test_*.py"],
            capture_output=True, text=True, env=env, cwd=ROOT / "tests",
        )
    return index, label, "killed" if proc.returncode != 0 else "SURVIVED"


def main() -> int:
    wanted = [int(a) for a in sys.argv[1:]] or list(range(len(MUTANTS)))
    survived = 0
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for index, label, verdict in pool.map(run, wanted):
            print(f"[{index:02d}] {verdict:9s} {label}", flush=True)
            survived += verdict != "killed"
    print(f"\n{len(wanted) - survived}/{len(wanted)} mutants killed")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
