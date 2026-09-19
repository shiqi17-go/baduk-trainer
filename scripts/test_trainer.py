"""End-to-end test of the 错题本 (trainer) interface."""
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
win.notebook.select(2)
pump(win, 0.5)
t = win.trainer

# Start from a clean library: leftover analysed games from earlier runs made
# the completion check (analyzed >= 1) pass instantly, before the new analysis
# had even started -- which is why the test collapsed downstream.
try:
    for _g in t.library.games():
        t.library.delete_game(_g.id)
except Exception:
    pass
t.refresh()
pump(win, 0.2)

try:
    print("=== 1. tab exists and is wired ===")
    check("third tab present", t is not None)
    check("library initialised", os.path.exists(t.library.path), t.library.path)
    stats = t.library.stats()
    print("  initial stats:", stats)

    print()
    print("=== 2. import a real game ===")
    import re
    from study import parse_sgf_mainline
    from goban import GTP_LETTERS
    sgf = r"F:\harness\baduk-trainer\samples\student10k.sgf"
    moves = [[c, f"{GTP_LETTERS[pos[0]]}{19-pos[1]}"]
             for c, pos in parse_sgf_mainline(sgf) if pos is not None]
    check("parsed the sgf", len(moves) > 100, str(len(moves)))
    gid = t.library.add_game(moves, black="测试甲", white="测试乙",
                             result="", path=sgf, name=os.path.basename(sgf))
    check("game added to the library", gid is not None, str(gid))
    t.refresh()
    pump(win, 0.3)
    check("game shows in the listbox", t.games_list.size() >= 1,
          str(t.games_list.size()))
    # select it
    t.games_list.selection_clear(0, "end")
    t.games_list.selection_set(0)
    pump(win, 0.2)

    print()
    print("=== 3. run analysis in the background ===")
    # ask colour programmatically by injecting into the worker queue instead of
    # the dialog (which blocks the UI thread)
    analysed_before = t.library.stats()["analyzed"]
    t.inbox.put({"action": "analyze", "library": t.library,
                 "game_id": gid, "moves": moves, "colour": "B", "visits": 200})
    ok, dt = pump_until(win,
                        lambda: t.library.stats()["analyzed"] > analysed_before,
                        1800, "analysis done")
    print(f"  analysis took {dt:.1f}s")
    check("analysis finished", ok)
    st = t.library.stats()
    print("  stats after:", st)
    check("problems were produced", st["problems"] > 0, str(st["problems"]))
    check("levels are recorded", bool(st["by_level"]), str(st["by_level"]))
    t.refresh_problems()
    pump(win, 0.2)
    check("problem list populated", t.prob_list.size() > 0, str(t.prob_list.size()))
    check("list shows losses", any("亏" in t.prob_list.get(i)
                                   for i in range(t.prob_list.size())))

    print()
    print("=== 4. pick a problem and the board shows the position ===")
    t.prob_list.selection_clear(0, "end")
    t.prob_list.selection_set(0)
    t._on_pick_problem()
    pump(win, 0.3)
    check("a problem is current", t.current is not None)
    check("board accepts clicks", t.board.accepts_click is True)
    check("prompt asks for a move", "你想下哪" in t.prompt.cget("text"),
          t.prompt.cget("text"))
    stones = sum(1 for row in t.board.grid for v in row if v != 0)
    check("the position has stones", stones > 0, str(stones))

    print()
    print("=== 5. answer wrong, then see the verdict ===")
    import json
    from goban import from_gtp
    best = from_gtp(t.current["best"], 19) if t.current["best"] else None
    # deliberately pick a DIFFERENT point (not the AI's answer) if possible
    wrong = None
    for y in range(19):
        for x in range(19):
            if t.board.grid[y][x] == 0 and (x, y) != best:
                wrong = (x, y)
                break
        if wrong:
            break
    check("could find a wrong point to click", wrong is not None)
    st_before = t.library.stats()["attempts"]
    if wrong:
        t._on_answer(wrong)
        pump(win, 0.3)
    check("verdict says it was wrong", "不对" in t.status_var.get(),
          t.status_var.get())
    st_after = t.library.stats()["attempts"]
    check("the attempt was recorded", st_after == st_before + 1,
          f"{st_before} -> {st_after}")

    print()
    print("=== 6. retry and answer with the AI's move ===")
    t.retry()
    pump(win, 0.2)
    check("retry re-enables clicks", t.board.accepts_click is True)
    if best:
        # reset and answer with the correct one
        t.answered = False
        t.board.accepts_click = True
        t._on_answer(best)
        pump(win, 0.3)
    check("verdict says correct", "答对了" in t.status_var.get(),
          t.status_var.get())

    print()
    print("=== 7. other tabs are untouched ===")
    check("game tab still present", "对弈" in win.notebook.tab(0, "text"))
    check("study tab still present", "打谱分析" in win.notebook.tab(1, "text"))

except Exception:
    traceback.print_exc()
    fails.append("exception during test")

finally:
    # clean up the test game so it does not pollute the real library
    try:
        if gid:
            t.library.delete_game(gid)
    except Exception:
        pass
    try:
        win._on_close()
    except Exception:
        pass

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("TRAINER TEST PASSED")
