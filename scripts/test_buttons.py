"""Test the two '存入错题本' buttons (game tab + study tab) reach the library."""
import os
import sys
import time
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
import main as mainmod  # noqa: E402
from goban import BLACK, WHITE  # noqa: E402
from library import Library  # noqa: E402

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

# clean library
try:
    for g in win.trainer.library.games():
        win.trainer.library.delete_game(g.id)
except Exception:
    pass

try:
    print("=== buttons exist ===")
    check("game tab has save_to_trainer", hasattr(win.game, "save_to_trainer"))
    check("study tab has save_to_trainer", hasattr(win.study, "save_to_trainer"))

    print()
    print("=== from the play tab ===")
    g = win.game
    # give the game board a couple of moves without starting the engine
    g.board.goban.play(BLACK, 15, 3)
    g.board.goban.play(WHITE, 3, 15)
    lib = Library(); n0 = lib.stats()["games"]; lib.close()
    g.save_to_trainer()
    pump(win, 0.6)
    lib = Library(); n1 = lib.stats()["games"]; lib.close()
    check("game was stored", n1 == n0 + 1, f"{n0} -> {n1}")
    check("switched to the trainer tab", win.notebook.index("current") == 2,
          str(win.notebook.index("current")))

    print()
    print("=== from the study tab ===")
    s = win.study
    win.notebook.select(1)
    pump(win, 0.3)
    s.board.goban.play(BLACK, 16, 3)
    s.board.goban.play(WHITE, 3, 16)
    s.board.last_move = s.board.goban.last_move()
    # stub the colour dialog so the test is not interactive
    t = win.trainer
    orig = t._ask_colour
    t._ask_colour = lambda: "B"
    lib = Library(); n2 = lib.stats()["games"]; lib.close()
    s.save_to_trainer()
    pump(win, 0.6)
    t._ask_colour = orig
    lib = Library(); n3 = lib.stats()["games"]; lib.close()
    check("game was stored", n3 == n2 + 1, f"{n2} -> {n3}")
    check("switched to the trainer tab", win.notebook.index("current") == 2,
          str(win.notebook.index("current")))

    print()
    print("=== duplicate storage is rejected ===")
    g2 = win.game
    g2.board.goban.clear = getattr(g2.board.goban, "clear", None)
    g2.board.goban = __import__("goban").Goban(19)      # fresh board
    g2.board.goban.play(BLACK, 15, 3)
    g2.board.goban.play(WHITE, 3, 15)
    lib = Library(); n4 = lib.stats()["games"]; lib.close()
    g2.save_to_trainer()          # same moves again
    pump(win, 0.5)
    lib = Library(); n5 = lib.stats()["games"]; lib.close()
    check("duplicate not re-stored", n5 == n4, f"{n4} -> {n5}")
    print("  status:", win.title())

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
print("BUTTON TEST PASSED")
