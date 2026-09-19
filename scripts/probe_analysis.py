"""Decisive probes:
1. Is scoreLead / winrate from BLACK's view or the side-to-move's view?
2. Do humanPolicy / humanPrior appear when humanSLProfile + includePolicy are set?
3. How large is the surprise signal at a move a weak human would actually play?
"""
import json
import os
import sys

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis, HUMAN_ANALYSIS_OVERRIDES  # noqa: E402

# White wastes two moves on the 1-1 points, so BLACK is clearly ahead, and we
# stop with WHITE to move. That makes the two perspectives give opposite signs.
moves = [["B", "Q16"], ["W", "A1"], ["B", "Q4"], ["W", "A19"], ["B", "D16"]]
# after 5 moves it is White's turn (turn index 5)

a = KataGoAnalysis()
a.start()
print("version:", a.version)
print()

print("=" * 74)
print("PROBE 1: perspective, black clearly ahead, WHITE to move")
print("=" * 74)
r = a.query({
    "moves": moves, "rules": "chinese", "komi": 7.5,
    "boardXSize": 19, "boardYSize": 19,
    "analyzeTurns": [5], "maxVisits": 30,
}, expect_turns=1)
ri = r["rootInfo"]
print(f"  currentPlayer = {ri['currentPlayer']}")
print(f"  winrate       = {ri['winrate']:.4f}")
print(f"  scoreLead     = {ri['scoreLead']:.2f}")
print("  -> if winrate>0.5 and scoreLead>0 : BLACK's perspective")
print("  -> if winrate<0.5 and scoreLead<0 : side-to-move (White) perspective")

print()
print("=" * 74)
print("PROBE 2: human policy fields with humanSLProfile set")
print("=" * 74)
q = {
    "moves": moves, "rules": "chinese", "komi": 7.5,
    "boardXSize": 19, "boardYSize": 19,
    "analyzeTurns": [3], "maxVisits": 60,
    "includePolicy": True, "includeOwnership": True,
    "overrideSettings": {"humanSLProfile": "preaz_10k", **HUMAN_ANALYSIS_OVERRIDES},
}
r2 = a.query(q, expect_turns=1)
print("  top-level keys:", sorted(k for k in r2.keys() if k != "moveInfos"))
print("  has humanPolicy:", "humanPolicy" in r2)
print("  has policy      :", "policy" in r2)
if "humanPolicy" in r2:
    hp = r2["humanPolicy"]
    print(f"  humanPolicy len={len(hp)} sum={sum(v for v in hp if v > 0):.4f} "
          f"pass={hp[-1]:.5f}")
    top = sorted(((v, i) for i, v in enumerate(hp) if v > 0), reverse=True)[:6]
    from goban import GTP_LETTERS
    for v, i in top:
        if i == 361:
            print(f"    pass      {v:.4f}")
        else:
            x, y = i % 19, i // 19
            print(f"    {GTP_LETTERS[x]}{19-y:<3} {v:.4f}")
print("  moveInfo[0] keys:", sorted(r2["moveInfos"][0].keys()))
print("  has humanPrior in moveInfos:",
      "humanPrior" in r2["moveInfos"][0])
if "humanPrior" in r2["moveInfos"][0]:
    for mi in r2["moveInfos"][:6]:
        print(f"    {mi['move']:<6} visits={mi['visits']:<5} "
              f"prior={mi.get('prior', 0):.4f} humanPrior={mi.get('humanPrior', 0):.4f} "
              f"scoreLead={mi.get('scoreLead', 0):+.2f}")

print()
print("=" * 74)
print("PROBE 3: does the human policy differ a lot between ranks?")
print("=" * 74)
pos = [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"], ["B", "R14"]]
for prof in ["preaz_20k", "preaz_10k", "preaz_5k", "rank_3d"]:
    qq = {
        "moves": pos, "rules": "chinese", "komi": 7.5,
        "boardXSize": 19, "boardYSize": 19,
        "analyzeTurns": [5], "maxVisits": 20,
        "includePolicy": True,
        "overrideSettings": {"humanSLProfile": prof, **HUMAN_ANALYSIS_OVERRIDES},
    }
    rr = a.query(qq, expect_turns=1)
    hp = rr.get("humanPolicy")
    if not hp:
        print(f"  {prof:>10}: no humanPolicy")
        continue
    top = sorted(((v, i) for i, v in enumerate(hp) if v > 0), reverse=True)[:3]
    from goban import GTP_LETTERS
    txt = []
    for v, i in top:
        if i == 361:
            txt.append(f"pass {v:.2f}")
        else:
            x, y = i % 19, i // 19
            txt.append(f"{GTP_LETTERS[x]}{19-y} {v:.2f}")
    ent = -sum(p * __import__("math").log2(p) for p in hp if p > 1e-9)
    print(f"  {prof:>10}: entropy={ent:.2f} bits   top3: {', '.join(txt)}")

a.close()
print()
print("PROBES DONE")
