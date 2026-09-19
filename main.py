"""围棋 AI 教学助手 —— one window, two modes.

    对弈     play against KataGo at a chosen human rank
    打谱分析  place stones freely and see what the AI thinks, plus written
              explanations of why

The analysis engine is only started when the 打谱分析 tab is first opened, so
launching the app does not spin up two engines at once.
"""
from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import GameFrame  # noqa: E402
from katago_engine import ROOT  # noqa: E402
from study import StudyFrame  # noqa: E402

# Unconditional top-level import. The trainer tab was imported inside a
# try/except (both lazily and as a --hidden-import), and PyInstaller silently
# omitted it from every packaged build, so the exe shipped with only two tabs.
# Importing it unconditionally forces the dependency graph to include it.
from trainer import TrainerFrame  # noqa: E402
HAVE_TRAINER = True

APP_TITLE = "围棋 AI 教学助手"
TAB_GAME = 0
TAB_STUDY = 1


def _log_trainer_error(exc: Exception) -> None:
    """In a windowed (no-console) build a swallowed exception is invisible, so
    drop it in a file we can actually read."""
    try:
        import traceback
        with open(os.path.join(ROOT, "trainer_error.log"), "a",
                  encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n---\n")
    except Exception:
        pass


class MainApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg="#f0ece3")
        # The window used to be locked to a fixed size, which felt cramped on a
        # large screen. Start maximised and allow resizing.
        self.resizable(True, True)
        self.minsize(1080, 740)
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(1500, int(sw * 0.94)), min(1000, int(sh * 0.92))
            self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        except Exception:
            pass
        try:
            self.state("zoomed")           # maximise (Windows)
        except Exception:
            pass

        style = ttk.Style(self)
        try:
            style.configure("TNotebook.Tab", font=("Microsoft YaHei", 10), padding=(16, 6))
        except Exception:
            pass

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1 and 2 are the original interfaces and are left untouched.
        self.game = GameFrame(self.notebook)
        self.study = StudyFrame(self.notebook)
        self.notebook.add(self.game, text="　对弈　")
        self.notebook.add(self.study, text="　打谱分析　")

        # Tab 3 is new and self-contained: 错题本 practises the student's own
        # mistakes. Its own storage, its own engine, no coupling to the others.
        self.trainer = None
        try:
            self.trainer = TrainerFrame(self.notebook)
            self.notebook.add(self.trainer, text="　错题本　")
        except Exception as exc:      # never let a new tab break the old ones
            _log_trainer_error(exc)

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.set_status("本程序完全离线运行：KataGo 引擎与神经网络都在本地文件夹里。")

        # Warm the analysis engine up in the background straight away. It used
        # to start only when the 打谱分析 tab was first opened, which meant
        # staring at "AI 分析中…" for fifteen seconds right when you wanted to
        # start placing stones.
        try:
            self.study.ensure_analysis()
        except Exception:
            pass

    # -------------------------------------------------------------- helpers
    def send_to_trainer(self, moves, colour=None, black="", white="",
                        name=""):
        """Add a game to the problem book and start analysing it.

        The "存入错题本" buttons in the play and study tabs call this. It adds
        the game to the shared library, switches to the 错题本 tab and kicks off
        analysis so problems appear without the user doing anything else.
        """
        if self.trainer is None:
            self.set_status("错题本不可用。")
            return False
        if not moves:
            self.set_status("棋盘上还没有可存的对局。")
            return False
        from library import Library
        lib = Library()
        gid = lib.add_game(moves, black=black, white=white, name=name)
        lib.close()
        self.notebook.select(2)
        if gid is None:
            self.set_status("这盘棋已在错题本里，无需重复收录。")
            return False
        self.trainer.queue_analysis(gid, colour)
        return True

    def set_status(self, text: str):
        try:
            self.title(f"{APP_TITLE} —— {text}")
        except Exception:
            pass

    def _on_tab(self, _ev=None):
        try:
            idx = self.notebook.index("current")
        except Exception:
            return
        if idx == TAB_STUDY:
            self.study.ensure_analysis()
            self.set_status("打谱分析：落子后 AI 自动给出推荐点与判断")
        elif idx == 2:
            try:
                self.trainer.refresh()
            except Exception:
                pass
            self.set_status("错题本：导入棋谱 → 分析 → 用自己的失误练题")
        else:
            self.set_status("对弈：选好段位后点「新对局」")

    def _on_close(self):
        frames = [self.game, self.study]
        if self.trainer is not None:
            frames.append(self.trainer)
        for frame in frames:
            try:
                frame.shutdown()
            except Exception:
                pass
        self.destroy()


def main():
    if "--selftest" in sys.argv:
        # windowed builds have no console, so the report goes to a file
        import selftest
        selftest.write_report(ROOT)
        return
    MainApp().mainloop()


if __name__ == "__main__":
    main()
