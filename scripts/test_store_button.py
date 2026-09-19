"""Reproduce the user's report: store a game from the play tab and see what the
错题本 shows. Uses a deliberately weak game so there SHOULD be problems."""
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


def pump_until(win, pred, timeout, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        win.update()
        if pred():
            return True, time.time() - t0
        time.sleep(0.05)
    print(f"    (timed out waiting for {label} after {timeout}s)")
    return False, time.time() - t0


win = mainmod.MainApp()
win.update()

# clean library
try:
    for g in win.trainer.library.games():
        win.trainer.library.delete_game(g.id)
except Exception:
    pass

try:
    # ------------------------------------------------------ build a game
    print("=== set up a game where black blunders at A1 ===")
    g = win.game
    # play by hand onto the board -- no engine, we only need the move list
    for color, x, y in [(BLACK, 15, 3), (WHITE, 3, 15), (BLACK, 15, 15),
                        (WHITE, 3, 3), (BLACK, 0, 0),      # A1 -- huge blunder
                        (WHITE, 15, 9), (BLACK, 0, 1),     # another blunder
                        (WHITE, 9, 9)]:
        g.board.goban.play(color, x, y)
    print("  moves on board:", g.board.goban.move_number())

    # --------------------------------------------------- store via the button
    print()
    print("=== click 存入错题本 (play tab) ===")
    def st_done(t, baseline):
        return t.library.stats()["analyzed"] > baseline
    g.save_to_trainer()
    pump(win, 0.5)
    check("switched to the trainer tab", win.notebook.index("current") == 2,
          str(win.notebook.index("current")))
    print("  status:", win.trainer.status_var.get())

    # --------------------------------------------------- watch the analysis
    print()
    print("=== watch the analysis run ===")
    t = win.trainer
    t0 = time.time()
    last = 0.0
    baseline = t.library.stats()["analyzed"]
    while time.time() - t0 < 600:
        win.update()
        time.sleep(0.4)
        if time.time() - last > 3:
            last = time.time()
            st = t.library.stats()
            print(f"  {time.time()-t0:6.0f}s  analysed={st['analyzed']} "
                  f"problems={st['problems']}  status='{t.status_var.get()}'",
                  flush=True)
        if st_done(t, baseline):
            break

    st = t.library.stats()
    print()
    print("=== result ===")
    print("  stats:", st)
    check("analysis produced problems", st["problems"] > 0, str(st["problems"]))

    rows = t.library.problems()
    for r in rows:
        print(f"    第{r['move_no']}手 {r['colour']} {r['played']} "
              f"-> AI 推荐 {r['best']}  亏{r['loss']}目  [{r['level']}]")

    check("problems appear in the list", t.prob_list.size() > 0,
          str(t.prob_list.size()))

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
print("REPRODUCTION TEST DONE")
