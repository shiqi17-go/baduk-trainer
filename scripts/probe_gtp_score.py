"""Work out the best way to get a real position assessment out of the GTP
engine for the play mode, replacing the naive area count.

Two candidates:
  kata-raw-nn  -- single response, raw net evaluation, gives whiteLead
  kata-analyze -- streamed search output, more accurate but may not terminate
                  the GTP response on its own
"""
import re
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from katago_engine import KataGoEngine

FAST = {"delayMoveScale": "0.0", "delayMoveMax": "0.0",
        "logToStderr": "false", "logSearchInfo": "false"}

eng = KataGoEngine(profile="rank_3d", visits=40, rules="chinese",
                   extra_overrides=FAST)
eng.start()
print("engine:", eng.version)
for mv in [("B", "Q16"), ("W", "D4"), ("B", "Q4"), ("W", "D16"), ("B", "R14")]:
    eng.play(*mv)

print()
print("=" * 66)
print("A) kata-raw-nn 0")
print("=" * 66)
t0 = time.time()
try:
    out = eng._cmd("kata-raw-nn 0", timeout=30)
    dt = time.time() - t0
    print(f"  took {dt:.2f}s, {len(out)} chars")
    m = re.search(r"whiteLead\s+(-?[\d.]+)", out)
    wl = float(m.group(1)) if m else None
    print(f"  whiteLead = {wl}   -> 黑领先 {-(wl or 0):.1f} 目")
    print("  first 200 chars:", out[:200])
except Exception as e:
    print("  FAILED:", type(e).__name__, str(e)[:120])

print()
print("=" * 66)
print("B) kata-analyze B 200  (interval 200cs = 2s)")
print("=" * 66)
t0 = time.time()
try:
    out = eng._cmd("kata-analyze B 200", timeout=45)
    dt = time.time() - t0
    print(f"  took {dt:.2f}s, {len(out)} chars")
    ri = re.search(r"rootInfo.*?scoreLead\s+(-?[\d.]+)", out)
    print("  rootInfo scoreLead =", ri.group(1) if ri else "(not found)")
    print("  tail:", out[-260:])
except Exception as e:
    print("  FAILED:", type(e).__name__, str(e)[:160])
    print("  (this is the 'streams forever' case)")

eng.close()
print()
print("DONE")
