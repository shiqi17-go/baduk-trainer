"""Integration test for the unified window (main.py): both modes in one app.

Verifies the two tabs, that each mode's engine works, that the analysis engine
is started lazily on first opening the study tab, and that the explanation
button produces text.
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


def pump_until(win, pred, timeout, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        win.update()
        if pred():
            return True, time.time() - t0
        time.sleep(0.05)
    print(f"    (timed out waiting for {label} after {timeout}s)")
    return False, time.time() - t0


def count_candidates(view):
    colours = {"#2e7d32", "#1565c0", "#ef6c00", "#9e9e9e"}
    n = 0
    for item in view.find_all():
        if view.type(item) != "oval":
            continue
        try:
            if view.itemcget(item, "fill") in colours:
                n += 1
        except Exception:
            pass
    return n


win = mainmod.MainApp()
win.update()
print("window title:", win.title())

try:
    # ------------------------------------------------------------- tabs
    print()
    print("=== 1. the window has both modes ===")
    n = win.notebook.index("end")
    check("at least two tabs present", n >= 2, str(n))
    check("tab 0 is 对弈", "对弈" in win.notebook.tab(0, "text"),
          win.notebook.tab(0, "text"))
    check("tab 1 is 打谱分析", "打谱分析" in win.notebook.tab(1, "text"),
          win.notebook.tab(1, "text"))
    # the trainer tab is added by the packaging step; check for it if present
    trainer_tabs = [i for i in range(n) if "错题本" in win.notebook.tab(i, "text")]
    check("错题本 tab present", bool(trainer_tabs),
          str([win.notebook.tab(i, "text") for i in range(n)]))
    # The analysis engine is now warmed up at launch rather than on first opening
    # the tab, so that placing a stone does not mean waiting ~15s for startup.
    check("analysis engine warms up at launch", win.study.startup_done is True)
    check("window is resizable", win.resizable() == (1, 1), str(win.resizable()))

    # --------------------------------------------------------- game mode
    print()
    print("=== 2. game mode still works ===")
    g = win.game
    g.rank_var.set("5级")
    g.color_var.set("黑")
    g.handicap_var.set(0)
    g.pacing_var.set(False)
    g.visits_var.set(20)
    g.new_game()
    ok, dt = pump_until(win, lambda: not g.busy, 400, "game engine ready")
    check("game engine started", ok, f"{dt:.1f}s")
    check("game board empty", g.board.goban.move_number() == 0)
    g.submit_human_move("Q16")
    ok, dt = pump_until(win, lambda: not g.busy, 300, "AI reply")
    check("AI replied in game mode", ok, f"{dt:.1f}s")
    check("game has two moves", g.board.goban.move_number() == 2,
          str(g.board.goban.move_number()))

    # -------------------------------------------------------- study mode
    print()
    print("=== 3. study mode (engine already warm) ===")
    win.notebook.select(1)
    pump(win, 0.8)
    s = win.study
    check("study frame ready", s.startup_done is True)
    ok, dt = pump_until(win, lambda: not s.busy, 600, "study analysis")
    check("study analysis completed", ok, f"{dt:.1f}s")
    check("candidates computed", len(s.board.candidates) > 0,
          str(len(s.board.candidates)))
    check("explanation panel is wide enough to read",
          s.info.winfo_width() >= 400, f"{s.info.winfo_width()}px")
    drawn = count_candidates(s.board)
    check("candidates drawn", drawn == len(s.board.candidates),
          f"drawn={drawn} data={len(s.board.candidates)}")

    # ------------------------------------------------- both engines alive
    print()
    print("=== 4. both engines coexist ===")
    check("game engine alive", bool(g.worker.engine and g.worker.engine.alive))
    check("analysis engine alive", bool(s.worker.engine and s.worker.engine.alive))
    check("they are different processes",
          g.worker.engine._proc.pid != s.worker.engine.proc.pid,
          f"{g.worker.engine._proc.pid} vs {s.worker.engine.proc.pid}")

    # ---------------------------------------------------- place + analyse
    print()
    print("=== 5. placing a stone in study mode ===")
    s.next_color = BLACK
    s.color_var.set("黑")
    s.place_stone(3, 15)          # D4
    ok, dt = pump_until(win, lambda: not s.busy, 300, "analysis after move")
    check("analysis after placing", ok, f"{dt:.1f}s")
    check("stone placed", s.board.goban.get(3, 15) == BLACK)
    check("candidates refreshed", len(s.board.candidates) > 0)

    # ------------------------------------------------------- explanation
    print()
    print("=== 6. 讲解这一手 produces text ===")
    s.explain_current()
    win.update()
    text = s.info.get("1.0", "end").strip()
    check("explanation is non-empty", len(text) > 20, f"{len(text)} chars")
    # the position renderer must speak about whose turn it is, NOT print a move
    # number for a move that has not been made
    check("says whose turn it is", "轮到" in text, text[:40])
    check("does not mislabel it as a played move", "手　" not in text[:20])
    check("mentions the AI's recommendation", "AI 建议" in text)
    # narrative form rather than the old bullet list: it must describe the board
    check("describes the board (groups)", "【局面】" in text, text[:80])
    check("mentions the student's level", "这个水平" in text)
    print("  --- 讲解输出 ---")
    for line in text.splitlines()[:8]:
        print("   ", line)

except Exception:
    traceback.print_exc()
    fails.append("exception during test")

finally:
    try:
        win._on_close()
    except Exception:
        try:
            win.destroy()
        except Exception:
            pass

print()
if fails:
    print(f"{len(fails)} CHECK(S) FAILED: {fails}")
    sys.exit(1)
print("UNIFIED APP TEST PASSED")
