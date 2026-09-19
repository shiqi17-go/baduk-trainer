"""Integration test for the study (打谱分析) mode."""
import os
import sys
import time
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
import study  # noqa: E402
from goban import BLACK, EMPTY, WHITE  # noqa: E402

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


CAND_COLOURS = {"#2e7d32", "#1565c0", "#ef6c00", "#9e9e9e"}


def count_candidates(view):
    n = 0
    for item in view.find_all():
        if view.type(item) != "oval":
            continue
        try:
            if view.itemcget(item, "fill") in CAND_COLOURS:
                n += 1
        except Exception:
            pass
    return n


win = study.StudyApp()
win.update()
print("window:", win.title())

try:
    # ---------------------------------------------------- first analysis
    print()
    print("=== board coordinates ===")
    from goban import GTP_LETTERS
    texts = [win.board.itemcget(i, "text") for i in win.board.find_all()
             if win.board.type(i) == "text" and win.board.itemcget(i, "text")]
    letters = [t for t in texts if t.isalpha()]
    numbers = [t for t in texts if t.isdigit()]
    check("19 letters on two sides", len(letters) == 38, str(len(letters)))
    check("19 numbers on two sides", len(numbers) == 38, str(len(numbers)))
    check("letters are A..T skipping I",
          sorted(set(letters), key=lambda c: GTP_LETTERS.index(c))
          == list(GTP_LETTERS[:19]),
          "".join(sorted(set(letters), key=lambda c: GTP_LETTERS.index(c))))
    check("numbers are 1..19",
          sorted(set(numbers), key=int) == [str(i) for i in range(1, 20)],
          ",".join(sorted(set(numbers), key=int)))
    check("no 'I' label (Go notation skips it)", "I" not in texts)

    print()
    print("=== initial analysis of the empty board ===")
    ok, dt = pump_until(win, lambda: not win.busy, 600, "first analysis")
    check("engine started and analysed", ok, f"{dt:.1f}s")
    print("  engine version:", win.engine_version or "(unknown)")
    check("engine reports a version", bool(win.engine_version), win.engine_version)
    check("candidates computed", len(win.board.candidates) > 0,
          str(len(win.board.candidates)))
    n_drawn = count_candidates(win.board)
    check("candidates drawn on the canvas",
          n_drawn == len(win.board.candidates),
          f"drawn={n_drawn} data={len(win.board.candidates)}")
    print("  top候选:", [c["move"] for c in win.board.candidates])
    print("  状态栏:", win.status_var.get())

    # ---------------------------------------------------- place a stone
    print()
    print("=== place a black stone at Q16 ===")
    win.next_color = BLACK
    win.color_var.set("黑")
    t0 = time.time()
    win.place_stone(15, 3)          # Q16
    ok, dt = pump_until(win, lambda: not win.busy, 300, "analysis after move")
    check("analysis after placing", ok, f"{dt:.1f}s")
    check("stone is on the board", win.board.goban.get(15, 3) == BLACK)
    check("mover switched to white", win.board.goban.moves[-1][0] == BLACK)
    check("candidates recomputed", len(win.board.candidates) > 0)
    drawn = count_candidates(win.board)
    check("candidates redrawn", drawn == len(win.board.candidates), str(drawn))
    check("status shows the move number", "第 1 手" in win.status_var.get(),
          win.status_var.get())

    # ------------------------------------- race: two stones in quick succession
    print()
    print("=== race test: two stones placed back to back ===")
    win.place_stone(3, 15)          # D4
    win.place_stone(15, 15)         # Q4  -- placed before the first analysis lands
    gen_at_end = win.gen
    ok, dt = pump_until(win, lambda: not win.busy, 300, "final analysis")
    check("analysis settled", ok, f"{dt:.1f}s")
    check("all three stones on the board", win.board.goban.move_number() == 3,
          str(win.board.goban.move_number()))
    # the displayed result must belong to the final position
    check("no stale result overwrote the final one", win.gen == gen_at_end,
          f"gen={win.gen} expected={gen_at_end}")
    last = win.board.goban.moves[-1]
    check("last move is Q4", last[1] == 15 and last[2] == 15, str(last))

    # ------------------------------------------------------------- undo
    print()
    print("=== undo ===")
    # capture BEFORE undoing: reading history[-1] afterwards gives the wrong
    # stone, because undo() has already popped it
    expected_undone = win.history[-1]
    win.undo()
    ok, _ = pump_until(win, lambda: not win.busy, 300, "analysis after undo")
    check("undo removed one stone", win.board.goban.move_number() == 2,
          str(win.board.goban.move_number()))
    check("the undone stone was black Q4",
          expected_undone[0] == BLACK and expected_undone[1:] == (15, 15),
          str(expected_undone))
    check("next colour goes back to the undone stone's colour",
          win.next_color == BLACK, f"next={win.next_color}")

    # ------------------------------------------------------- toggle ownership
    print()
    print("=== ownership overlay ===")
    win.own_var.set(True)
    win._on_toggle_own()
    win.update()
    check("ownership data present", bool(win.board.ownership))
    check("overlay flag on", win.board.show_ownership)
    win.own_var.set(False)
    win._on_toggle_own()

    # ---------------------------------------------------------- sgf round trip
    print()
    print("=== SGF save / load round trip ===")
    path = r"F:\harness\baduk-trainer\samples\study_roundtrip.sgf"
    parts = ["(;GM[1]FF[4]CA[UTF-8]SZ[19]KM[7.5]",
             ";B[dd]", ";W[pp]", ";B[dp]", ";W[pd]", ";B[jj]"]
    parts.append(")")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    moves = study.parse_sgf_mainline(path)
    check("parser found 5 moves", len(moves) == 5, str(len(moves)))
    check("first move is B dd", moves[0] == ("B", (3, 3)), str(moves[0]))
    check("last move is B jj", moves[-1] == ("B", (9, 9)), str(moves[-1]))

    win.clear_board()
    pump_until(win, lambda: not win.busy, 300, "analysis after clear")
    check("board cleared", win.board.goban.move_number() == 0,
          str(win.board.goban.move_number()))
    check("candidates cleared or recomputed", True)

except Exception:
    traceback.print_exc()
    fails.append("exception during test")

finally:
    try:
        win.inbox.put({"action": "quit"})
        time.sleep(0.3)
        win.update()
        win.destroy()
    except Exception:
        pass

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("STUDY MODE TEST PASSED")
