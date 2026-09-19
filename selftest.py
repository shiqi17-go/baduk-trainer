"""Built-in smoke test, used to verify the packaged exe actually works.

The shipped build is windowed (no console), so `weiqi-peilian.exe --selftest`
runs the real App with the real engine and writes selftest_report.txt next to
the executable.
"""
from __future__ import annotations

import os
import time
import traceback


def _pump(win, seconds=0.05):
    t0 = time.time()
    while time.time() - t0 < seconds:
        win.update()
        time.sleep(0.03)


def _pump_until(win, pred, timeout, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        win.update()
        if pred():
            return True, time.time() - t0
        time.sleep(0.05)
    return False, time.time() - t0


def _count_stones(view):
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


def run(verbose: bool = True) -> str:
    lines: list[str] = []
    fails: list[str] = []

    def log(s=""):
        lines.append(s)
        if verbose:
            print(s, flush=True)

    def check(name, cond, extra=""):
        log(("  PASS  " if cond else "  FAIL  ") + name + (f"  {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    import sys
    if getattr(sys, "frozen", False):
        # in a PyInstaller bundle the entry script is __main__, not "app"
        sys.modules.setdefault("app", sys.modules["__main__"])
    import app as appmod

    log("=" * 64)
    log("SELF TEST -- packaged build")
    log("=" * 64)
    log(f"frozen   : {getattr(__import__('sys'), 'frozen', False)}")
    log(f"executable: {os.path.abspath(os.sys.executable)}")
    log(f"app root : {appmod.ROOT}")
    log(f"engine dir: {appmod.KataGoEngine.__module__}")
    from katago_engine import ENGINE_DIR, KATAGO_EXE, MODELS_DIR, main_model_path, human_model_path
    log(f"engine    : {KATAGO_EXE}  exists={os.path.exists(KATAGO_EXE)}")
    log(f"models dir: {MODELS_DIR}")
    log(f"main net  : {main_model_path()}")
    log(f"human net : {human_model_path()}")
    log("")

    check("katago.exe found", os.path.exists(KATAGO_EXE))
    check("main neural net found", bool(main_model_path()))
    check("human SL net found", bool(human_model_path()))

    win = None
    try:
        win = appmod.App()
        win.update()
        log(f"window title: {win.title()}")

        win.rank_var.set("5级")
        win.color_var.set("黑")
        win.handicap_var.set(0)
        win.komi_var.set("7.5")
        win.pacing_var.set(False)
        win.visits_var.set(20)
        win.update()

        log("")
        log("--- starting a game (loads KataGo) ---")
        win.new_game()
        ok, dt = _pump_until(win, lambda: not win.busy, 300, "engine ready")
        check("engine started from the packaged build", ok, f"{dt:.1f}s")
        check("empty board, human is black", win.board.goban.move_number() == 0,
              str(win.board.goban.move_number()))
        b, w = _count_stones(win.board)
        check("nothing drawn yet", b == 0 and w == 0, f"b={b} w={w}")

        log("")
        log("--- human Q16, then the AI answers ---")
        win.submit_human_move("Q16")
        ok, dt = _pump_until(win, lambda: not win.busy, 240, "AI reply")
        check("AI answered", ok, f"{dt:.1f}s")
        g = win.board.goban
        check("two moves recorded", g.move_number() == 2, str(g.move_number()))
        b, w = _count_stones(win.board)
        check("one black and one white stone drawn", b == 1 and w == 1, f"b={b} w={w}")
        if len(g.moves) == 2:
            c, ax, ay = g.moves[1]
            log(f"  AI replied at {appmod.to_gtp(ax, ay, 19)}")

        log("")
        log("--- undo ---")
        win.undo()
        _pump_until(win, lambda: not win.busy, 90, "undo")
        check("undo cleared both moves", win.board.goban.move_number() == 0,
              str(win.board.goban.move_number()))
        b, w = _count_stones(win.board)
        check("canvas cleared after undo", b == 0 and w == 0, f"b={b} w={w}")

        log("")
        log("--- SGF export ---")
        sgf = win.build_sgf()
        check("SGF looks valid", sgf.startswith("(;GM[1]") and sgf.endswith(")"), sgf[:80])

        # -------------------------------------------------- unified window
        log("")
        log("--- unified window (对弈 + 打谱分析) ---")
        try:
            import main as mainmod
            m = mainmod.MainApp()
            m.update()
            n = m.notebook.index("end")
            check("window has at least 2 tabs", n >= 2, str(n))
            check("tab 0 is 对弈", "对弈" in m.notebook.tab(0, "text"),
                  m.notebook.tab(0, "text"))
            check("tab 1 is 打谱分析", "打谱分析" in m.notebook.tab(1, "text"),
                  m.notebook.tab(1, "text"))
            # the trainer tab was dropped by PyInstaller's lazy-import analysis;
            # make sure it survived packaging this time
            trainer_tab = None
            for i in range(n):
                if "错题本" in m.notebook.tab(i, "text"):
                    trainer_tab = i
                    break
            check("错题本 tab present", trainer_tab is not None,
                  f"tabs: {[m.notebook.tab(i, 'text') for i in range(n)]}")
            if trainer_tab is not None:
                check("trainer frame exists", m.trainer is not None)
                if m.trainer is not None:
                    check("trainer library works",
                          m.trainer.library.stats()["games"] >= 0)
            # The analysis engine now warms up at launch instead of on first
            # opening the tab, so placing a stone does not mean waiting ~15s.
            check("analysis engine warms up at launch",
                  m.study.startup_done is True)
            check("window is resizable", m.resizable() == (1, 1),
                  str(m.resizable()))
            m.notebook.select(1)
            _pump(m, 0.6)
            ok, dt = _pump_until(m, lambda: not m.study.busy, 300, "study analysis")
            check("study analysis works in the packaged build", ok, f"{dt:.1f}s")
            check("candidates computed", len(m.study.board.candidates) > 0,
                  str(len(m.study.board.candidates)))
            check("explanation panel is wide enough to read",
                  m.study.info.winfo_width() >= 400,
                  f"{m.study.info.winfo_width()}px")
            m._on_close()
            _pump(win, 0.3)
        except Exception:
            log(traceback.format_exc())
            check("unified window", False)

    except Exception:
        log("")
        log("EXCEPTION:")
        log(traceback.format_exc())
        fails.append("exception")
    finally:
        if win is not None:
            try:
                win.inbox.put({"action": "quit"})
                _pump(win, 0.4)
                win.destroy()
            except Exception:
                pass

    log("")
    if fails:
        log(f"RESULT: {len(fails)} CHECK(S) FAILED -> {fails}")
    else:
        log("RESULT: SELF TEST PASSED")
    return "\n".join(lines)


def write_report(root: str) -> str:
    text = run(verbose=False)
    path = os.path.join(root, "selftest_report.txt")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass
    return text
