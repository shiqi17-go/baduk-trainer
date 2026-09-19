"""Two things the earlier benchmark could not answer:
1. Is the one-off OpenCL autotuning cached, so the next start is fast?
2. Do the two nets actually disagree anywhere? The quiet opening agreed exactly,
   which proves nothing -- try a real, sharp middlegame from a game record.
"""
import os
import re
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis  # noqa: E402
from katago_engine import ENGINE_DIR, MODELS_DIR  # noqa: E402
from study import parse_sgf_mainline  # noqa: E402

SMALL = os.path.join(MODELS_DIR, "b10c384h6nbttflrs.bin")
STRONG = os.path.join(MODELS_DIR, "kata1-tf3-b11c768-s11750M-d6216M.bin")
GTP = "ABCDEFGHJKLMNOPQRSTUVWXYZ"

print("=" * 72)
print("1) is the autotuning cache reusable?")
print("=" * 72)
tune_dir = os.path.join(ENGINE_DIR, "KataGoData", "opencltuning")
if os.path.isdir(tune_dir):
    for f in sorted(os.listdir(tune_dir)):
        print(f"  {f}  ({os.path.getsize(os.path.join(tune_dir, f))/1024:.0f} KB)")
else:
    print("  no tuning dir found at", tune_dir)

print()
print("  starting STRONG again and timing it ...")
a = KataGoAnalysis(model=STRONG)
t0 = time.time()
a.start(timeout=2400)
print(f"  second start: {time.time()-t0:.1f}s")

# ---------------------------------------------------------------- positions
sgf = r"F:\harness\baduk-trainer\samples\demo1.sgf"
moves_all = parse_sgf_mainline(sgf)
moves = []
for color, pos in moves_all:
    if pos is None:
        continue
    x, y = pos
    moves.append([color, f"{GTP[x]}{19-y}"])
print()
print(f"  game record: {len(moves)} moves")

CUTS = [40, 70, 100, 130]
CUTS = [c for c in CUTS if c < len(moves)]

print()
print("=" * 72)
print("2) do the nets disagree on real middlegame positions?")
print("=" * 72)
print(f"{'turn':>5} {'visits':>7} {'small best':>11} {'lead':>7} "
      f"{'strong best':>12} {'lead':>7} {'agree?':>7}")
for turn in CUTS:
    row = []
    for model in (SMALL, STRONG):
        b = KataGoAnalysis(model=model) if model is SMALL else a
        if model is SMALL:
            b.start(timeout=900)
        q = {"moves": moves, "rules": "chinese", "komi": 7.5,
             "boardXSize": 19, "boardYSize": 19,
             "analyzeTurns": [turn], "maxVisits": 300}
        r = b.query(q, expect_turns=1, timeout=900)
        mi = (r.get("moveInfos") or [{}])[0]
        row.append((mi.get("move"), mi.get("scoreLead")))
        if model is SMALL:
            b.close()
    (m1, l1), (m2, l2) = row
    same = "yes" if m1 == m2 else "NO"
    print(f"{turn:>5} {300:>7} {str(m1):>11} {l1:>7.1f} {str(m2):>12} {l2:>7.1f} "
          f"{same:>7}")
    sys.stdout.flush()

a.close()
print()
print("DONE")
