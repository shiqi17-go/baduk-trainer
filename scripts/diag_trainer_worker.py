"""Diagnose why the trainer's analysis worker never finishes."""
import os
import queue
import sys
import time

sys.path.insert(0, r"F:\harness\baduk-trainer")
from library import Library  # noqa: E402
from trainer import TrainerWorker  # noqa: E402

lib = Library(r"F:\harness\baduk-trainer\library_test.db")
inbox: queue.Queue = queue.Queue()
outbox: queue.Queue = queue.Queue()
w = TrainerWorker(inbox, outbox)
w.start()

# a SHORT game so the analysis should be quick if the engine starts at all
gid = lib.add_game([["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
                    ["B", "R14"], ["W", "C6"], ["B", "K16"], ["W", "K4"]],
                   name="小局")
print("game id:", gid)
inbox.put({"action": "analyze", "library": lib, "game_id": gid,
           "moves": [["B", "Q16"], ["W", "D4"], ["B", "Q4"], ["W", "D16"],
                     ["B", "R14"], ["W", "C6"], ["B", "K16"], ["W", "K4"]],
           "colour": "B", "visits": 60})

t0 = time.time()
last = time.time()
while time.time() - t0 < 240:
    try:
        msg = outbox.get(timeout=5.0)
    except queue.Empty:
        if time.time() - last > 5:
            print(f"  ... {time.time()-t0:.0f}s, engine alive="
                  f"{bool(w.engine and w.engine.alive)}")
            last = time.time()
        continue
    last = time.time()
    print(f"[{time.time()-t0:6.1f}s] {msg['kind']}: "
          f"{msg.get('text', msg.get('problems', ''))}")
    if msg["kind"] in ("game_done", "error"):
        break

inbox.put({"action": "quit"})
time.sleep(0.3)
lib.close()
if os.path.exists(r"F:\harness\baduk-trainer\library_test.db"):
    os.remove(r"F:\harness\baduk-trainer\library_test.db")
print("DONE")
