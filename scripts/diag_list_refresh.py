"""Isolate the problem-list refresh from the analysis timing."""
import os
import sys
import time
import traceback

sys.path.insert(0, r"F:\harness\baduk-trainer")
import main as mainmod  # noqa: E402
from goban import BLACK, WHITE  # noqa: E402

win = mainmod.MainApp()
win.update()
win.notebook.select(2)
for _ in range(8):
    win.update()
    time.sleep(0.05)
t = win.trainer

try:
    # seed the library with a game and two problems, exactly as the worker would
    moves = [["B", "Q16"], ["W", "D4"], ["B", "A19"], ["W", "D16"]]
    gid = t.library.add_game(moves, black="测试甲", white="测试乙",
                             name="测试局")
    t.library.add_problem(gid, turn=2, move_no=3, colour="B", played="A19",
                          best="C17", loss=12.9, level="大恶手", winrate=None,
                          human_rank=2, human_share=0.3,
                          board=[[0] * 19 for _ in range(19)],
                          board_before=[[0] * 19 for _ in range(19)],
                          pv=["C17", "C16"], reason="测试讲解")
    t.library.add_problem(gid, turn=3, move_no=4, colour="W", played="D16",
                          best="D4", loss=4.1, level="失误", winrate=None,
                          human_rank=None, human_share=None,
                          board=[[0] * 19 for _ in range(19)],
                          board_before=[[0] * 19 for _ in range(19)],
                          pv=[], reason="测试讲解")

    print("direct problems() returns:", len(t.library.problems()))

    print("prob_list BEFORE refresh_problems:", t.prob_list.size())
    t.refresh()
    win.update()
    print("prob_list AFTER refresh_problems:", t.prob_list.size())
    print("  level filter:", t.level_var.get())
    print("  games_list selection:", t.games_list.curselection())
    print("  _games count:", len(getattr(t, "_games", [])))

    if t.prob_list.size() > 0:
        print("  list contents:")
        for i in range(t.prob_list.size()):
            print("   ", t.prob_list.get(i))
    else:
        # dig into why
        print("  --- probing the query ---")
        rows_none = t.library.problems(level=None, game_id=None)
        print("    problems(level=None, game_id=None):", len(rows_none))
        rows_all = t.library.problems(level="全部", game_id=None)
        print("    problems(level='全部', game_id=None):", len(rows_all))

except Exception:
    traceback.print_exc()

finally:
    try:
        for g in t.library.games():
            t.library.delete_game(g.id)
        win._on_close()
    except Exception:
        pass

print("DONE")
