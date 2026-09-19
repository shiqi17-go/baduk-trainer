"""Local game library and problem store.

Foundation for the teaching features. Everything the tutor knows about a
student comes from here: imported game records plus the per-move analysis of
them. Built on SQLite so it is a single file next to the app, no server.

Design notes:
  * A "problem" is a position where the student made a real mistake, together
    with what the AI preferred. Problems come from the student's OWN games --
    that is what makes them motivating to practise.
  * Analysis is stored, not recomputed, so the library keeps growing in value
    and re-opening a game is instant.
  * The database lives in the app folder so the whole thing stays portable.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

# mistake severity bands, in points lost compared with the AI's first choice
LEVELS = [
    (10.0, "大恶手"),
    (5.0, "严重失误"),
    (2.0, "失误"),
    (0.8, "不够精细"),
]
# Was 2.0, which made the 不够精细 band unreachable and produced an empty list
# for a well-played or slowly-lost game -- users then saw "0 题" and assumed it
# was broken. 1.0 keeps the finer band reachable.
MIN_PROBLEM_LOSS = 1.0          # only this bad or worse becomes a problem


def level_for(loss: float) -> str:
    for threshold, name in LEVELS:
        if loss >= threshold:
            return name
    return "尚可"


SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT,
    name        TEXT,
    black       TEXT,
    white       TEXT,
    result      TEXT,
    komi        REAL,
    size        INTEGER,
    moves       TEXT,          -- JSON list of ["B","Q16"]
    added_at    TEXT,
    analyzed_at TEXT,          -- NULL until analysis has run
    student_colour TEXT,       -- 'B', 'W' or NULL
    visits      INTEGER
);

CREATE TABLE IF NOT EXISTS problems (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
    turn        INTEGER,       -- 0-based index into the move list
    move_no     INTEGER,       -- 1-based move number shown to the student
    colour      TEXT,
    played      TEXT,
    best        TEXT,
    loss        REAL,
    level       TEXT,
    winrate     REAL,
    human_rank  INTEGER,       -- rank of the played move in the human policy
    human_share REAL,          -- how often that level plays it
    board       TEXT,          -- JSON 19x19 grid of 0/1/2 after the played move
    board_before TEXT,         -- JSON grid before the played move
    pv          TEXT,          -- JSON list of the expected continuation
    reason      TEXT,          -- the generated teaching text
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id  INTEGER NOT NULL REFERENCES problems(id) ON DELETE CASCADE,
    chosen      TEXT,
    ok          INTEGER,
    seconds     REAL,
    at          TEXT
);
"""


@dataclass
class GameRow:
    id: int
    name: str
    black: str
    white: str
    result: str
    moves: list
    analyzed_at: str | None
    student_colour: str | None
    problem_count: int = 0


