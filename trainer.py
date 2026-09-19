"""错题本 —— practise from your own mistakes.

New, self-contained interface. The other two tabs (对弈 / 打谱分析) are not
touched by anything in this file.

Why this exists: a game review tells a student what went wrong once. A problem
set built from their OWN blunders is what actually changes habits, and it is
far more motivating than generic tsumego -- "this one is from your move 70".

Flow:
    import SGF  ->  batch analyse in the background  ->  problems appear
    pick a problem  ->  the position BEFORE the mistake is shown  ->  click
    where you would play  ->  compared against the AI's choice  ->  explained
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from analysis import AnalysisError, KataGoAnalysis  # noqa: E402
from goban import BLACK, EMPTY, WHITE, GTP_LETTERS, from_gtp, star_points  # noqa: E402
from katago_engine import ROOT  # noqa: E402
from library import MIN_PROBLEM_LOSS, Library, level_for  # noqa: E402
from study import parse_sgf_mainline  # noqa: E402

WOOD = "#E3B778"
WOOD_DARK = "#C99B57"
LINE = "#3B2A16"
STAR = "#2A1E10"


# --------------------------------------------------------------------- worker

class TrainerWorker(threading.Thread):
    """Imports and analyses games in the background, storing problems."""

    def __init__(self, inbox: queue.Queue, outbox: queue.Queue):
        super().__init__(daemon=True)
        self.inbox = inbox
        self.outbox = outbox
        self.engine: KataGoAnalysis | None = None
        self.stopped = False

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
        if self.engine:
            self.engine.close()

    def _ensure_engine(self):
        if self.engine and self.engine.alive:
            return
        self.post("status", text="正在启动分析引擎…（首次约 15 秒）")
        eng = KataGoAnalysis()
        eng.start()
        self.engine = eng
        self.post("engine_ready", version=eng.version)

    def _analyze(self, req):
        from explain import build_turn_facts, fill_missing_losses, grid_after

        lib: Library = req["library"]
        game_id = req["game_id"]
        moves = req["moves"]
        colour = req["colour"]                 # 'B' or 'W' -- whose mistakes
        visits = req.get("visits", 200)
        try:
            self._ensure_engine()
        except Exception as e:
            self.post("error", text=f"引擎启动失败：{e}")
            return
        try:
            self.post("progress", game_id=game_id,
                      text=f"正在分析（{len(moves)} 手，{visits} 搜索量）…")
            results = self.engine.analyze_game(
                moves, profile_for_turn=["preaz_10k"] * len(moves),
                turns=list(range(len(moves))), max_visits=visits)

            # replay the game so each turn has the real board
            from goban import Goban
            board = Goban(19)
            grids = []
            for t in range(len(moves)):
                grids.append([row[:] for row in board.grid])
                c = 1 if moves[t][0] == "B" else 2
                pos = from_gtp(moves[t][1], 19)
                if pos:
                    ok, _ = board.is_legal(c, pos[0], pos[1])
                    if ok:
                        board.play(c, pos[0], pos[1])

            facts = []
            for t in sorted(results):
                if t >= len(moves):
                    continue
                facts.append(build_turn_facts(t, results[t], moves[t][1],
                                              "preaz_10k", grid=grids[t]))
            fill_missing_losses(facts, results)

            lib.clear_problems(game_id)
            made = 0
            for f in facts:
                if f["mover"] != colour:
                    continue
                loss = f.get("loss")
                if loss is None or loss < MIN_PROBLEM_LOSS:
                    continue
                # board BEFORE the move: that is the position to present
                before = grids[f["turn"]]
                after = [row[:] for row in before]
                pos = from_gtp(f["played"], 19)
                if pos:
                    after[pos[1]][pos[0]] = 1 if colour == "B" else 2
                reason = ""
                try:
                    from explain import render_teaching
                    reason = render_teaching(f)
                except Exception:
                    pass
                lib.add_problem(
                    game_id, turn=f["turn"], move_no=f["move_number"],
                    colour=colour, played=f["played"], best=f.get("best_move"),
                    loss=round(loss, 2), level=level_for(loss),
                    winrate=f.get("root_winrate_mover"),
                    human_rank=f.get("human_rank"),
                    human_share=f.get("human_p_played"),
                    board=after, board_before=before, pv=f.get("pv"),
                    reason=reason)
                made += 1
            lib.mark_analyzed(game_id, colour, visits)
            self.post("game_done", game_id=game_id, problems=made)
        except AnalysisError as e:
            self.post("error", text=f"分析失败：{e}")
        except Exception as e:
            self.post("error", text=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------- board

class ProblemBoard(tk.Canvas):
    """Shows a position. Optionally accepts one click as an answer."""

    def __init__(self, master, size=19, cell=34, margin=34, **kw):
        self.size = size
        self.cell = cell
        self.margin = margin
        px = margin * 2 + cell * (size - 1)
        super().__init__(master, width=px, height=px, highlightthickness=0,
                         bg=WOOD, **kw)
        self.grid = [[EMPTY] * size for _ in range(size)]
        self.marker: tuple[int, int] | None = None      # the played move
        self.answer: tuple[int, int] | None = None      # student's click
        self.best: tuple[int, int] | None = None        # revealed answer
        self.accepts_click = False
        self.on_click = None
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self._hover = None
        self.redraw()

    def _px(self, i: int) -> int:
        return self.margin + i * self.cell

    def _xy(self, ev):
        gx = round((ev.x - self.margin) / self.cell)
        gy = round((ev.y - self.margin) / self.cell)
        if not (0 <= gx < self.size and 0 <= gy < self.size):
            return None
        if abs(self._px(gx) - ev.x) > self.cell * 0.5:
            return None
        if abs(self._px(gy) - ev.y) > self.cell * 0.5:
            return None
        return gx, gy

    def show(self, grid, marker=None, best=None):
        self.grid = [list(r) for r in grid]
        self.marker = marker
        self.best = best
        self.answer = None
        self.redraw()

    def _click(self, ev):
        if not self.accepts_click:
            return
        pos = self._xy(ev)
        if pos and self.on_click:
            self.answer = pos
            self.redraw()
            self.on_click(pos)

    def _motion(self, ev):
        p = self._xy(ev) if self.accepts_click else None
        if p != self._hover:
            self._hover = p
            self.redraw()

    def redraw(self):
        self.delete("all")
        n, c, m = self.size, self.cell, self.margin
        w = h = m * 2 + c * (n - 1)
        self.create_rectangle(0, 0, w, h, fill=WOOD, outline="")
        for i in range(3):
            o = i * 4
            self.create_rectangle(o, o, w - o, h - o, outline=WOOD_DARK)
        for i in range(n):
            p = self._px(i)
            self.create_line(self._px(0), p, self._px(n - 1), p, fill=LINE)
            self.create_line(p, self._px(0), p, self._px(n - 1), fill=LINE)
        r = max(2, c // 11)
        for x, y in star_points(n):
            self.create_oval(self._px(x) - r, self._px(y) - r,
                             self._px(x) + r, self._px(y) + r, fill=STAR, outline=STAR)
        fs = max(7, c // 3)
        for i in range(n):
            p = self._px(i)
            self.create_text(p, m * 0.45, text=GTP_LETTERS[i], fill=LINE,
                             font=("Segoe UI", fs))
            self.create_text(p, self._px(n - 1) + m * 0.55, text=GTP_LETTERS[i],
                             fill=LINE, font=("Segoe UI", fs))
            self.create_text(m * 0.45, p, text=str(n - i), fill=LINE,
                             font=("Segoe UI", fs))
            self.create_text(self._px(n - 1) + m * 0.55, p, text=str(n - i),
                             fill=LINE, font=("Segoe UI", fs))

        if self._hover and self.accepts_click and self.grid[self._hover[1]][self._hover[0]] == EMPTY:
            hx, hy = self._hover
            rr = c * 0.44
            self.create_oval(self._px(hx) - rr, self._px(hy) - rr,
                             self._px(hx) + rr, self._px(hy) + rr,
                             outline="#6b5a3e")

        rr = c * 0.46
        for y in range(n):
            for x in range(n):
                v = self.grid[y][x]
                if v == EMPTY:
                    continue
                fill = "#1a1a1a" if v == BLACK else "#f7f7f2"
                out = "#000000" if v == BLACK else "#8a8a80"
                self.create_oval(self._px(x) - rr, self._px(y) - rr,
                                 self._px(x) + rr, self._px(y) + rr,
                                 fill=fill, outline=out)

        # the mistake itself, then the student's answer and the right answer
        if self.marker:
            mx, my = self.marker
            rr2 = c * 0.17
            self.create_oval(self._px(mx) - rr2, self._px(my) - rr2,
                             self._px(mx) + rr2, self._px(my) + rr2,
                             fill="#e53935", outline="")
        if self.answer and self.answer != self.best:
            ax, ay = self.answer
            self.create_line(self._px(ax) - c * 0.28, self._px(ay) - c * 0.28,
                             self._px(ax) + c * 0.28, self._px(ay) + c * 0.28,
                             fill="#c62828", width=3)
            self.create_line(self._px(ax) - c * 0.28, self._px(ay) + c * 0.28,
                             self._px(ax) + c * 0.28, self._px(ay) - c * 0.28,
                             fill="#c62828", width=3)
        if self.best:
            bx, by = self.best
            R = c * 0.5
            self.create_oval(self._px(bx) - R, self._px(by) - R,
                             self._px(bx) + R, self._px(by) + R,
                             outline="#2e7d32", width=3)


# ------------------------------------------------------------------------ app

class TrainerFrame(tk.Frame):
    """错题本 —— a third interface, independent of the other two tabs."""

    def __init__(self, master, **kw):
        super().__init__(master, bg="#f0ece3", **kw)
        self.library = Library()
        self.inbox: queue.Queue = queue.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.worker = TrainerWorker(self.inbox, self.outbox)
        self.worker.start()
        self.current = None          # sqlite3.Row of the shown problem
        self.answered = False
        self._pump_job = None
        self._build_ui()
        self._pump_job = self.after(80, self._pump)
        self.refresh()

    def shutdown(self):
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
        self.library.close()

    def destroy(self):
        if self._pump_job is not None:
            try:
                self.after_cancel(self._pump_job)
            except Exception:
                pass
            self._pump_job = None
        super().destroy()

    # ------------------------------------------------------------------- ui
    def _build_ui(self):
        left = tk.Frame(self, bg="#f0ece3", width=250)
        left.grid_propagate(False)
        left.grid(row=0, column=0, sticky="ns", padx=(10, 4), pady=10)

        tk.Label(left, text="错题本", bg="#f0ece3",
                 font=("Microsoft YaHei", 14, "bold")).pack(anchor="w")
        tk.Label(left, text="从你自己的棋里出题", bg="#f0ece3", fg="#666",
                 font=("Microsoft YaHei", 8)).pack(anchor="w", pady=(0, 6))

        b = tk.Frame(left, bg="#f0ece3")
        b.pack(fill="x", pady=(0, 6))
        self._btn(b, "导入棋谱", self.import_sgf, 11).pack(side="left", expand=True, fill="x")
        self._btn(b, "开始分析", self.analyze_selected, 11).pack(side="left", expand=True, fill="x")

        box = tk.LabelFrame(left, text="棋谱库", bg="#f0ece3",
                            font=("Microsoft YaHei", 9))
        box.pack(fill="both", expand=True, pady=4)
        self.games_list = tk.Listbox(box, font=("Microsoft YaHei", 9), height=8,
                                     activestyle="none")
        self.games_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.games_list.bind("<<ListboxSelect>>", lambda e: self.refresh_problems())

        box2 = tk.LabelFrame(left, text="题目", bg="#f0ece3",
                             font=("Microsoft YaHei", 9))
        box2.pack(fill="both", expand=True, pady=4)
        self.level_var = tk.StringVar(value="全部")
        ttk.Combobox(box2, textvariable=self.level_var,
                     values=["全部", "大恶手", "严重失误", "失误", "不够精细"],
                     state="readonly", width=9,
                     postcommand=self.refresh_problems).pack(anchor="w", padx=4, pady=3)
        self.prob_list = tk.Listbox(box2, font=("Microsoft YaHei", 9), height=10,
                                    activestyle="none")
        self.prob_list.pack(fill="both", expand=True, padx=4, pady=4)
        self.prob_list.bind("<<ListboxSelect>>", self._on_pick_problem)

        self.hint = tk.Label(left, text="", bg="#f0ece3", fg="#666",
                             font=("Microsoft YaHei", 8), wraplength=230,
                             justify="left")
        self.hint.pack(anchor="w", pady=(4, 0))

        # ---------------------------------------------------------- middle
        mid = tk.Frame(self, bg="#f0ece3")
        mid.grid(row=0, column=1, sticky="n", padx=6, pady=10)
        self.board = ProblemBoard(mid)
        self.board.on_click = self._on_answer
        self.board.pack()
        self.prompt = tk.Label(mid, text="从左边选一道题", bg="#f0ece3",
                               font=("Microsoft YaHei", 11, "bold"))
        self.prompt.pack(pady=(8, 2))
        row = tk.Frame(mid, bg="#f0ece3")
        row.pack()
        self._btn(row, "看答案", self.reveal, 9).pack(side="left", padx=3)
        self._btn(row, "重做本题", self.retry, 9).pack(side="left", padx=3)
        self._btn(row, "下一题", self.next_problem, 9).pack(side="left", padx=3)

        # ----------------------------------------------------------- right
        right = tk.Frame(self, bg="#f0ece3")
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 10), pady=10)
        self.grid_columnconfigure(2, weight=1)
        self.grid_rowconfigure(0, weight=1)
        tk.Label(right, text="讲解", bg="#f0ece3",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w")
        wrap = tk.Frame(right, bg="#faf8f3")
        wrap.pack(fill="both", expand=True, pady=4)
        sb = tk.Scrollbar(wrap, orient="vertical")
        sb.pack(side="right", fill="y")
        self.info = tk.Text(wrap, font=("Microsoft YaHei", 10), bg="#faf8f3",
                            relief="flat", wrap="word", padx=10, pady=8,
                            yscrollcommand=sb.set, width=46)
        self.info.pack(side="left", fill="both", expand=True)
        sb.configure(command=self.info.yview)
        self.info.configure(state="disabled")

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, anchor="w", bg="#dcd6c8",
                 font=("Microsoft YaHei", 9), padx=8).grid(
            row=1, column=0, columnspan=3, sticky="we")

    def _btn(self, parent, text, cmd, width=12):
        return tk.Button(parent, text=text, command=cmd,
                         font=("Microsoft YaHei", 9), width=width,
                         relief="groove", bg="#e9e3d5",
                         activebackground="#dcd6c8")

    def set_status(self, text: str):
        self.status_var.set(text)

    def _write_info(self, text: str):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        self.info.insert("1.0", text)
        self.info.configure(state="disabled")

    # -------------------------------------------------------------- library
    def refresh(self):
        st = self.library.stats()
        self.hint.configure(
            text=f"棋谱 {st['games']} 盘（已分析 {st['analyzed']}）\n"
                 f"题目 {st['problems']} 道\n"
                 f"练过 {st['attempts']} 次，答对 {st['solved']} 次")
        self.games_list.delete(0, "end")
        self._games = self.library.games()
        for g in self._games:
            mark = "✓" if g.analyzed_at else "·"
            self.games_list.insert(
                "end", f"{mark} {g.name[:14]}  {g.problem_count}题")
        self.refresh_problems()

    def refresh_problems(self):
        sel = self.games_list.curselection()
        gid = self._games[sel[0]].id if (sel and hasattr(self, "_games")
                                         and sel[0] < len(self._games)) else None
        lvl = self.level_var.get()
        rows = self.library.problems(level=None if lvl == "全部" else lvl,
                                     game_id=gid)
        self._problems = rows
        self.prob_list.delete(0, "end")
        for r in rows:
            ok = "✓" if r["ever_ok"] else " "
            self.prob_list.insert(
                "end", f"{ok}{r['level']:<4} 第{r['move_no']}手 {r['played']}"
                       f" 亏{r['loss']:.1f}目")

    # --------------------------------------------------------------- import
    def import_sgf(self):
        paths = filedialog.askopenfilenames(
            title="导入棋谱（可多选）",
            filetypes=[("SGF 棋谱", "*.sgf"), ("所有文件", "*.*")])
        if not paths:
            return
        added = skipped = 0
        for p in paths:
            try:
                moves = [[c, f"{GTP_LETTERS[pos[0]]}{19-pos[1]}"]
                         for c, pos in parse_sgf_mainline(p) if pos is not None]
            except Exception:
                skipped += 1
                continue
            import re
            txt = ""
            try:
                with open(p, encoding="utf-8", errors="ignore") as f:
                    txt = f.read()
            except Exception:
                pass
            def grab(tag):
                m = re.search(rf"{tag}\[([^\]]*)\]", txt)
                return m.group(1) if m else ""
            gid = self.library.add_game(
                moves, black=grab("PB"), white=grab("PW"),
                result=grab("RE"), komi=7.5, path=p,
                name=os.path.basename(p))
            if gid:
                added += 1
            else:
                skipped += 1
        self.refresh()
        self.set_status(f"导入 {added} 盘" + (f"，跳过 {skipped} 盘（重复或无法解析）"
                                             if skipped else "")
                        + "。选一盘点「开始分析」出题。")

    def queue_analysis(self, game_id: int, colour: str | None = None):
        """Public entry: analyse a game that another tab just stored.

        `colour` limits which side's mistakes become problems ('B'/'W'); when it
        is None the user is asked, because a study-mode game could be anyone's.
        """
        g = self.library.get_game(game_id)
        if not g:
            self.set_status("没有找到这盘棋。")
            return
        moves = json.loads(g["moves"])
        if not moves:
            self.set_status("这盘棋没有可分析的着手。")
            return
        if colour is None:
            colour = self._ask_colour()
        if not colour:
            self.set_status("已存入棋谱库（未分析）。")
            return
        self.set_status(f"正在分析《{g['name']}》…")
        self.inbox.put({"action": "analyze", "library": self.library,
                        "game_id": game_id, "moves": moves, "colour": colour,
                        "visits": 200})

    def analyze_selected(self):
        sel = self.games_list.curselection()
        if not sel or not hasattr(self, "_games") or sel[0] >= len(self._games):
            self.set_status("先在左边选一盘棋。")
            return
        g = self._games[sel[0]]
        if not g.moves:
            self.set_status("这盘棋没有可分析的着手。")
            return
        colour = self._ask_colour()
        if not colour:
            return
        self.set_status(f"正在分析《{g.name}》…")
        self.inbox.put({"action": "analyze", "library": self.library,
                        "game_id": g.id, "moves": g.moves, "colour": colour,
                        "visits": 200})

    def _ask_colour(self) -> str | None:
        dlg = tk.Toplevel(self)
        dlg.title("这盘棋你是哪一方")
        dlg.transient(self.winfo_toplevel())
        dlg.resizable(False, False)
        tk.Label(dlg, text="只把这一方的失误收进错题本：", bg="#f0ece3",
                 font=("Microsoft YaHei", 10)).pack(padx=16, pady=(14, 8))
        res: dict = {}

        def pick(c):
            res["c"] = c
            dlg.destroy()

        row = tk.Frame(dlg, bg="#f0ece3")
        row.pack(pady=(0, 14))
        for label, val in (("我执黑", "B"), ("我执白", "W"), ("两边都要", "both")):
            tk.Button(row, text=label, width=9, relief="groove", bg="#e9e3d5",
                      font=("Microsoft YaHei", 9),
                      command=lambda v=val: pick(v)).pack(side="left", padx=4)
        dlg.grab_set()
        self.wait_window(dlg)
        return res.get("c")

    # -------------------------------------------------------------- practice
    def _on_pick_problem(self, _ev=None):
        sel = self.prob_list.curselection()
        if not sel or sel[0] >= len(getattr(self, "_problems", [])):
            return
        row = self._problems[sel[0]]
        self.current = row
        self.answered = False
        try:
            before = json.loads(row["board_before"])
        except Exception:
            before = [[0] * 19 for _ in range(19)]
        self.board.accepts_click = True
        self.board.show(before, marker=None)
        who = "黑" if row["colour"] == "B" else "白"
        self.prompt.configure(text=f"第 {row['move_no']} 手，{who}棋该走——你想下哪？")
        self._write_info(
            f"{row['game_name']}\n"
            f"第 {row['move_no']} 手　{who} {row['played']}\n"
            f"等级：{row['level']}　实战亏 {row['loss']:.1f} 目\n\n"
            "先在棋盘上点一个你认为对的位置，再点「看答案」。")
        self.set_status("想想这一手该下哪里。")

    def _on_answer(self, pos):
        if self.answered or not self.current:
            return
        # check straight away: click once to answer, see the verdict
        self.reveal(chosen=pos)

    def reveal(self, chosen=None):
        row = self.current
        if not row or self.answered:
            return
        best = from_gtp(row["best"], 19) if row["best"] else None
        played = from_gtp(row["played"], 19)
        self.board.best = best
        self.board.accepts_click = False
        self.board.redraw()
        self.answered = True
        ok = bool(chosen and best and chosen == best)
        if chosen:
            share = row["human_share"]
            tries = ""
            if share is not None:
                tries = f"（该水平 {100*share:.0f}% 的人也会这么下）"
            self.set_status(
                ("答对了！" if ok else f"不对。AI 推荐 {row['best']}{tries}")
                + f"　实战下的是 {row['played']}")
            self.library.record_attempt(row["id"],
                                        f"{GTP_LETTERS[chosen[0]]}{19-chosen[1]}",
                                        ok, 0.0)
        else:
            self.set_status(f"答案：AI 推荐 {row['best']}　实战下的是 {row['played']}")
        self._write_info((row["reason"] or "（这道题没有留下讲解）")
                         + (f"\n\n你的选择：{GTP_LETTERS[chosen[0]]}{19-chosen[1]}"
                            + ("　✓ 与 AI 一致" if ok else "　✗ 与 AI 不同")
                            if chosen else ""))
        self.refresh_problems()

    def retry(self):
        if not self.current:
            return
        row = self.current
        self.answered = False
        self.board.accepts_click = True
        try:
            before = json.loads(row["board_before"])
        except Exception:
            before = [[0] * 19 for _ in range(19)]
        self.board.show(before, marker=None)
        self.set_status("再想一次。")

    def next_problem(self):
        sel = self.prob_list.curselection()
        n = len(getattr(self, "_problems", []))
        if n == 0:
            return
        idx = (sel[0] + 1) % n if sel else 0
        self.prob_list.selection_clear(0, "end")
        self.prob_list.selection_set(idx)
        self.prob_list.see(idx)
        self._on_pick_problem()

    # ---------------------------------------------------------------- events
    def _pump(self):
        try:
            while True:
                msg = self.outbox.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        if self._pump_job is not None:
            self._pump_job = self.after(80, self._pump)

    def _handle(self, msg: dict):
        k = msg["kind"]
        if k == "status":
            self.set_status(msg["text"])
        elif k == "progress":
            self.set_status(msg["text"])
        elif k == "engine_ready":
            pass
        elif k == "game_done":
            self.refresh()
            n = msg["problems"]
            if n > 0:
                self.set_status(f"分析完成：新增 {n} 道题，在左边列表里，"
                                f"点一道就能看局面。")
            else:
                self.set_status("分析完成：这盘棋没有亏 1 目以上的手。"
                                "可能是好棋，或输得很慢（每手只亏一点）。")
        elif k == "error":
            self.set_status(msg["text"])
