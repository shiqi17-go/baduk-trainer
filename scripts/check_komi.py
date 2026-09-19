"""Is the per-query komi actually applied? And how does the net calibrate?"""
import sys

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis  # noqa: E402

a = KataGoAnalysis()
a.start()
print("version:", a.version)
print()
print(f"{'komi':>6} {'winrate(black)':>15} {'scoreLead(black)':>17}")
for komi in [0.5, 6.5, 7.5, 15.0, 30.0]:
    r = a.query({
        "moves": [], "rules": "chinese", "komi": komi,
        "boardXSize": 19, "boardYSize": 19,
        "analyzeTurns": [0], "maxVisits": 120,
    }, expect_turns=1)
    ri = r["rootInfo"]
    print(f"{komi:>6} {ri['winrate']:>15.4f} {ri['scoreLead']:>17.2f}")

print()
print("If winrate/scoreLead move strongly with komi, the query field is honoured.")
print()
print("--- a clearly decided position, both conventions ---")
moves = [["B", "Q16"], ["W", "A1"], ["B", "Q4"], ["W", "A19"],
         ["B", "D16"], ["W", "T1"], ["B", "D4"], ["W", "T19"]]
for turn in (8, 9):
    r = a.query({
        "moves": moves, "rules": "chinese", "komi": 7.5,
        "boardXSize": 19, "boardYSize": 19,
        "analyzeTurns": [turn], "maxVisits": 120,
    }, expect_turns=1)
    ri = r["rootInfo"]
    print(f"  turn {turn}: currentPlayer={ri['currentPlayer']} "
          f"winrate={ri['winrate']:.4f} scoreLead={ri['scoreLead']:+.2f}  "
          f"(black is far ahead here)")
a.close()
