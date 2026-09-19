"""围棋陪练 -- play against KataGo at a chosen human rank.

Tkinter front-end (no third-party GUI deps, packages cleanly with PyInstaller).
The engine runs in a background thread so the board stays responsive even
though the human-like config deliberately pauses 0-10s before every move.

Run:  python app.py
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from goban import (BLACK, EMPTY, WHITE, Goban, IllegalMove, from_gtp,  # noqa: E402
                   handicap_points, sgf_coord, star_points, to_gtp)
from katago_engine import (ROOT, ALL_PROFILES, DEFAULT_CFG,  # noqa: E402
                           KataGoEngine, KataGoError)

APP_TITLE = "围棋陪练"
WOOD = "#E3B778"
WOOD_DARK = "#C99B57"
LINE = "#3B2A16"
STAR = "#2A1E10"

# label -> humanSL profile. Two styles:
#   preaz_*  = pre-AlphaZero opening style (old-fashioned, common for kyu players)
#   rank_*   = modern style
RANKS = [
    ("20级", 20, "k"), ("18级", 18, "k"), ("15级", 15, "k"), ("12级", 12, "k"),
    ("10级", 10, "k"), ("8级", 8, "k"), ("6级", 6, "k"), ("5级", 5, "k"),
    ("4级", 4, "k"), ("3级", 3, "k"), ("2级", 2, "k"), ("1级", 1, "k"),
    ("1段", 1, "d"), ("2段", 2, "d"), ("3段", 3, "d"), ("4段", 4, "d"),
    ("5段", 5, "d"), ("6段", 6, "d"), ("7段", 7, "d"), ("9段", 9, "d"),
    ("最强（满血 KataGo）", 0, "max"),
]
RANK_LABELS = [f"{lbl}" for lbl, _, _ in RANKS]

# Sentinel profile: full-strength KataGo with the human SL model switched off.
MAX_PROFILE = "__max__"


def profile_for(index: int, style: str) -> str:
    _, num, kind = RANKS[index]
    if kind == "max":
        return MAX_PROFILE
    prefix = "preaz" if style == "传统" else "rank"
    return f"{prefix}_{num}{kind}"


# --------------------------------------------------------------- engine side

class EngineWorker(threading.Thread):
    """Owns the KataGo process. Talks to the UI through two queues."""

    def __init__(self, inbox: queue.Queue, outbox: queue.Queue):
        super().__init__(daemon=True)
        self.inbox = inbox
        self.outbox = outbox
        self.engine: KataGoEngine | None = None
        self.profile = ""
        self.human_color = BLACK
        self.ai_color = WHITE
        self.board_size = 19
        self.komi = 7.5
        self.handicap = 0
        self.nice_pacing = True
        self.visits = 40
        self.game_id = 0
        self.moves: list[tuple[int, str]] = []   # (color, gtp coord) applied so far
        self.stopped = False

    # -- helpers

    def post(self, kind: str, **kw):
        """Every message carries the game generation, so the UI can drop
        anything left over from a previous game."""
        kw.setdefault("game_id", self.game_id)
        self.outbox.put({"kind": kind, **kw})

    def log(self, text: str):
        self.post("log", text=text)

    def _overrides(self, effective_profile: str | None = None,
                   strongest: bool = False,
                   visits: int | None = None) -> dict:
        """Config overrides for this engine.

        humanSLProfile must NOT be set for full-strength play: with no human
        model loaded it is invalid, and passing the sentinel would be rejected
        outright. That mistake is what stopped the engine from starting.
        """
        ov = {
            "rules": "chinese",
            "maxVisits": str(visits if visits is not None else self.visits),
            "logToStderr": "false",
            "logAllGTPCommunication": "false",
            "logSearchInfo": "false",
            "logDir": os.path.join(ROOT, "logs"),
        }
        if not strongest:
            ov["humanSLProfile"] = effective_profile or self.profile
        if not self.nice_pacing:
            ov["delayMoveScale"] = "0.0"
            ov["delayMoveMax"] = "0.0"
        return ov

    def _ensure_engine(self, force_restart: bool = False):
        if self.engine and self.engine.alive and not force_restart:
            return
        if self.engine:
            self.engine.close()
        strongest = self.profile == MAX_PROFILE
        effective = "rank_9d" if strongest else self.profile
        # full-strength play: real search, and enough of it to matter. With the
        # CUDA backend ~800 visits costs about what 300 did on OpenCL.
        visits = max(self.visits, 800) if strongest else self.visits
        self.post("status", text="正在启动 AI 引擎…")
        eng = KataGoEngine(
            profile=effective,
            rules="chinese",
            board_size=self.board_size,
            komi=self.komi,
            # full-strength play needs real search, not the human-imitation config
            visits=visits,
            use_human_model=not strongest,
            config=DEFAULT_CFG if strongest else None,
            extra_overrides=self._overrides(effective, strongest, visits),
        )
        eng.start()
        self.engine = eng
        mode = "满血 KataGo（不模仿人类）" if strongest else self.profile
        self.log(f"AI 就绪：{mode}  |  引擎 {eng.version}  |  "
                 f"主模型 {os.path.basename(self.engine.main_model or '')}")

    def _reset_board(self):
        eng = self.engine
        eng._cmd(f"boardsize {self.board_size}")
        eng._cmd(f"komi {self.komi}")
        eng.clear_board()
        self.moves = []
        if self.handicap >= 2:
            pts = handicap_points(self.board_size, self.handicap)
            coords = [to_gtp(x, y, self.board_size) for x, y in pts]
            eng._cmd("set_free_handicap " + " ".join(coords))
            self.moves = [(BLACK, c) for c in coords]

    def _sync_engine_board(self):
        """Rebuild the engine's board from self.moves (used after undo)."""
        eng = self.engine
        eng._cmd(f"boardsize {self.board_size}")
        eng._cmd(f"komi {self.komi}")
        eng.clear_board()
        handicap_moves = []
        rest = []
        for color, coord in self.moves:
            if self.handicap >= 2 and color == BLACK and len(handicap_moves) < self.handicap:
                handicap_moves.append(coord)
            else:
                rest.append((color, coord))
        if handicap_moves:
            eng._cmd("set_free_handicap " + " ".join(handicap_moves))
        for color, coord in rest:
            c = "B" if color == BLACK else "W"
            if coord.upper() == "PASS":
                eng.play(c, "pass")
            else:
                eng.play(c, coord)

    def _ai_turn(self):
        color = self.ai_color
        c = "B" if color == BLACK else "W"
        self.post("thinking", value=True)
        t0 = time.time()
        try:
            mv = self.engine.genmove(c, timeout=240)
        except KataGoError as e:
            self.post("thinking", value=False)
            self.post("error", text=f"引擎出错：{e}")
            return None
        dt = time.time() - t0
        self.post("thinking", value=False)
        low = mv.strip().lower()
        if low == "resign":
            self.post("gameover", text="AI 认输，你赢了！")
            self.post("ai_move", coord="resign", seconds=dt)
            return "resign"
        self.moves.append((color, mv))
        self.post("ai_move", coord=mv, seconds=dt)
        self._assess()
        return mv

    def _assess(self):
        """Ask the net for a real position assessment.

        Replaces a naive area count that was wrong by up to 80 points: it only
        counted stones and fully enclosed empty points, so it had no idea about
        open territory, influence, or dead stones, and reported a decided game
        as dead even.
        """
        try:
            ev = self.engine.evaluate()
        except Exception:
            return
        if ev:
            self.post("assessment", data=ev)

    # -- main loop

    def run(self):
        while not self.stopped:
            try:
                cmd = self.inbox.get(timeout=0.3)
            except queue.Empty:
                continue
            action = cmd.get("action")
            try:
                if action == "new":
                    self._do_new(cmd)
                elif action == "move":
                    self._do_move(cmd)
                elif action == "pass":
                    self._do_pass()
                elif action == "undo":
                    self._do_undo()
                elif action == "resign":
                    self.post("gameover", text="你认输了。")
                elif action == "quit":
                    self.stopped = True
                    break
            except Exception as e:
                self.post("error", text=f"{type(e).__name__}: {e}")
        if self.engine:
            self.engine.close()

    def _do_new(self, cmd):
        self.game_id = cmd.get("game_id", self.game_id)
        need_restart = (
            self.engine is None
            or not self.engine.alive
            or cmd["profile"] != self.profile
            or cmd["nice_pacing"] != self.nice_pacing
            or cmd["visits"] != self.visits
        )
        self.profile = cmd["profile"]
        self.human_color = cmd["human_color"]
        self.ai_color = WHITE if self.human_color == BLACK else BLACK
        self.board_size = cmd["board_size"]
        self.komi = cmd["komi"]
        self.handicap = cmd["handicap"]
        self.nice_pacing = cmd["nice_pacing"]
        self.visits = cmd["visits"]
        self._ensure_engine(force_restart=need_restart)
        self._reset_board()
        # in a handicap game White moves first; otherwise Black does
        first = WHITE if self.handicap >= 2 else BLACK
        ai_first = first == self.ai_color
        self.post("new_ok", handicap_points=handicap_points(self.board_size, self.handicap),
                  human_color=self.human_color, ai_first=ai_first)
        if ai_first:
            self._ai_turn()
        else:
            self.post("status", text="该你下了")
            self._assess()

    def _do_move(self, cmd):
        coord = cmd["coord"]
        c = "B" if self.human_color == BLACK else "W"
        self.engine.play(c, coord)
        self.moves.append((self.human_color, coord))
        self.post("human_move", coord=coord)
        self._ai_turn()

    def _do_pass(self):
        c = "B" if self.human_color == BLACK else "W"
        self.engine.play(c, "pass")
        self.moves.append((self.human_color, "pass"))
        self.post("human_move", coord="pass")
        last_two = [m[1].strip().lower() for m in self.moves[-2:]]
        if len(last_two) == 2 and last_two == ["pass", "pass"]:
            try:
                score = self.engine.final_score()
            except KataGoError:
                score = ""
            self.post("gameover", text=f"双方停手，终局。{score}")
            return
        self._ai_turn()

    def _do_undo(self):
        # remove the AI's move and the human's move before it
        removed = 0
        while self.moves and removed < 2:
            color, _ = self.moves.pop()
            removed += 1
            if color == self.human_color:
                break
        self._sync_engine_board()
        self.post("undo_ok", moves=list(self.moves))
        self.post("status", text="该你下了")
        self._assess()


