"""An independent reference model of KeptWord, written from the README rules only.

It shares no code with the contract. test_property.py drives both with the same random
operations and demands identical outcomes (success vs revert) and identical state.
"""
from __future__ import annotations

GRANT, APPEAL_FEE, APPEAL_WINDOW, STALL = 1000, 10, 6 * 3600, 7 * 86400


class Ref:
    def __init__(self) -> None:
        self.m: dict = {}
        self.p: dict = {}
        self.burned = 0
        self.granted = 0

    # ------------------------------------------------------------ helpers
    def _grace(self, interval: int) -> int:
        return min(interval // 4, 6 * 3600)

    def opens(self, p: dict, idx: int) -> int:
        return p["start"] + (idx + 1) * p["interval"] + self._grace(p["interval"])

    def _close(self, p: dict, status: str, now: int) -> None:
        owner = self.m[p["owner"]]
        pool = sum(a for _, a in p["spons"])
        owner["locked"] -= p["stake"]
        owner["active"] -= 1
        if status == "KEPT":
            owner["balance"] += p["stake"] + pool
            owner["kept"] += 1
            owner["streak"] += 1
            owner["best"] = max(owner["best"], owner["streak"])
            for who, a in p["spons"]:
                self.m[who]["locked"] -= a
                self.m[who]["s_kept"] += 1
        elif status == "BROKEN":
            owner["broken"] += 1
            owner["streak"] = 0
            comp = p["stake"] * 50 // 100
            paid = 0
            for who, a in p["spons"]:
                share = comp * a // pool
                paid += share
                self.m[who]["locked"] -= a
                self.m[who]["balance"] += a + share
                self.m[who]["s_broken"] += 1
            self.burned += p["stake"] - paid
        else:
            owner["balance"] += p["stake"]
            for who, a in p["spons"]:
                self.m[who]["locked"] -= a
                self.m[who]["balance"] += a
        p["status"] = status

    def _after(self, p: dict, now: int) -> None:
        allowed = p["cp"] - p["min_met"]
        if p["missed"] > allowed:
            if p["appeals"] == 0:
                p["status"] = "BREACHED"
                p["deadline"] = now + APPEAL_WINDOW
            else:
                self._close(p, "BROKEN", now)
        elif p["checked"] >= p["cp"]:
            self._close(p, "KEPT", now)

    def _record(self, p: dict, met: bool) -> None:
        p["results"].append("MET" if met else "MISSED")
        p["checked"] += 1
        p["fails"] = 0
        p["last"] = 0
        owner = self.m[p["owner"]]
        if met:
            p["met"] += 1
            owner["met"] += 1
        else:
            p["missed"] += 1
            owner["missed"] += 1

    # ------------------------------------------------------------ actions (True = ok, False = revert)
    def claim(self, who) -> bool:
        if who in self.m:
            return False
        self.m[who] = dict(balance=GRANT, locked=0, active=0, kept=0, broken=0, met=0, missed=0,
                           streak=0, best=0, s_kept=0, s_broken=0)
        self.granted += GRANT
        return True

    def open(self, who, now, start, interval, cp, min_met, items, stake, text_ok, url_ok, page_ok) -> bool:
        if not text_ok or not url_ok:
            return False
        if start < now + 300 or start > now + 90 * 86400:
            return False
        if not (600 <= interval <= 30 * 86400) or not (1 <= cp <= 6):
            return False
        if not (1 <= min_met <= cp) or not (1 <= items <= 5) or not (20 <= stake <= 500):
            return False
        if who not in self.m or self.m[who]["balance"] < stake or self.m[who]["active"] >= 5:
            return False
        if not page_ok:
            return False
        pid = len(self.p) + 1
        self.p[pid] = dict(id=pid, owner=who, start=start, interval=interval, cp=cp, min_met=min_met,
                           items=items, stake=stake, status="ACTIVE", checked=0, met=0, missed=0,
                           fails=0, last=0, spons=[], appeals=0, deadline=0, results=[])
        self.m[who]["balance"] -= stake
        self.m[who]["locked"] += stake
        self.m[who]["active"] += 1
        return True

    def sponsor(self, who, pid, amount, now) -> bool:
        p = self.p.get(pid)
        if p is None or p["status"] != "ACTIVE" or now >= p["start"] or who == p["owner"]:
            return False
        if not (5 <= amount <= 500) or who not in self.m or self.m[who]["balance"] < amount:
            return False
        if any(w == who for w, _ in p["spons"]) or len(p["spons"]) >= 20:
            return False
        p["spons"].append((who, amount))
        self.m[who]["balance"] -= amount
        self.m[who]["locked"] += amount
        return True

    def cancel(self, who, pid, now) -> bool:
        p = self.p.get(pid)
        if p is None or who != p["owner"] or p["status"] != "ACTIVE" or now >= p["start"]:
            return False
        self._close(p, "CANCELLED", now)
        return True

    def verify(self, pid, now, page) -> bool:
        """page: None when unreachable, else the number of in-window entries."""
        p = self.p.get(pid)
        if p is None or p["status"] != "ACTIVE" or now < self.opens(p, p["checked"]):
            return False
        if p["last"] and now - p["last"] < 300:
            return False
        if page is None:
            if p["fails"] + 1 < 3:
                p["fails"] += 1
                p["last"] = now
                return True
            met = False
        else:
            met = page >= p["items"]
        self._record(p, met)
        self._after(p, now)
        return True

    def appeal(self, who, pid, now, page) -> bool:
        p = self.p.get(pid)
        if p is None or who != p["owner"] or p["status"] != "BREACHED" or p["appeals"] >= 1:
            return False
        if now >= p["deadline"] or self.m[who]["balance"] < APPEAL_FEE:
            return False
        if page is None:
            return False
        met = page >= p["items"]
        self.m[who]["balance"] -= APPEAL_FEE
        self.burned += APPEAL_FEE
        p["appeals"] = 1
        if met:
            idx = p["checked"] - 1
            p["results"][idx] = "MET"
            p["met"] += 1
            p["missed"] -= 1
            self.m[who]["met"] += 1
            self.m[who]["missed"] -= 1
            p["status"] = "ACTIVE"
            p["deadline"] = 0
            self._after(p, now)
        else:
            self._close(p, "BROKEN", now)
        return True

    def finalize(self, pid, now) -> bool:
        p = self.p.get(pid)
        if p is None or p["status"] != "BREACHED" or now < p["deadline"]:
            return False
        self._close(p, "BROKEN", now)
        return True

    def abort(self, pid, now) -> bool:
        p = self.p.get(pid)
        if p is None or p["status"] != "ACTIVE" or now < self.opens(p, p["checked"]) + STALL:
            return False
        self._close(p, "ABORTED", now)
        return True
