"""Hold a KataGo CUDA engine open so VRAM use can be measured from outside."""
import subprocess
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from analysis import KataGoAnalysis


def vram():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total",
         "--format=csv,noheader"],
        capture_output=True, text=True)
    return out.stdout.strip()


print("baseline (no engine):", vram(), flush=True)
a = KataGoAnalysis()
a.start()
print("engine started, version", a.version, flush=True)

# warm it up so kernels and caches are allocated
pos = [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"]]
for v in (100, 800):
    a.query({"moves": pos, "rules": "chinese", "komi": 7.5,
             "boardXSize": 19, "boardYSize": 19,
             "analyzeTurns": [len(pos)], "maxVisits": v}, expect_turns=1)
    print(f"  after {v} visits: {vram()}", flush=True)

print("HOLDING for 40s ...", flush=True)
time.sleep(40)
print("with engine loaded:", vram(), flush=True)
a.close()
time.sleep(3)
print("after close:", vram(), flush=True)