# ------------------------------------------------------------------ the board

class BoardView(tk.Canvas):
    def __init__(self, master, app, size=19, cell=32, margin=34, **kw):
        self.size = size
        self.cell = cell
        self.margin = margin
        px = margin * 2 + cell * (size - 1)
        super().__init__(master, width=px, height=px, highlightthickness=0,
                         bg=WOOD, **kw)
        self.app = app
        self.goban = Goban(size)
        self.last_move: tuple[int, int] | None = None
        self.locked = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self._hover: tuple[int, int] | None = None
        self.redraw()

    # -- geometry

    def _px(self, x: int) -> int:
        return self.margin + x * self.cell

    def _xy_from_event(self, ev) -> tuple[int, int] | None:
        gx = round((ev.x - self.margin) / self.cell)
        gy = round((ev.y - self.margin) / self.cell)
        if not (0 <= gx < self.size and 0 <= gy < self.size):
            return None
        # reject clicks too far from an intersection
        if abs(self._px(gx) - ev.x) > self.cell * 0.5 or abs(self._px(gy) - ev.y) > self.cell * 0.5:
            return None
        return gx, gy

    # -- drawing

    def redraw(self):
        self.delete("all")
        n, c, m = self.size, self.cell, self.margin
        # board texture
        self.create_rectangle(0, 0, self.winfo_reqwidth(), self.winfo_reqheight(),
                              fill=WOOD, outline="")
        for i in range(3):
            off = i * 4
            self.create_rectangle(0 + off, 0 + off,
                                  self.winfo_reqwidth() - off,
                                  self.winfo_reqheight() - off,
                                  outline=WOOD_DARK)
        # grid
        for i in range(n):
            p = self._px(i)
            self.create_line(self._px(0), p, self._px(n - 1), p, fill=LINE, width=1)
            self.create_line(p, self._px(0), p, self._px(n - 1), fill=LINE, width=1)
        # star points
        r = max(2, c // 11)
        for x, y in star_points(n):
            if n == 19 and (x == 9) != (y == 9):
                pass
            self.create_oval(self._px(x) - r, self._px(y) - r,
                             self._px(x) + r, self._px(y) + r,
                             fill=STAR, outline=STAR)
        # coordinates
        from goban import GTP_LETTERS
        for i in range(n):
            self.create_text(self._px(i), m * 0.45, text=GTP_LETTERS[i],
                             fill=LINE, font=("Segoe UI", max(7, c // 3)))
            self.create_text(self._px(i), self._px(n - 1) + m * 0.55,
                             text=GTP_LETTERS[i], fill=LINE,
                             font=("Segoe UI", max(7, c // 3)))
            self.create_text(m * 0.45, self._px(i), text=str(n - i),
                             fill=LINE, font=("Segoe UI", max(7, c // 3)))
            self.create_text(self._px(n - 1) + m * 0.55, self._px(i),
                             text=str(n - i), fill=LINE,
                             font=("Segoe UI", max(7, c // 3)))
        # hover ghost
        if self._hover and not self.locked and self.goban.get(*self._hover) == EMPTY:
            hx, hy = self._hover
            rr = c * 0.44
            self.create_oval(self._px(hx) - rr, self._px(hy) - rr,
                             self._px(hx) + rr, self._px(hy) + rr,
                             outline="#6b5a3e", width=1)
        # stones
        rr = c * 0.46
        for y in range(n):
            for x in range(n):
                v = self.goban.get(x, y)
                if v == EMPTY:
                    continue
                fill = "#1a1a1a" if v == BLACK else "#f7f7f2"
                outline = "#000000" if v == BLACK else "#8a8a80"
                self.create_oval(self._px(x) - rr, self._px(y) - rr,
                                 self._px(x) + rr, self._px(y) + rr,
                                 fill=fill, outline=outline, width=1)
                if v == WHITE:
                    self.create_oval(self._px(x) - rr * 0.55, self._px(y) - rr * 0.55,
                                     self._px(x) - rr * 0.1, self._px(y) - rr * 0.1,
                                     fill="#ffffff", outline="")
        # last move marker
        if self.last_move:
            lx, ly = self.last_move
            v = self.goban.get(lx, ly)
            col = "#ff3b30" if v == BLACK else "#e02020"
            r2 = c * 0.16
            self.create_oval(self._px(lx) - r2, self._px(ly) - r2,
                             self._px(lx) + r2, self._px(ly) + r2,
                             fill=col, outline="")

    def _on_motion(self, ev):
        pos = self._xy_from_event(ev)
        if pos != self._hover:
            self._hover = pos
            self.redraw()

    def _on_click(self, ev):
        if self.locked:
            return
        pos = self._xy_from_event(ev)
        if not pos:
            return
        x, y = pos
        ok, why = self.goban.is_legal(self.app.human_color, x, y)
        if not ok:
            self.app.set_status(f"不能下在这里：{why}")
            return
        self.app.submit_human_move(to_gtp(x, y, self.size))


# ---------------------------------------------------------------------- app

class GameFrame(tk.Frame):
    """对弈模式 —— as a Frame so it can live inside a tab of the main window."""

    def __init__(self, master, **kw):
        super().__init__(master, bg="#f0ece3", **kw)
        self.human_color = BLACK
        self.board_size = 19
        self.inbox: queue.Queue = queue.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.worker = EngineWorker(self.inbox, self.outbox)
        self.worker.start()
        self.busy = False
        self.game_over = False
        self.game_id = 0
        self.assessment = None       # latest net evaluation, from _assess
        self._pump_job = None
        self._build_ui()
        self._pump_job = self.after(60, self._pump)
        self.set_status("选择段位后点「新对局」开始。")

    def shutdown(self):
        """Stop the engine thread and cancel the pending timer.

        Without the cancel, an already-queued callback can fire after the widget
        is destroyed and Tcl reports 'invalid command name ..._pump'.
        """
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        try:
            self.inbox.put({"action": "quit"})
            time.sleep(0.15)
        except Exception:
            pass

    def destroy(self):
        """Cancel the timer no matter which path destroys the widget -- relying
        on shutdown() alone was not enough and Tcl still complained about a
        callback for a widget that no longer existed."""
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        super().destroy()

    # -------------------------------------------------------------- widgets

    def _build_ui(self):
        pad = dict(padx=6, pady=3)
        left = tk.Frame(self, bg="#f0ece3")
        left.grid(row=0, column=0, sticky="n", padx=(12, 4), pady=12)

        tk.Label(left, text=APP_TITLE, bg="#f0ece3",
                 font=("Microsoft YaHei", 15, "bold")).pack(anchor="w", pady=(0, 8))

        # rank
        box = tk.LabelFrame(left, text="对手段位", bg="#f0ece3",
                            font=("Microsoft YaHei", 9))
        box.pack(fill="x", pady=4)
        self.rank_var = tk.StringVar(value="最强（满血 KataGo）")
        cb = ttk.Combobox(box, textvariable=self.rank_var, values=RANK_LABELS,
                          state="readonly", width=10)
        cb.pack(side="left", **pad)
        self.style_var = tk.StringVar(value="传统")
        ttk.Combobox(box, textvariable=self.style_var, values=["传统", "现代"],
                     state="readonly", width=5).pack(side="left", **pad)

        # colour
        box2 = tk.LabelFrame(left, text="我执", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box2.pack(fill="x", pady=4)
        self.color_var = tk.StringVar(value="黑")
        for label in ("黑", "白"):
            tk.Radiobutton(box2, text=label, value=label, variable=self.color_var,
                           bg="#f0ece3", font=("Microsoft YaHei", 9),
                           command=self._on_color).pack(side="left", padx=8)

        # handicap + komi
        box3 = tk.LabelFrame(left, text="让子 / 贴目", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box3.pack(fill="x", pady=4)
        tk.Label(box3, text="让", bg="#f0ece3").pack(side="left", padx=(6, 0))
        self.handicap_var = tk.IntVar(value=0)
        ttk.Spinbox(box3, from_=0, to=9, width=3, textvariable=self.handicap_var,
                    command=self._on_handicap).pack(side="left", padx=2)
        tk.Label(box3, text="子   贴目", bg="#f0ece3").pack(side="left")
        self.komi_var = tk.StringVar(value="7.5")
        ttk.Entry(box3, width=5, textvariable=self.komi_var).pack(side="left", padx=2)

        # pacing / strength
        box4 = tk.LabelFrame(left, text="棋力与节奏", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box4.pack(fill="x", pady=4)
        self.pacing_var = tk.BooleanVar(value=True)
        tk.Checkbutton(box4, text="像真人一样思考（0~10秒延迟）",
                       variable=self.pacing_var, bg="#f0ece3",
                       font=("Microsoft YaHei", 9)).pack(anchor="w", padx=6)
        row = tk.Frame(box4, bg="#f0ece3")
        row.pack(anchor="w", padx=6, pady=(2, 6))
        tk.Label(row, text="算力(visits)", bg="#f0ece3").pack(side="left")
        self.visits_var = tk.IntVar(value=40)
        ttk.Spinbox(row, from_=1, to=400, width=5, increment=10,
                    textvariable=self.visits_var).pack(side="left", padx=4)

        # buttons
        btns = tk.Frame(left, bg="#f0ece3")
        btns.pack(fill="x", pady=(10, 4))
        self._btn(btns, "新对局", self.new_game).pack(fill="x", pady=2)
        self._btn(btns, "悔棋", self.undo).pack(fill="x", pady=2)
        row2 = tk.Frame(btns, bg="#f0ece3")
        row2.pack(fill="x", pady=2)
        self._btn(row2, "虚手(pass)", self.do_pass).pack(side="left", expand=True, fill="x")
        self._btn(row2, "认输", self.resign).pack(side="left", expand=True, fill="x")
        self._btn(btns, "保存棋谱 (SGF)", self.save_sgf).pack(fill="x", pady=2)
        self._btn(btns, "存入错题本", self.save_to_trainer).pack(fill="x", pady=2)

        # board
        self.board = BoardView(self, self, size=self.board_size)
        self.board.grid(row=0, column=1, padx=12, pady=12)

        # status
        self.status_var = tk.StringVar(value="")
        bar = tk.Label(self, textvariable=self.status_var, anchor="w",
                       bg="#dcd6c8", font=("Microsoft YaHei", 9), padx=8)
        bar.grid(row=1, column=0, columnspan=2, sticky="we")
        self.counter_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.counter_var, anchor="w", bg="#dcd6c8",
                 font=("Consolas", 9), padx=8).grid(row=2, column=0, columnspan=2,
                                                    sticky="we")

        # log
        self.log_text = tk.Text(self, height=5, width=42, font=("Consolas", 8),
                                bg="#faf8f3", relief="flat")
        self.log_text.grid(row=3, column=0, sticky="we", padx=12, pady=(0, 10))
        self.log_text.configure(state="disabled")

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, font=("Microsoft YaHei", 9),
                         width=14, relief="groove", bg="#e9e3d5", activebackground="#dcd6c8")

    # ---------------------------------------------------------------- actions

    def _rank_index(self) -> int:
        try:
            return RANK_LABELS.index(self.rank_var.get())
        except ValueError:
            return RANK_LABELS.index("5级")

    def _on_color(self):
        self.human_color = BLACK if self.color_var.get() == "黑" else WHITE

    def _on_handicap(self):
        h = self.handicap_var.get()
        self.komi_var.set("0.5" if h >= 2 else "7.5")

    def set_status(self, text: str):
        self.status_var.set(text)

    def log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def new_game(self):
        self._on_color()
        try:
            komi = float(self.komi_var.get())
        except ValueError:
            komi = 7.5
        profile = profile_for(self._rank_index(), self.style_var.get())
        self.game_id += 1
        self.board.goban = Goban(self.board_size, komi)
        self.board.last_move = None
        self.board.redraw()
        self.busy = True
        self.game_over = False
        self.board.locked = True
        self.set_status("正在准备 AI…")
        self.counter_var.set("")
        self.inbox.put({
            "action": "new",
            "game_id": self.game_id,
            "profile": profile,
            "human_color": self.human_color,
            "board_size": self.board_size,
            "komi": komi,
            "handicap": self.handicap_var.get(),
            "nice_pacing": bool(self.pacing_var.get()),
            "visits": int(self.visits_var.get()),
        })

    def submit_human_move(self, coord: str):
        if self.busy or self.game_over:
            return
        self.busy = True
        self.board.locked = True
        self.set_status("AI 思考中…")
        self.inbox.put({"action": "move", "coord": coord})

    def do_pass(self):
        if self.busy or self.game_over:
            return
        self.busy = True
        self.board.locked = True
        self.set_status("AI 思考中…")
        self.inbox.put({"action": "pass"})

    def undo(self):
        if self.busy:
            return
        self.busy = True
        self.board.locked = True
        self.set_status("悔棋中…")
        self.inbox.put({"action": "undo"})

    def resign(self):
        if self.game_over:
            return
        self.game_over = True
        self.board.locked = True
        self.set_status("你认输了。")
        self.log("你认输了。")

    def build_sgf(self) -> str:
        """SGF coordinates are two letters, both 0-based from the top-left.
        Note this is NOT the GTP alphabet: GTP skips 'I', SGF does not."""
        g = self.board.goban
        handicap = self.handicap_var.get()
        parts = ["(;GM[1]FF[4]CA[UTF-8]AP[weiqi-peilian:1.0]",
                 f"SZ[{self.board_size}]KM[{self.komi_var.get()}]",
                 f"PB[{'AI' if self.human_color == WHITE else '你'}]",
                 f"PW[{'AI' if self.human_color == BLACK else '你'}]"]
        if handicap >= 2:
            parts.append(f"HA[{handicap}]")
        body = []
        for i, (color, x, y) in enumerate(g.moves):
            c = "B" if color == BLACK else "W"
            if handicap >= 2 and i < handicap:
                body.append(f"AB[{sgf_coord(x, y)}]")
            elif x < 0:
                body.append(f";{c}[]")
            else:
                body.append(f";{c}[{sgf_coord(x, y)}]")
        return "".join(parts) + "".join(body) + ")"

    def save_sgf_to(self, path: str) -> bool:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.build_sgf())
        return True

    def save_to_trainer(self):
        """Send the current game to the problem book (错题本 tab) for analysis."""
        g = self.board.goban
        if not g.moves:
            messagebox.showinfo(APP_TITLE, "还没有对局可以收录。")
            return
        moves = [["B" if c == BLACK else "W", to_gtp(x, y, self.board_size)]
                 for (c, x, y) in g.moves if x >= 0]
        black = "你" if self.human_color == BLACK else "AI"
        white = "AI" if self.human_color == BLACK else "你"
        name = f"对弈_{time.strftime('%m月%d日_%H点%M')}"
        top = self.winfo_toplevel()
        if hasattr(top, "send_to_trainer"):
            top.send_to_trainer(moves,
                                colour=("B" if self.human_color == BLACK else "W"),
                                black=black, white=white, name=name)
        else:
            messagebox.showinfo(APP_TITLE, "当前窗口不支持错题本。")

    def save_sgf(self):
        if not self.board.goban.moves:
            messagebox.showinfo(APP_TITLE, "还没有落子，没什么可保存的。")
            return
        path = filedialog.asksaveasfilename(
            title="保存棋谱", defaultextension=".sgf",
            filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")],
            initialfile="陪练对局.sgf")
        if not path:
            return
        self.save_sgf_to(path)
        self.log(f"棋谱已保存：{os.path.basename(path)}")
        self.set_status("棋谱已保存。")

    # ------------------------------------------------------------- ui update

    def _pump(self):
        try:
            while True:
                msg = self.outbox.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        if self._pump_job is not None:
            self._pump_job = self.after(60, self._pump)

    def _handle(self, msg: dict):
        kind = msg["kind"]
        gid = msg.get("game_id")
        if gid is not None and gid != self.game_id:
            # left over from a previous game: applying it would corrupt the
            # new board and wrongly unlock the UI
            return
        if kind == "log":
            self.log(msg["text"])
        elif kind == "status":
            self.set_status(msg["text"])
        elif kind == "thinking":
            if msg["value"]:
                self.set_status("AI 思考中…")
        elif kind == "assessment":
            self.assessment = msg["data"]
            self._update_counter()
        elif kind == "new_ok":
            ai_first = bool(msg.get("ai_first"))
            self.game_over = False
            self.board.goban = Goban(self.board_size, float(self.komi_var.get() or 7.5))
            for x, y in msg["handicap_points"]:
                try:
                    self.board.goban.play(BLACK, x, y)
                except IllegalMove:
                    pass
            self.board.last_move = None
            self.board.redraw()
            if ai_first:
                # stay locked until the AI's opening move actually arrives
                self.busy = True
                self.board.locked = True
                self.set_status("AI 先行…")
            else:
                self.busy = False
                self.board.locked = False
                self.set_status("该你下了")
        elif kind == "human_move":
            coord = msg["coord"]
            if coord.lower() != "pass":
                pos = from_gtp(coord, self.board_size)
                if pos:
                    try:
                        self.board.goban.play(self.human_color, *pos)
                        self.board.last_move = pos
                    except IllegalMove as e:
                        self.log(f"界面拒绝了 {coord}：{e}")
            else:
                self.board.goban.play_pass(self.human_color)
            self.board.redraw()
            self._update_counter()
        elif kind == "ai_move":
            coord = msg["coord"]
            if coord.lower() == "resign":
                self.game_over = True
                self.board.locked = True
                self.set_status("AI 认输，你赢了！")
                self.log("AI 认输，你赢了！")
                return
            ai_color = WHITE if self.human_color == BLACK else BLACK
            if coord.lower() != "pass":
                pos = from_gtp(coord, self.board_size)
                if pos:
                    try:
                        self.board.goban.play(ai_color, *pos)
                        self.board.last_move = pos
                        self.log(f"AI 下 {coord}（{msg.get('seconds', 0):.1f}秒）")
                    except IllegalMove as e:
                        self.log(f"严重：AI 的 {coord} 在界面非法（{e}）")
            else:
                self.board.goban.play_pass(ai_color)
            self.board.redraw()
            self._update_counter()
            self.busy = False
            self.board.locked = False
            self.set_status("该你下了")
        elif kind == "undo_ok":
            g = Goban(self.board_size, float(self.komi_var.get() or 7.5))
            for color, coord in msg["moves"]:
                pos = from_gtp(coord, self.board_size)
                if pos:
                    try:
                        g.play(color, *pos)
                    except IllegalMove:
                        pass
            self.board.goban = g
            self.board.last_move = g.last_move()
            self.board.redraw()
            self.busy = False
            self.board.locked = False
            self._update_counter()
            self.set_status("已悔棋，该你下了。")
        elif kind == "gameover":
            self.game_over = True
            self.board.locked = True
            self.busy = False
            self.set_status(msg["text"])
            self.log(msg["text"])
        elif kind == "error":
            self.busy = False
            self.board.locked = False
            self.set_status(msg["text"])
            self.log("错误：" + msg["text"])

    def _update_counter(self):
        """Move count, captures, and the net's own position assessment.

        The old line used goban.area_score(), which only counted stones and
        fully enclosed empty points. Measured against KataGo it was off by up to
        82 points -- it called a game that black led by 80 "dead even".
        """
        g = self.board.goban
        parts = [f"手数 {g.move_number():3d}",
                 f"提子 黑{g.captures[BLACK]} 白{g.captures[WHITE]}"]
        ev = self.assessment or {}
        lead = ev.get("blackLead")
        if lead is not None:
            who = "黑" if lead >= 0 else "白"
            parts.append(f"形势 {who}领先 {abs(lead):.1f} 目")
            # The winrate is deliberately NOT shown: measured on this net it is
            # badly calibrated (1 point of komi moved it 20 points), and it reads
            # as absurd in an even opening -- black sits near 33% before a single
            # stone is on the board.
        else:
            parts.append("形势 计算中…")
        self.counter_var.set("   ".join(parts))

    def _on_close(self):
        self.shutdown()
        self.destroy()


class App(tk.Tk):
    """Standalone launcher, kept so app.py still runs on its own. Attribute
    access is delegated to the inner frame so existing callers (tests, the
    selftest) keep working unchanged."""

    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.resizable(False, False)
        self.frame = GameFrame(self)
        self.frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def __getattr__(self, name):
        # only called when normal lookup fails; guard against recursion before
        # self.frame exists
        if name == "frame":
            raise AttributeError(name)
        return getattr(self.frame, name)

    def _on_close(self):
        try:
            self.frame.shutdown()
        finally:
            self.destroy()


def main():
    if "--selftest" in sys.argv:
        # windowed builds have no console, so the report goes to a file
        import selftest
        selftest.write_report(ROOT)
        return
    try:
        app = App()
        app.mainloop()
    except Exception:
        import traceback
        msg = traceback.format_exc()
        # A windowed (no-console) build shows nothing on a crash, so leave a
        # breadcrumb next to the exe as well.
        try:
            with open(os.path.join(ROOT, "error.log"), "w", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_TITLE, msg[-1500:])
            root.destroy()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
