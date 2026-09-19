"""Latency probe: how fast can we answer "where is best here?" after a stone
is placed? Only measures; builds nothing.

Also checks whether the engine streams partial results during a search
(isDuringSearch), which would let the UI refine the answer progressively.
"""
import os
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis  # noqa: E402

# a plausible middlegame position
MOVES = [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
         ["B", "R14"], ["W", "C6"], ["B", "K16"], ["W", "K4"],
         ["B", "F17"], ["W", "C14"], ["B", "R6"], ["W", "O3"],
         ["B", "D10"], ["W", "J10"], ["B", "Q10"], ["W", "F3"]]

a = KataGoAnalysis()
a.start()
print("engine:", a.version)
print()
print(f"{'visits':>7} {'human分析':>10} {'耗时(秒)':>10} {'候选数':>7}  isDuringSearch")
print("-" * 60)

for visits in (50, 100, 200, 400):
    for human in (False, True):
        q = {
            "moves": MOVES, "rules": "chinese", "komi": 7.5,
            "boardXSize": 19, "boardYSize": 19,
            "analyzeTurns": [len(MOVES)], "maxVisits": visits,
            "includePolicy": True, "includeOwnership": True,
        }
        if human:
            q["overrideSettings"] = {
                "humanSLProfile": "preaz_10k",
                "ignorePreRootHistory": False,
                "humanSLRootExploreProbWeightless": 0.5,
                "humanSLCpuctPermanent": 2.0,
                "rootNumSymmetriesToSample": 2,
            }
        t0 = time.time()
        r = a.query(q, expect_turns=1, timeout=600)
        dt = time.time() - t0
        mis = r.get("moveInfos", []) or []
        best = mis[0]["move"] if mis else "-"
        lead = mis[0].get("scoreLead") if mis else None
        print(f"{visits:>7} {str(human):>10} {dt:>10.2f} {len(mis):>7}  "
              f"{r.get('isDuringSearch')}   best={best} lead={lead:+.1f}"
              if lead is not None else
              f"{visits:>7} {str(human):>10} {dt:>10.2f} {len(mis):>7}  "
              f"{r.get('isDuringSearch')}")
        sys.stdout.flush()

a.close()
print()
print("DONE")