class Library:
    def __init__(self, path: str | None = None):
        if path is None:
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "library.db")
        self.path = path
        # The connection is shared between the UI thread and the background
        # analysis worker. sqlite3 by default refuses cross-thread use
        # ("objects created in a thread can only be used in that same thread"),
        # which is what stopped analysis from ever writing its results. WAL
        # mode lets one writer and many readers coexist, and a single lock
        # serialises the actual writes.
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------ games

    def add_game(self, moves: list, black: str = "", white: str = "",
                 result: str = "", komi: float = 7.5, size: int = 19,
                 path: str = "", name: str = "") -> int | None:
        """Returns the new game id, or None if this game is already stored."""
        if not moves:
            return None
        key = json.dumps(moves, ensure_ascii=False)
        with self._lock:
            row = self.conn.execute("SELECT id FROM games WHERE moves = ?",
                                    (key,)).fetchone()
            if row:
                return None                      # already imported
            cur = self.conn.execute(
                "INSERT INTO games(path,name,black,white,result,komi,size,moves,added_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (path, name or (os.path.basename(path) if path else "未命名"),
                 black, white, result, komi, size, key,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
            self.conn.commit()
            return cur.lastrowid

    def games(self) -> list[GameRow]:
        rows = self.conn.execute(
            "SELECT g.*, (SELECT COUNT(*) FROM problems p WHERE p.game_id=g.id)"
            " AS pc FROM games g ORDER BY g.id DESC").fetchall()
        out = []
        for r in rows:
            out.append(GameRow(
                id=r["id"], name=r["name"] or "", black=r["black"] or "",
                white=r["white"] or "", result=r["result"] or "",
                moves=json.loads(r["moves"] or "[]"),
                analyzed_at=r["analyzed_at"], student_colour=r["student_colour"],
                problem_count=r["pc"]))
        return out

    def get_game(self, game_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM games WHERE id=?",
                                 (game_id,)).fetchone()

    def delete_game(self, game_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM games WHERE id=?", (game_id,))
            self.conn.commit()

    def mark_analyzed(self, game_id: int, student_colour: str, visits: int):
        with self._lock:
            self.conn.execute(
                "UPDATE games SET analyzed_at=?, student_colour=?, visits=? WHERE id=?",
                (time.strftime("%Y-%m-%d %H:%M:%S"), student_colour, visits, game_id))
            self.conn.commit()

    # --------------------------------------------------------------- problems

    def clear_problems(self, game_id: int):
        with self._lock:
            self.conn.execute("DELETE FROM problems WHERE game_id=?", (game_id,))
            self.conn.commit()

    def add_problem(self, game_id: int, **kw) -> int:
        cols = ("game_id,turn,move_no,colour,played,best,loss,level,winrate,"
                "human_rank,human_share,board,board_before,pv,reason,created_at")
        vals = (game_id, kw.get("turn"), kw.get("move_no"), kw.get("colour"),
                kw.get("played"), kw.get("best"), kw.get("loss"),
                kw.get("level"), kw.get("winrate"), kw.get("human_rank"),
                kw.get("human_share"),
                json.dumps(kw.get("board")), json.dumps(kw.get("board_before")),
                json.dumps(kw.get("pv") or []), kw.get("reason", ""),
                time.strftime("%Y-%m-%d %H:%M:%S"))
        with self._lock:
            cur = self.conn.execute(
                f"INSERT INTO problems({cols}) VALUES({','.join('?'*16)})", vals)
            self.conn.commit()
        return cur.lastrowid

    def problems(self, level: str | None = None, game_id: int | None = None,
                 asked_only: bool | None = None, limit: int = 500) -> list[sqlite3.Row]:
        q = ("SELECT p.*, g.name AS game_name, g.black, g.white, g.result,"
             " (SELECT COUNT(*) FROM attempts a WHERE a.problem_id=p.id) AS tries,"
             " (SELECT MAX(a.ok) FROM attempts a WHERE a.problem_id=p.id) AS ever_ok"
             " FROM problems p JOIN games g ON g.id=p.game_id WHERE 1=1")
        args: list = []
        if level:
            q += " AND p.level=?"
            args.append(level)
        if game_id:
            q += " AND p.game_id=?"
            args.append(game_id)
        if asked_only:
            q += " AND EXISTS(SELECT 1 FROM attempts a WHERE a.problem_id=p.id)"
        q += " ORDER BY p.loss DESC LIMIT ?"
        args.append(limit)
        return self.conn.execute(q, args).fetchall()

    def stats(self) -> dict:
        g = self.conn.execute("SELECT COUNT(*) c,"
                              " SUM(analyzed_at IS NOT NULL) a FROM games").fetchone()
        p = self.conn.execute("SELECT COUNT(*) c FROM problems").fetchone()
        by_level = {r["level"]: r["c"] for r in self.conn.execute(
            "SELECT level, COUNT(*) c FROM problems GROUP BY level")}
        att = self.conn.execute(
            "SELECT COUNT(*) c, SUM(ok) ok FROM attempts").fetchone()
        return {
            "games": g["c"] or 0,
            "analyzed": g["a"] or 0,
            "problems": p["c"] or 0,
            "by_level": by_level,
            "attempts": att["c"] or 0,
            "solved": att["ok"] or 0,
        }

    # --------------------------------------------------------------- attempts

    def record_attempt(self, problem_id: int, chosen: str, ok: bool,
                       seconds: float):
        with self._lock:
            self.conn.execute(
                "INSERT INTO attempts(problem_id,chosen,ok,seconds,at)"
                " VALUES(?,?,?,?,?)",
                (problem_id, chosen, 1 if ok else 0, seconds,
                 time.strftime("%Y-%m-%d %H:%M:%S")))
            self.conn.commit()


if __name__ == "__main__":
    lib = Library()
    print("database:", lib.path)
    print("stats   :", lib.stats())
    lib.close()
