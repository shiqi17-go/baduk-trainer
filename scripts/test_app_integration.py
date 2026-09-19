"""End-to-end integration test for the GUI.

Drives the real App class (real Tk window, real engine thread, real KataGo
process) instead of clicking with the mouse, then verifies BOTH the game state
and what was actually drawn on the canvas.

Run: python scripts/test_app_integration.py
"""
import os
import sys
import time
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
import app as appmod  # noqa: E402
from goban import BLACK, EMPTY, WHITE, from_gtp  # noqa: E402

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
            return True
        time.sleep(0.05)
    print(f"    (timed out waiting for {label} after {timeout}s)")
    return False


def count_stones(view):
    """Count stones actually drawn on the canvas, by fill colour."""
    black = white = 0
    for item in view.find_all():
        if view.type(item) != "oval":
            continue
        try:
            fill = view.itemcget(item, "fill")
        except Exception:
            continue
        if fill == "#1a1a1a":
            black += 1
        elif fill == "#f7f7f2":
            white += 1
    return black, white


win = appmod.App()
win.update()
print("window created:", win.title())
print("human colour default:", "black" if win.human_color == BLACK else "white")

try:
    # ---------------------------------------------------------- new game
    print()
    print("=== new game, human plays black, AI 5k ===")
    win.rank_var.set("5级")
    win.color_var.set("黑")
    win.handicap_var.set(0)
    win.komi_var.set("7.5")
    win.pacing_var.set(False)      # keep the test quick
    win.visits_var.set(20)
    win.update()

    t0 = time.time()
    win.new_game()
    ok = pump_until(win, lambda: not win.busy, 240, "engine ready")
    print(f"  engine ready in {time.time()-t0:.1f}s")
    check("engine became ready", ok)
    check("board is empty (human is black)", win.board.goban.move_number() == 0,
          str(win.board.goban.move_number()))
    b, w = count_stones(win.board)
    check("no stones drawn yet", b == 0 and w == 0, f"b={b} w={w}")
    check("status invites the human to move", "该你" in win.status_var.get(),
          win.status_var.get())

    # ---------------------------------------------------------- human move
    print()
    print("=== human plays Q16, AI replies ===")
    t0 = time.time()
    win.submit_human_move("Q16")
    ok = pump_until(win, lambda: not win.busy, 180, "AI reply")
    print(f"  round trip in {time.time()-t0:.1f}s")
    check("AI replied (not busy)", ok)

    g = win.board.goban
    check("two moves on the board", g.move_number() == 2, str(g.move_number()))
    hx, hy = from_gtp("Q16", 19)
    check("human stone at Q16", g.get(hx, hy) == BLACK, str(g.get(hx, hy)))
    ai_moves = [m for m in g.moves if m[0] == WHITE]
    check("AI played one white stone", len(ai_moves) == 1, str(len(ai_moves)))
    if ai_moves:
        _, ax, ay = ai_moves[0]
        print(f"  AI answered at {appmod.to_gtp(ax, ay, 19)}")
        check("AI stone is on the board", g.get(ax, ay) == WHITE)
        check("last-move marker tracks the AI move",
              win.board.last_move == (ax, ay), str(win.board.last_move))

    b, w = count_stones(win.board)
    check("1 black stone drawn", b == 1, f"b={b}")
    check("1 white stone drawn", w == 1, f"w={w}")

    # ------------------------------------------------------------- undo
    print()
    print("=== undo ===")
    win.undo()
    pump_until(win, lambda: not win.busy, 60, "undo")
    g = win.board.goban
    check("undo removed both moves", g.move_number() == 0, str(g.move_number()))
    b, w = count_stones(win.board)
    check("no stones drawn after undo", b == 0 and w == 0, f"b={b} w={w}")

    # ---------------------------------------------------- illegal move
    print()
    print("=== illegal move is refused ===")
    win.submit_human_move("Q16")
    pump_until(win, lambda: not win.busy, 180, "AI reply 2")
    g = win.board.goban
    check("board has 2 moves again", g.move_number() == 2, str(g.move_number()))
    before = g.move_number()
    win.submit_human_move("Q16")     # occupied point
    pump(win, 1.5)
    check("clicking an occupied point does not add a move",
          win.board.goban.move_number() == before, str(win.board.goban.move_number()))

    # --------------------------------------------------- handicap game
    print()
    print("=== handicap 3, human black (AI white moves first) ===")
    win.handicap_var.set(3)
    win._on_handicap()
    check("komi auto-set for handicap", win.komi_var.get() == "0.5", win.komi_var.get())
    win.new_game()
    ok = pump_until(win, lambda: not win.busy, 240, "handicap game ready")
    check("handicap game started", ok)
    g = win.board.goban
    check("3 handicap stones placed", g.captures is not None and
          sum(1 for c, x, y in g.moves if c == BLACK and x >= 0) == 3,
          str([(c, x, y) for c, x, y in g.moves]))
    whites = [m for m in g.moves if m[0] == WHITE]
    check("AI (white) moved first in the handicap game", len(whites) == 1,
          str(len(whites)))
    b, w = count_stones(win.board)
    check("3 black + 1 white drawn", b == 3 and w == 1, f"b={b} w={w}")

    # ------------------------------------------------------ rank switch
    print()
    print("=== switching rank restarts the engine ===")
    win.handicap_var.set(0)
    win._on_handicap()
    win.rank_var.set("15级")
    win.style_var.set("传统")
    win.new_game()
    ok = pump_until(win, lambda: not win.busy, 240, "15k game ready")
    check("new rank game started", ok)
    check("board reset for the new game", win.board.goban.move_number() == 0,
          str(win.board.goban.move_number()))

except Exception:
    traceback.print_exc()
    fails.append("exception during test")

finally:
    print()
    print("=== SGF export ===")
    try:
        from goban import Goban
        path = r"F:\harness\baduk-trainer\calibration\integration_test.sgf"
        g = Goban(19)
        g.play(BLACK, 3, 3)      # D16
        g.play(WHITE, 15, 15)    # Q4
        win.board.goban = g
        win.save_sgf_to(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        ok = os.path.exists(path) and ";B[dd]" in text and ";W[pp]" in text
        check("SGF written with both moves", ok, text[:110])
    except Exception:
        traceback.print_exc()
        check("SGF written", False)

    win.inbox.put({"action": "quit"})
    time.sleep(0.3)
    win.update()
    win.destroy()

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("INTEGRATION TEST PASSED")
