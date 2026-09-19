"""打谱分析模式 —— free placement with live KataGo feedback.

Place stones freely (playing through a game record, exploring a variation, or
setting up a problem) and after every stone the engine reports where it would
play and what each candidate is worth.

Design notes:
  * Analysis runs in a worker thread so the board never freezes.
  * Every request carries a generation number; results from an older generation
    are dropped. Without this, a slow answer for an earlier position would paint
    itself onto a newer board -- the same class of bug that hit the game mode.
  * The engine's winrate is shown only as a secondary number: it is measurably
    badly calibrated at low visits (1 point of komi moved it 20 points), while
    scoreLead is reliable. Score leads the display.
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
from analysis import KataGoAnalysis, AnalysisError  # noqa: E402
from goban import (BLACK, EMPTY, WHITE, Goban, IllegalMove, from_gtp,  # noqa: E402
                   from_sgf_coord, sgf_coord, star_points, to_gtp)
from katago_engine import ROOT  # noqa: E402

WOOD = "#E3B778"
WOOD_DARK = "#C99B57"
LINE = "#3B2A16"
STAR = "#2A1E10"

STUDENT_RANKS = [
    ("20级", "preaz_20k"), ("15级", "preaz_15k"), ("10级", "preaz_10k"),
    ("8级", "preaz_8k"), ("5级", "preaz_5k"), ("3级", "preaz_3k"),
    ("1级", "preaz_1k"), ("1段", "rank_1d"), ("3段", "rank_3d"),
    ("不显示", ""),
]
STUDENT_LABELS = [label for label, _ in STUDENT_RANKS]

# A second, stronger reference so the explanation can show the whole ladder:
# what this student's level plays, what a strong human plays, and what the AI
# considers best. The gap between those three is the actual lesson.
EXPERT_RANKS = [
    ("不显示", ""), ("5段", "rank_5d"), ("7段", "rank_7d"), ("9段", "rank_9d"),
    ("职业风格", "proyear_2020"),
]
EXPERT_LABELS = [label for label, _ in EXPERT_RANKS]


# --------------------------------------------------------------------- worker

class AnalysisWorker(threading.Thread):
    """Owns one KataGo analysis process. Latest request wins."""

    def __init__(self, inbox: queue.Queue, outbox: queue.Queue):
        super().__init__(daemon=True)
        self.inbox = inbox
        self.outbox = outbox
        self.engine: KataGoAnalysis | None = None
        self.stopped = False
        self._latest_gen = -1

    def post(self, kind: str, **kw):
        self.outbox.put({"kind": kind, **kw})

    def run(self):
        while not self.stopped:
            try:
                req = self.inbox.get(timeout=0.2)
            except queue.Empty:
                continue
            action = req.get("action")
            if action == "quit":
                break
            if action == "analyze":
                self._analyze(req)
            elif action == "review":
                self._review(req)
        if self.engine:
            self.engine.close()

    def _review(self, req):
        """Full-game review: analyse every turn and render the explanation
        report. Runs in this thread, so position analysis waits its turn."""
        moves = req["moves"]
        profile = req.get("profile") or "preaz_10k"
        visits = int(req.get("visits", 150))
        try:
            self._ensure_engine()
        except Exception as e:
            self.post("error", text=f"引擎启动失败：{e}")
            return
        try:
            from explain import (build_turn_facts, fill_missing_losses,
                                 grid_after, render_report)
            self.post("review_progress",
                      text=f"正在逐手分析 {len(moves)} 手，请稍候…")
            results = self.engine.analyze_game(
                moves,
                profile_for_turn=[profile] * len(moves),
                turns=list(range(len(moves))),
                max_visits=visits,
            )
            facts = []
            # replay the game so each turn's explanation can name actual groups
            from goban import Goban, from_gtp
            board = Goban(19)
            for t in range(len(moves)):
                color, mv = moves[t]
                if t in results:
                    f = build_turn_facts(t, results[t], mv, profile,
                                         grid=[row[:] for row in board.grid])
                    facts.append(f)
                # advance the board past this move
                c = 1 if color == "B" else 2
                pos = from_gtp(mv, 19)
                if pos:
                    ok, _ = board.is_legal(c, pos[0], pos[1])
                    if ok:
                        board.play(c, pos[0], pos[1])
            fill_missing_losses(facts, results)
            meta = {
                "black_name": "本局黑方", "white_name": "本局白方",
                "student_profile": profile, "version": self.engine.version,
                "komi": req.get("komi", 7.5), "rules": "chinese",
            }
            text = render_report(facts, meta, top_n=int(req.get("top", 8)))
            self.post("review_done", text=text, turns=len(facts))
        except Exception as e:
            self.post("error", text=f"复盘失败：{type(e).__name__}: {e}")

    def _ensure_engine(self):
        if self.engine and self.engine.alive:
            return
        self.post("status", text="正在启动分析引擎…（通常约 15 秒；"
                                 "若这台显卡第一次跑这个网络，需要几分钟自动调优）")
        eng = KataGoAnalysis()
        eng.start()
        self.engine = eng
        self.post("engine_ready", version=eng.version)

    def _drain_stale(self):
        """Drop queued requests that a newer one has already superseded."""
        while True:
            try:
                nxt = self.inbox.get_nowait()
            except queue.Empty:
                return
            if nxt.get("action") == "analyze" and nxt.get("gen", -1) > self._latest_gen:
                self.inbox.put(nxt)
                return
            if nxt.get("action") == "quit":
                self.inbox.put(nxt)
                return

    def _analyze(self, req):
        gen = req["gen"]
        self._latest_gen = gen
        self._drain_stale()
        moves = req["moves"]
        visits = req.get("visits", 120)
        profile = req.get("profile") or None
        ref_profile = req.get("ref_profile") or None
        try:
            self._ensure_engine()
        except Exception as e:
            self.post("error", text=f"引擎启动失败：{e}")
            return
        if gen != self._latest_gen:
            return

        def build(prof):
            q = {
                "moves": moves,
                "rules": "chinese",
                "komi": req.get("komi", 7.5),
                "boardXSize": 19, "boardYSize": 19,
                "analyzeTurns": [len(moves)],
                "maxVisits": visits,
                "includeOwnership": True,
                "includePolicy": True,
                "includeMovesOwnership": False,
            }
            if prof:
                q["overrideSettings"] = {
                    "humanSLProfile": prof,
                    "ignorePreRootHistory": False,
                    "humanSLRootExploreProbWeightless": 0.5,
                    "humanSLCpuctPermanent": 2.0,
                    "rootNumSymmetriesToSample": 2,
                }
            return q

        try:
            t0 = time.time()
            r = self.engine.query(build(profile), expect_turns=1, timeout=900)
            # Second pass for the stronger reference. Only the human policy is
            # kept from it, so it can run cheaply.
            if ref_profile:
                try:
                    r2 = self.engine.query(dict(build(ref_profile), maxVisits=max(40, visits // 3)),
                                           expect_turns=1, timeout=900)
                    r["humanPolicyRef"] = r2.get("humanPolicy")
                    r["_ref_profile"] = ref_profile
                    ref_infos = {m["move"]: m for m in (r2.get("moveInfos") or [])}
                    r["_ref_leads"] = {k: v.get("scoreLead") for k, v in ref_infos.items()}
                except Exception as e:
                    r["_ref_error"] = str(e)[:120]
            dt = time.time() - t0
            if gen != self._latest_gen:
                return          # superseded while we were computing
            self.post("result", gen=gen, data=r, seconds=dt)
        except AnalysisError as e:
            if gen == self._latest_gen:
                self.post("error", text=f"分析失败：{e}")
        except Exception as e:
            if gen == self._latest_gen:
                self.post("error", text=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------- board

class StudyBoard(tk.Canvas):
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
        self.candidates: list[dict] = []
        self.best_move: tuple[int, int] | None = None
        self.ownership: list[float] | None = None
        self.show_ownership = False
        # variation preview: (colour, x, y, step) drawn as faint ghost stones
        self.pv_moves: list[tuple[int, int, int, int]] = []
        self.show_pv = False
        self.bind("<Button-1>", self._on_click)
        self.bind("<Button-3>", self._on_right)
        self.bind("<Motion>", self._on_motion)
        self._hover: tuple[int, int] | None = None
        self.redraw()

    # geometry -------------------------------------------------------------
    def _px(self, x: int) -> int:
        return self.margin + x * self.cell

    def fit_to(self, avail_w: int, avail_h: int) -> None:
        """Grow or shrink the board to use the available space, instead of
        sitting at one fixed size in the corner of a maximised window.

        Capped so the board cannot crowd out the explanation panel -- at 45px
        per intersection it had squeezed the text column down to ~220px, which
        is unreadable.
        """
        n = self.size
        usable = min(avail_w, avail_h)
        cell = int(usable / (n - 1 + 2.2))
        cell = max(16, min(38, cell))
        if cell == self.cell:
            return
        self.cell = cell
        self.margin = int(cell * 1.1)
        px = self.margin * 2 + cell * (n - 1)
        self.configure(width=px, height=px)
        self.redraw()

    def _xy_from_event(self, ev):
        gx = round((ev.x - self.margin) / self.cell)
        gy = round((ev.y - self.margin) / self.cell)
        if not (0 <= gx < self.size and 0 <= gy < self.size):
            return None
        if abs(self._px(gx) - ev.x) > self.cell * 0.5:
            return None
        if abs(self._px(gy) - ev.y) > self.cell * 0.5:
            return None
        return gx, gy

    # events ---------------------------------------------------------------
    def _on_motion(self, ev):
        pos = self._xy_from_event(ev)
        if pos != self._hover:
            self._hover = pos
            self.redraw()

    def _on_click(self, ev):
        pos = self._xy_from_event(ev)
        if pos:
            self.app.place_stone(*pos, color=None)

    def _on_right(self, ev):
        pos = self._xy_from_event(ev)
        if pos and self.goban.get(*pos) != EMPTY:
            self.app.erase_stone(*pos)

    # drawing --------------------------------------------------------------
    def redraw(self):
        self.delete("all")
        n, c, m = self.size, self.cell, self.margin
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        self.create_rectangle(0, 0, w, h, fill=WOOD, outline="")
        for i in range(3):
            off = i * 4
            self.create_rectangle(off, off, w - off, h - off, outline=WOOD_DARK)
        for i in range(n):
            p = self._px(i)
            self.create_line(self._px(0), p, self._px(n - 1), p, fill=LINE, width=1)
            self.create_line(p, self._px(0), p, self._px(n - 1), fill=LINE, width=1)
        r = max(2, c // 11)
        for x, y in star_points(n):
            self.create_oval(self._px(x) - r, self._px(y) - r,
                             self._px(x) + r, self._px(y) + r, fill=STAR, outline=STAR)

        # coordinate labels on all four sides. Letters skip 'I' (standard Go
        # notation: A..T), numbers count down from the top as GTP expects.
        # The play-mode board had these from the start; the study board was
        # missing them.
        from goban import GTP_LETTERS
        fsize = max(7, c // 3)
        for i in range(n):
            p = self._px(i)
            letter = GTP_LETTERS[i]
            number = str(n - i)
            self.create_text(p, m * 0.45, text=letter, fill=LINE,
                             font=("Segoe UI", fsize))
            self.create_text(p, self._px(n - 1) + m * 0.55, text=letter,
                             fill=LINE, font=("Segoe UI", fsize))
            self.create_text(m * 0.45, p, text=number, fill=LINE,
                             font=("Segoe UI", fsize))
            self.create_text(self._px(n - 1) + m * 0.55, p, text=number,
                             fill=LINE, font=("Segoe UI", fsize))

        # ownership overlay (stipple fakes translucency in tk)
        if self.show_ownership and self.ownership:
            for y in range(n):
                for x in range(n):
                    v = self.ownership[y * n + x]
                    if abs(v) < 0.35:
                        continue
                    col = "#111111" if v > 0 else "#ffffff"
                    self.create_rectangle(
                        self._px(x) - c * 0.34, self._px(y) - c * 0.34,
                        self._px(x) + c * 0.34, self._px(y) + c * 0.34,
                        fill=col, outline="", stipple="gray25")

        if self._hover and self.goban.get(*self._hover) == EMPTY:
            hx, hy = self._hover
            rr = c * 0.44
            self.create_oval(self._px(hx) - rr, self._px(hy) - rr,
                             self._px(hx) + rr, self._px(hy) + rr,
                             outline="#6b5a3e", width=1)

        # variation preview, drawn under the real stones: the student should be
        # able to SEE the expected continuation instead of imagining it
        if self.show_pv and self.pv_moves:
            for color, px_, py_, step in self.pv_moves:
                rr2 = c * 0.40
                fill = "#444444" if color == BLACK else "#f2f2ee"
                self.create_oval(self._px(px_) - rr2, self._px(py_) - rr2,
                                 self._px(px_) + rr2, self._px(py_) + rr2,
                                 fill=fill, outline="#d0d0c8", width=1,
                                 stipple="gray50")
                self.create_text(self._px(px_), self._px(py_), text=str(step),
                                 fill="#c62828",
                                 font=("Segoe UI", max(7, int(c * 0.3)), "bold"))

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

        # candidate moves: numbered discs sized by how good they are
        if self.candidates:
            leads = [cd["lead"] for cd in self.candidates if cd.get("lead") is not None]
            best_lead = max(leads) if leads else 0.0
            for i, cd in enumerate(self.candidates):
                x, y = cd["xy"]
                gap = best_lead - cd["lead"] if cd.get("lead") is not None else 0.0
                if gap < 0.8:
                    col, rad = "#2e7d32", 0.40
                elif gap < 3:
                    col, rad = "#1565c0", 0.34
                elif gap < 8:
                    col, rad = "#ef6c00", 0.29
                else:
                    col, rad = "#9e9e9e", 0.24
                R = c * rad
                self.create_oval(self._px(x) - R, self._px(y) - R,
                                 self._px(x) + R, self._px(y) + R,
                                 fill=col, outline="#ffffff", width=1)
                txt = cd.get("label", str(i + 1))
                self.create_text(self._px(x), self._px(y), text=txt, fill="#ffffff",
                                 font=("Segoe UI", max(7, int(c * rad * 0.95)), "bold"))

        if self.best_move:
            bx, by = self.best_move
            R = c * 0.50
            self.create_oval(self._px(bx) - R, self._px(by) - R,
                             self._px(bx) + R, self._px(by) + R,
                             outline="#d32f2f", width=2)

        if self.last_move:
            lx, ly = self.last_move
            v = self.goban.get(lx, ly)
            col = "#ff3b30" if v == BLACK else "#e02020"
            r2 = c * 0.15
            self.create_oval(self._px(lx) - r2, self._px(ly) - r2,
                             self._px(lx) + r2, self._px(ly) + r2,
                             fill=col, outline="")


# ------------------------------------------------------------------------ app

class StudyFrame(tk.Frame):
    """打谱分析模式 —— as a Frame so it can live in a tab."""

    def __init__(self, master, **kw):
        super().__init__(master, bg="#f0ece3", **kw)
        self.inbox: queue.Queue = queue.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.worker = AnalysisWorker(self.inbox, self.outbox)
        self.worker.start()

        self.gen = 0
        self.busy = False
        self.next_color = BLACK
        self.auto_alternate = True
        self.history: list[tuple[int, int, int]] = []   # (color, x, y) for undo
        self.engine_version = ""
        self.last_result: dict | None = None
        self.startup_done = False
        self._pump_job = None

        self._build_ui()
        self._pump_job = self.after(60, self._pump)
        self.set_status("点棋盘落子，AI 会自动分析并标出推荐点。右键可擦掉一颗子。")

    def ensure_analysis(self):
        """Start the engine + first analysis. Called when the tab is first
        opened, so launching the app does not pay for two engines at once."""
        if not self.startup_done:
            self.startup_done = True
            self.request_analysis()

    def shutdown(self):
        """Stop the worker and cancel the pending timer, so a queued callback
        cannot fire after the widget is gone (Tcl: 'invalid command name')."""
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
        """Cancel the timer on every destruction path; cancelling only in
        shutdown() left Tcl complaining about a stale callback."""
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        super().destroy()

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        pad = dict(padx=6, pady=3)
        left = tk.Frame(self, bg="#f0ece3", width=232)
        # Lock the control column: its natural width was ~507px because of the
        # explanatory labels, and that was stealing space from the explanation
        # panel. Fixed width + no propagation keeps it at 232px.
        left.grid_propagate(False)
        left.grid(row=0, column=0, sticky="ns", padx=(10, 4), pady=10)

        tk.Label(left, text="打谱分析", bg="#f0ece3",
                 font=("Microsoft YaHei", 14, "bold")).pack(anchor="w", pady=(0, 2))
        tk.Label(left, text="自由摆子，即时看 AI 判断", bg="#f0ece3", fg="#666",
                 font=("Microsoft YaHei", 8)).pack(anchor="w", pady=(0, 6))

        box = tk.LabelFrame(left, text="下一手", bg="#f0ece3",
                            font=("Microsoft YaHei", 9))
        box.pack(fill="x", pady=6)
        self.color_var = tk.StringVar(value="黑")
        for label in ("黑", "白"):
            tk.Radiobutton(box, text=label, value=label, variable=self.color_var,
                           bg="#f0ece3", font=("Microsoft YaHei", 9),
                           command=self._on_color).pack(side="left", padx=8)
        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(box, text="自动交换", variable=self.auto_var, bg="#f0ece3",
                       font=("Microsoft YaHei", 9)).pack(side="left", padx=4)

        box2 = tk.LabelFrame(left, text="显示", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box2.pack(fill="x", pady=4)
        row = tk.Frame(box2, bg="#f0ece3"); row.pack(anchor="w", padx=6)
        tk.Label(row, text="候选点数", bg="#f0ece3").pack(side="left")
        self.topn_var = tk.IntVar(value=6)
        ttk.Spinbox(row, from_=1, to=15, width=3, textvariable=self.topn_var,
                    command=self.request_analysis).pack(side="left", padx=4)
        row2 = tk.Frame(box2, bg="#f0ece3"); row2.pack(anchor="w", padx=6)
        tk.Label(row2, text="搜索量", bg="#f0ece3").pack(side="left")
        # 200 on CUDA feels like the old 150 on OpenCL (about half a second on a
        # cold position) while searching a third harder. Raising it costs time
        # roughly linearly on cold positions; warm ones are nearly free.
        self.visits_var = tk.IntVar(value=200)
        ttk.Spinbox(row2, from_=20, to=2000, width=5, increment=50,
                    textvariable=self.visits_var,
                    command=self.request_analysis).pack(side="left", padx=4)
        self.own_var = tk.BooleanVar(value=False)
        tk.Checkbutton(box2, text="显示地盘归属", variable=self.own_var, bg="#f0ece3",
                       font=("Microsoft YaHei", 9),
                       command=self._on_toggle_own).pack(anchor="w", padx=6)
        self.pv_var = tk.BooleanVar(value=False)
        tk.Checkbutton(box2, text="显示后续变化", variable=self.pv_var, bg="#f0ece3",
                       font=("Microsoft YaHei", 9),
                       command=self._on_toggle_pv).pack(anchor="w", padx=6)

        box3 = tk.LabelFrame(left, text="对照学生水平", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box3.pack(fill="x", pady=4)
        self.rank_var = tk.StringVar(value="10级")
        ttk.Combobox(box3, textvariable=self.rank_var, values=STUDENT_LABELS,
                     state="readonly", width=9,
                     postcommand=self.request_analysis).pack(side="left", **pad)
        row3 = tk.Frame(box3, bg="#f0ece3"); row3.pack(anchor="w", padx=6)
        # Off by default: the extra query doubles the analysis time, and the
        # student-level contrast alone is usually enough.
        self.ref_on = tk.BooleanVar(value=False)
        tk.Checkbutton(row3, text="加高手对照", variable=self.ref_on, bg="#f0ece3",
                       font=("Microsoft YaHei", 9),
                       command=self.request_analysis).pack(side="left")
        self.ref_var = tk.StringVar(value="9段")
        ttk.Combobox(row3, textvariable=self.ref_var, values=EXPERT_LABELS,
                     state="readonly", width=6,
                     postcommand=self.request_analysis).pack(side="left", padx=4)

        btns = tk.Frame(left, bg="#f0ece3")
        btns.pack(fill="x", pady=(8, 4))
        self._btn(btns, "悔棋", self.undo).pack(fill="x", pady=2)
        r3 = tk.Frame(btns, bg="#f0ece3"); r3.pack(fill="x", pady=2)
        self._btn(r3, "清空棋盘", self.clear_board).pack(side="left", expand=True, fill="x")
        self._btn(r3, "重新分析", self.request_analysis).pack(side="left", expand=True, fill="x")
        r4 = tk.Frame(btns, bg="#f0ece3"); r4.pack(fill="x", pady=2)
        self._btn(r4, "载入棋谱", self.load_sgf).pack(side="left", expand=True, fill="x")
        self._btn(r4, "保存棋谱", self.save_sgf).pack(side="left", expand=True, fill="x")
        r5 = tk.Frame(btns, bg="#f0ece3"); r5.pack(fill="x", pady=2)
        self._btn(r5, "讲解这一手", self.explain_current).pack(
            side="left", expand=True, fill="x")
        self._btn(r5, "整盘复盘报告", self.review_game).pack(
            side="left", expand=True, fill="x")
        self._btn(btns, "存入错题本", self.save_to_trainer).pack(fill="x", pady=2)

        self.board = StudyBoard(self, self)
        self.board.grid(row=0, column=1, padx=10, pady=12, sticky="n")

        # The explanation used to sit UNDER the board in a 9-line box, so the
        # part that actually teaches was always below the fold. Put it beside
        # the board instead, full height, with a scrollbar.
        right = tk.Frame(self, bg="#f0ece3")
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 12), pady=12)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)

        head = tk.Frame(right, bg="#f0ece3")
        head.pack(fill="x")
        tk.Label(head, text="分析与讲解", bg="#f0ece3",
                 font=("Microsoft YaHei", 11, "bold")).pack(side="left")
        tk.Button(head, text="讲解这一手", command=self.explain_current,
                  font=("Microsoft YaHei", 9), relief="groove", bg="#e9e3d5"
                  ).pack(side="right")

        wrap = tk.Frame(right, bg="#faf8f3")
        wrap.pack(fill="both", expand=True, pady=(4, 0))
        sb = tk.Scrollbar(wrap, orient="vertical")
        sb.pack(side="right", fill="y")
        self.info = tk.Text(wrap, font=("Microsoft YaHei", 10), bg="#faf8f3",
                            relief="flat", wrap="word", padx=10, pady=8,
                            yscrollcommand=sb.set, width=48)
        self.info.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.info.yview)
        # tags for a bit of visual structure
        self.info.tag_configure("h1", font=("Microsoft YaHei", 12, "bold"),
                                foreground="#1a3d6b", spacing1=8, spacing3=4)
        self.info.tag_configure("sec", font=("Microsoft YaHei", 10, "bold"),
                                foreground="#7a4a00", spacing1=6)
        self.info.tag_configure("dim", foreground="#666666")
        self.info.configure(state="disabled")

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, anchor="w", bg="#dcd6c8",
                 font=("Microsoft YaHei", 9), padx=8).grid(
            row=1, column=0, columnspan=3, sticky="we")

        # keep the board as large as the window allows
        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, ev=None):
        try:
            # reserve room for the control column and a readable text column
            h = self.winfo_height() - 60
            w = max(320, self.winfo_width() - 800)
            self.board.fit_to(w, h)
        except Exception:
            pass

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, font=("Microsoft YaHei", 9),
                         width=12, relief="groove", bg="#e9e3d5",
                         activebackground="#dcd6c8")

    # -------------------------------------------------------------- actions
    def _on_color(self):
        self.next_color = BLACK if self.color_var.get() == "黑" else WHITE

    def _on_toggle_own(self):
        self.board.show_ownership = bool(self.own_var.get())
        self.board.redraw()

    def _on_toggle_pv(self):
        self.board.show_pv = bool(self.pv_var.get())
        self.board.redraw()

    def set_status(self, text: str):
        self.status_var.set(text)

    def _write_info(self, text: str):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", text)
        self.info.configure(state="disabled")

    def place_stone(self, x: int, y: int, color: int | None = None):
        self._on_color()
        c = self.next_color if color is None else color
        ok, why = self.goban_is_legal(c, x, y)
        if not ok:
            self.set_status(f"不能下在这里：{why}")
            return
        self.board.goban.play(c, x, y)
        self.history.append((c, x, y))
        self.board.last_move = (x, y)
        self.board.redraw()
        if self.auto_var.get():
            self.next_color = WHITE if c == BLACK else BLACK
            self.color_var.set("黑" if self.next_color == BLACK else "白")
        self.request_analysis()

    def goban_is_legal(self, color, x, y):
        return self.board.goban.is_legal(color, x, y)

    def erase_stone(self, x: int, y: int):
        """Right-click: remove one stone (and forget it from the undo history)."""
        g = self.board.goban
        if g.get(x, y) == EMPTY:
            return
        g.grid[y][x] = EMPTY
        self.history = [h for h in self.history if not (h[1] == x and h[2] == y)]
        g.moves = [(c, mx, my) for (c, mx, my) in g.moves if not (mx == x and my == y)]
        self.board.last_move = g.last_move()
        self.board.redraw()
        self.request_analysis()

    def undo(self):
        if not self.board.goban.moves:
            return
        self.board.goban.undo()
        if self.history:
            c, x, y = self.history.pop()
            self.next_color = c
            self.color_var.set("黑" if c == BLACK else "白")
        self.board.last_move = self.board.goban.last_move()
        self.board.redraw()
        self.request_analysis()

    def clear_board(self):
        self.board.goban = Goban(19)
        self.board.last_move = None
        self.history = []
        self.board.candidates = []
        self.board.best_move = None
        self.board.ownership = None
        self.board.redraw()
        self.request_analysis()

    def load_sgf(self):
        path = filedialog.askopenfilename(
            title="载入棋谱", filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            moves = parse_sgf_mainline(path)
        except Exception as e:
            messagebox.showerror("载入失败", str(e))
            return
        self.board.goban = Goban(19)
        self.history = []
        skipped = 0
        for color, coord in moves:
            c = BLACK if color == "B" else WHITE
            if coord is None:
                self.board.goban.play_pass(c)
                continue
            x, y = coord
            ok, _ = self.board.goban.is_legal(c, x, y)
            if not ok:
                skipped += 1
                continue
            self.board.goban.play(c, x, y)
            self.history.append((c, x, y))
        self.board.last_move = self.board.goban.last_move()
        self.board.redraw()
        msg = f"载入 {os.path.basename(path)}：{len(self.history)} 手"
        if skipped:
            msg += f"（{skipped} 手被跳过）"
        self.set_status(msg)
        self.request_analysis()

    def save_to_trainer(self):
        """Send the current game to the problem book (错题本 tab)."""
        g = self.board.goban
        if not g.moves:
            self.set_status("棋盘上还没有可收录的对局。")
            return
        moves = [["B" if c == BLACK else "W", to_gtp(x, y, 19)]
                 for (c, x, y) in g.moves if x >= 0]
        top = self.winfo_toplevel()
        name = f"打谱_{time.strftime('%m月%d日_%H点%M')}"
        if hasattr(top, "send_to_trainer"):
            # study games could be anyone's, so let the user pick the side
            top.send_to_trainer(moves, colour=None, name=name)
        else:
            self.set_status("当前窗口不支持错题本。")

    def save_sgf(self):
        path = filedialog.asksaveasfilename(
            title="保存棋谱", defaultextension=".sgf",
            filetypes=[("SGF 棋谱", "*.sgf")], initialfile="打谱.sgf")
        if not path:
            return
        parts = ["(;GM[1]FF[4]CA[UTF-8]AP[weiqi-study:0.1]SZ[19]KM[7.5]"]
        for color, x, y in self.history:
            parts.append(f";{'B' if color == BLACK else 'W'}[{sgf_coord(x, y)}]")
        parts.append(")")
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(parts))
        self.set_status(f"已保存：{os.path.basename(path)}")

    # ------------------------------------------------------------ analysis
    def _moves_for_engine(self):
        out = []
        for color, x, y in self.board.goban.moves:
            c = "B" if color == BLACK else "W"
            if x < 0:
                out.append([c, "pass"])
            else:
                out.append([c, to_gtp(x, y, 19)])
        return out

    def request_analysis(self):
        self.gen += 1
        profile = dict(STUDENT_RANKS).get(self.rank_var.get(), "")
        # only run the second (expert) query when the user asked for it
        ref = dict(EXPERT_RANKS).get(self.ref_var.get(), "") if self.ref_on.get() else ""
        self.busy = True
        self.set_status("AI 分析中…")
        self.inbox.put({
            "action": "analyze",
            "gen": self.gen,
            "moves": self._moves_for_engine(),
            "visits": int(self.visits_var.get()),
            "profile": profile,
            "ref_profile": ref,
            "komi": 7.5,
        })

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
        if kind == "status":
            self.set_status(msg["text"])
        elif kind == "engine_ready":
            self.engine_version = msg.get("version", "")
        elif kind == "review_progress":
            self.set_status(msg["text"])
        elif kind == "review_done":
            self.busy = False
            text = msg["text"]
            self._write_info(text)
            path = os.path.join(ROOT, "samples", "打谱复盘报告.txt")
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.set_status(f"复盘完成（{msg.get('turns', 0)} 手），"
                                f"报告已写入 {path}")
            except Exception as e:
                self.set_status(f"复盘完成，但写文件失败：{e}")
        elif kind == "error":
            self.busy = False
            self.set_status(msg["text"])
        elif kind == "result":
            if msg["gen"] != self.gen:
                return              # stale answer for an older position
            self._render_result(msg["data"], msg.get("seconds", 0.0))

    def _render_result(self, r: dict, seconds: float):
        self.busy = False
        self.last_result = r
        ri = r.get("rootInfo", {})
        mover = ri.get("currentPlayer", "B")
        sign = 1.0 if mover == "B" else -1.0
        mis = r.get("moveInfos", []) or []
        top_n = int(self.topn_var.get())

        self.board.candidates = []
        self.board.best_move = None
        self.board.pv_moves = []
        if mis:
            b = mis[0]
            pos = from_gtp(b["move"], 19)
            self.board.best_move = pos

        # variation preview: number the expected continuation so it can be read
        # off the board rather than imagined from a move list
        if mis:
            pv = mis[0].get("pv") or []
            col = BLACK if mover == "B" else WHITE
            step = 0
            for mv in pv[:8]:
                pos = from_gtp(mv, 19)
                if not pos:
                    continue
                step += 1
                self.board.pv_moves.append((col, pos[0], pos[1], step))
                col = WHITE if col == BLACK else BLACK
        for i, mi in enumerate(mis[:top_n]):
            pos = from_gtp(mi["move"], 19)
            if not pos:
                continue
            lead = mi.get("scoreLead")
            self.board.candidates.append({
                "xy": pos,
                "move": mi["move"],
                "lead": sign * lead if lead is not None else None,
                "winrate": mi.get("winrate"),
                "visits": mi.get("visits", 0),
                "label": str(i + 1),
            })
        self.board.ownership = r.get("ownership")
        self.board.redraw()

        L = []
        who = "黑" if mover == "B" else "白"
        if mis:
            b0 = self.board.candidates[0] if self.board.candidates else None
            if b0:
                L.append(f"轮到{who}走。AI 推荐 {b0['move']}"
                         f"（{who}方{'领先' if (b0['lead'] or 0) >= 0 else '落后'} "
                         f"{abs(b0['lead'] or 0):.1f} 目）")
            L.append("")
            L.append("候选点（按目差排序，棋盘上标了序号）：")
            for i, cd in enumerate(self.board.candidates, 1):
                if cd["lead"] is None:
                    L.append(f"  {i}. {cd['move']:<5}")
                    continue
                if i == 1:
                    note = "（首选）"
                else:
                    d = (self.board.candidates[0]["lead"] or 0) - cd["lead"]
                    note = "（与首选相当）" if abs(d) < 0.05 else f"（差 {d:.1f} 目）"
                L.append(f"  {i}. {cd['move']:<5} 领先 {cd['lead']:+.1f} 目 {note}")
            pv = mis[0].get("pv") or []
            if pv:
                # the 为什么 block below repeats this in a teaching context, so
                # only show the raw sequence here
                L.append("")
                L.append("预计后续：" + " → ".join(pv[:6]))

        hp = r.get("humanPolicy")
        if hp:
            from explain import norm_policy
            hmap = norm_policy(hp, 19)
            top = sorted(hmap.items(), key=lambda kv: -kv[1])[:3]
            if top:
                L.append("")
                L.append(f"【{self.rank_var.get()}】这个水平的人常下："
                         + "、".join(f"{mv} {100*p:.0f}%" for mv, p in top))
                if mis and self.board.candidates:
                    best_mv = self.board.candidates[0]["move"]
                    if best_mv in hmap:
                        L.append(f"         AI 首选 {best_mv} 在该水平的落子倾向里占 "
                                 f"{100*hmap[best_mv]:.1f}%")
                    else:
                        L.append(f"         AI 首选 {best_mv} 几乎不在该水平的落子倾向里")

        href = r.get("humanPolicyRef")
        if href:
            from explain import norm_policy
            rmap = norm_policy(href, 19)
            top = sorted(rmap.items(), key=lambda kv: -kv[1])[:3]
            if top:
                L.append("")
                L.append(f"【{self.ref_var.get()}】高手在这个局面常下："
                         + "、".join(f"{mv} {100*p:.0f}%" for mv, p in top))
                if mis and self.board.candidates:
                    best_mv = self.board.candidates[0]["move"]
                    if best_mv in rmap:
                        L.append(f"         AI 首选 {best_mv} 在高手落子倾向里占 "
                                 f"{100*rmap[best_mv]:.1f}%"
                                 + ("（高手也认同）" if rmap[best_mv] > 0.25 else ""))
                    else:
                        L.append(f"         AI 首选 {best_mv} 不在高手的主要选择里"
                                 f"——这一手是人类容易忽略的")
        elif r.get("_ref_error"):
            L.append("")
            L.append(f"（高手对照未取到：{r['_ref_error']}）")

        # ------------------------------------------------------------ 讲解
        # The "why here" part is generated on every analysis, not only when the
        # button is pressed: the facts are already in hand, so it costs nothing
        # extra, and a student should not have to ask for the reason.
        try:
            from explain import build_position_facts, render_teaching_position
            profile = dict(STUDENT_RANKS).get(self.rank_var.get(), "") or "preaz_10k"
            facts = build_position_facts(r, profile, grid=self.board.goban.grid)
            teaching = render_teaching_position(facts)
            lines = teaching.split("\n")
            # "轮到X走" is already the panel's first line, and the variation is
            # already printed above -- drop both to avoid saying it twice
            kept = [ln for ln in lines
                    if ln.strip()
                    and not ln.startswith("轮到")
                    and not ln.startswith("【预计后续】")]
            if kept:
                L.append("")
                L.append("────────── 为 什 么 ──────────")
                L.extend(kept)
        except Exception as e:
            L.append("")
            L.append(f"（讲解生成失败：{type(e).__name__}: {e}）")

        analyze_time = f"分析耗时 {seconds:.2f}s" if seconds else ""
        self._write_info("\n".join(L))
        n = self.board.goban.move_number()
        self.set_status(f"第 {n} 手　·　{analyze_time}"
                        + (f"　·　引擎 {self.engine_version}" if self.engine_version else ""))

    # -------------------------------------------------------------- 讲解
    def explain_current(self):
        """Explain the position waiting to be played.

        The analysis we hold describes the board AFTER the last stone, so the
        engine's recommendation is for whoever moves next. Presenting it as a
        comment on the stone just placed would label the wrong player and the
        wrong move -- an early version did exactly that.
        """
        if not self.last_result:
            self.set_status("还没有分析结果，先落一子或点「重新分析」。")
            return
        try:
            from explain import (build_position_facts, is_decided,
                                 render_teaching_position)
        except Exception as e:
            self.set_status(f"讲解模块加载失败：{e}")
            return
        profile = dict(STUDENT_RANKS).get(self.rank_var.get(), "") or "preaz_10k"
        # hand the board in so the explanation can talk about groups, not just
        # numbers -- that is what makes it read like teaching
        f = build_position_facts(self.last_result, profile,
                                 grid=self.board.goban.grid)
        text = render_teaching_position(f)
        self._write_info(text)
        self.set_status("已生成当前局面的讲解。"
                        + ("（该局面已分出胜负，讲评意义有限）" if is_decided(f) else ""))

    def review_game(self):
        """Kick off a full-game review in the worker and write a report file."""
        moves = self._moves_for_engine()
        if len(moves) < 2:
            self.set_status("至少要有几手棋才能做整盘复盘。")
            return
        profile = dict(STUDENT_RANKS).get(self.rank_var.get(), "") or "preaz_10k"
        self.busy = True
        self.set_status(f"正在生成整盘复盘报告（{len(moves)} 手），请稍候…")
        self._write_info("正在逐手分析，请稍候……\n\n（分析完成后报告会写到文件，"
                         "同时在这里显示摘要。）")
        self.inbox.put({
            "action": "review",
            "moves": moves,
            "profile": profile,
            # reports are read carefully, so spend more visits than the live view
            "visits": max(1000, int(self.visits_var.get())),
            "top": 8,
            "komi": 7.5,
        })

    # -------------------------------------------------------------- closing
    def shutdown(self):
        try:
            self.inbox.put({"action": "quit"})
            time.sleep(0.15)
        except Exception:
            pass

    def _on_close(self):
        self.shutdown()
        self.destroy()


class StudyApp(tk.Tk):
    """Standalone launcher, kept so study.py still runs on its own."""

    def __init__(self):
        super().__init__()
        self.title("围棋 AI 打谱分析")
        self.resizable(False, False)
        self.frame = StudyFrame(self)
        self.frame.pack(fill="both", expand=True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.frame.ensure_analysis()

    def __getattr__(self, name):
        if name == "frame":
            raise AttributeError(name)
        return getattr(self.frame, name)

    def _on_close(self):
        try:
            self.frame.shutdown()
        finally:
            self.destroy()


# ---------------------------------------------------------------- sgf parsing

def parse_sgf_mainline(path: str) -> list[tuple[str, tuple[int, int] | None]]:
    """Main line only: (colour, (x, y) or None for a pass). Variations and setup
    stones are ignored -- good enough for playing through a record."""
    import re
    with open(path, encoding="utf-8", errors="ignore") as f:
        text = f.read()
    moves: list[tuple[str, tuple[int, int] | None]] = []
    for m in re.finditer(r";\s*([BW])\s*\[([a-z]{0,2})\]", text):
        color, coord = m.group(1), m.group(2)
        if not coord:
            moves.append((color, None))
            continue
        pos = from_sgf_coord(coord)
        if pos:
            moves.append((color, pos))
    return moves


def main():
    StudyApp().mainloop()


if __name__ == "__main__":
    main()
