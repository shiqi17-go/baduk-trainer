"""Reproduce the exact user flow: store a game via the play tab's button, land
on the trainer tab, analysis runs, and the problem list fills.

Waits for the LISTBOX to fill -- the actual thing the user sees -- not the DB.
"""
import os
import sys
import time
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
import main as mainmod  # noqa: E402
from goban import BLACK, WHITE  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if extra else ""),
          flush=True)
    if not cond:
        fails.append(name)


def pump(win, seconds):
    t0 = time.time()
    while time.time() - t0 < seconds:
        win.update()
        time.sleep(0.04)


win = mainmod.MainApp()
win.update()

try:
    for g in win.trainer.library.games():
        win.trainer.library.delete_game(g.id)
except Exception:
    pass

try:
    print("=== play a short game with a huge blunder ===")
    g = win.game
    # a normal opening then A1 -- unmistakable
    for color, x, y in [(BLACK, 15, 3), (WHITE, 3, 15), (BLACK, 15, 15),
                        (WHITE, 3, 3), (BLACK, 0, 0), (WHITE, 15, 9),
                        (BLACK, 1, 0), (WHITE, 9, 9), (BLACK, 8, 3),
                        (WHITE, 3, 8)]:
        g.board.goban.play(color, x, y)
    print("  moves:", g.board.goban.move_number())

    print()
    print("=== click 存入错题本 and watch the trainer tab ===")
    t0 = time.time()
    last = 0.0
    g.save_to_trainer()
    check("switched to the trainer tab", win.notebook.index("current") == 2,
          str(win.notebook.index("current")))
    t = win.trainer

    done = False
    while time.time() - t0 < 900:
        win.update()
        time.sleep(0.4)
        if time.time() - last > 3:
            last = time.time()
            print(f"  {time.time()-t0:6.0f}s  题目列表={t.prob_list.size()}  "
                  f"status='{t.status_var.get()}'", flush=True)
        if t.prob_list.size() > 0:
            done = True
            break

    print()
    print("=== result ===")
    check("problems appeared in the list", t.prob_list.size() > 0,
          str(t.prob_list.size()))
    st = t.library.stats()
    print("  library stats:", st)
    print("  list contents:")
    for i in range(t.prob_list.size()):
        print("   ", t.prob_list.get(i))

    # click the first problem and see if the board shows it
    if t.prob_list.size() > 0:
        t.prob_list.selection_set(0)
        t._on_pick_problem()
        win.update()
        stones = sum(1 for row in t.board.grid for v in row if v != 0)
        check("clicking a problem shows the position", stones > 0, str(stones))
        check("prompt asks the student", "你想下哪" in t.prompt.cget("text"),
              t.prompt.cget("text"))

except Exception:
    traceback.print_exc()
    fails.append("exception during test")

finally:
    try:
        for g in win.trainer.library.games():
            win.trainer.library.delete_game(g.id)
        win._on_close()
    except Exception:
        pass

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("REAL-FLOW TEST PASSED")
