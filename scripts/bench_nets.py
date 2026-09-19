"""Benchmark the strong net against the small one.

The b10 net was chosen for speed when the app simulated weak ranks. A 768-channel
transformer is much stronger but OpenCL is known to handle transformers poorly,
so measure before committing to a design.
"""
import os
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis  # noqa: E402
from katago_engine import MODELS_DIR, human_model_path  # noqa: E402

SMALL = os.path.join(MODELS_DIR, "b10c384h6nbttflrs.bin")
STRONG = os.path.join(MODELS_DIR, "kata1-tf3-b11c768-s11750M-d6216M.bin")

MOVES = [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
         ["B", "R14"], ["W", "C6"], ["B", "K16"], ["W", "K4"],
         ["B", "F17"], ["W", "C14"], ["B", "R6"], ["W", "O3"]]


def bench(model: str, label: str):
    print("=" * 72)
    print(f"{label}: {os.path.basename(model)} ({os.path.getsize(model)/1e6:.1f} MB)")
    print("=" * 72)
    a = KataGoAnalysis(model=model)
    t0 = time.time()
    try:
        # A net architecture that has never run on this GPU needs OpenCL
        # autotuning first, which can take several minutes. It is cached after
        # that, so this cost is paid once per (architecture, GPU) pair.
        a.start(timeout=2400)
    except Exception as e:
        print(f"  START FAILED: {type(e).__name__}: {e}")
        print("  stderr tail:", a.stderr_tail(8))
        return None
    print(f"  engine start : {time.time()-t0:.1f}s  (version {a.version})")

    out = {}
    for visits in (50, 150, 300, 800):
        q = {
            "moves": MOVES, "rules": "chinese", "komi": 7.5,
            "boardXSize": 19, "boardYSize": 19,
            "analyzeTurns": [len(MOVES)], "maxVisits": visits,
        }
        t = time.time()
        try:
            r = a.query(q, expect_turns=1, timeout=900)
        except Exception as e:
            print(f"  visits {visits:4d}: FAILED {type(e).__name__}: {str(e)[:70]}")
            continue
        dt = time.time() - t
        mi = (r.get("moveInfos") or [{}])[0]
        out[visits] = (dt, mi.get("move"), mi.get("scoreLead"))
        print(f"  visits {visits:4d}: {dt:6.2f}s   best={mi.get('move'):<5} "
              f"lead={mi.get('scoreLead')}")
        sys.stdout.flush()
    a.close()
    return out


small = bench(SMALL, "SMALL (shipped)")
print()
strong = bench(STRONG, "STRONG (new)")

print()
print("=" * 72)
print("SPEED COMPARISON (same position)")
print("=" * 72)
print(f"{'visits':>7} {'small':>9} {'strong':>9} {'slowdown':>10}")
if small and strong:
    for v in sorted(set(small) & set(strong)):
        s, st = small[v][0], strong[v][0]
        print(f"{v:>7} {s:>8.2f}s {st:>8.2f}s {st/max(s,1e-6):>9.1f}x")

print()
print("=" * 72)
print("EVALUATION COMPARISON (does the bigger net disagree?)")
print("=" * 72)
if small and strong:
    for v in sorted(set(small) & set(strong)):
        s, st = small[v], strong[v]
        print(f"  visits {v:4d}: small {s[1]:<5} {s[2]:+.1f}   "
              f"strong {st[1]:<5} {st[2]:+.1f}   "
              f"diff {abs((s[2] or 0)-(st[2] or 0)):.1f} pts")
print()
print("DONE")
