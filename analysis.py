"""KataGo parallel analysis engine wrapper.

Unlike the GTP wrapper (katago_engine.py), this talks to `katago analysis`,
which speaks JSON lines on stdin/stdout and exposes the extra fields we need for
explanation: ownership, per-move ownership, the neural net's raw policy, and the
human SL policy (humanPolicy / humanPrior).

Relevant query fields (from docs/Analysis_Engine.md):
    includeOwnership / includeMovesOwnership / includePolicy / includePVVisits
    analyzeTurns, maxVisits, overrideSettings (humanSLProfile lives here)

Row-major note: ownership and policy arrays are indexed y * boardXSize + x with
(0,0) at the TOP-LEFT (A19) -- which matches the internal grid in goban.py.

Winrate perspective is whatever `reportAnalysisWinratesAs` says; we force BLACK
and normalise explicitly rather than guessing.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from katago_engine import (ENGINE_DIR, KATAGO_EXE, MODELS_DIR, ROOT,  # noqa: E402
                           child_env, human_model_path, main_model_path,
                           pick_backend)

ANALYSIS_CFG = os.path.join(ENGINE_DIR, "analysis_example.cfg")

# Recipe from the docs section "Ensuring all likely human moves are analyzed":
# spend half the playouts exploring human moves weightlessly, and give
# high-human-policy moves many visits even when they lose. Without this the
# moves a human would actually play are not in moveInfos, so we could not tell
# the student what their natural move costs.
HUMAN_ANALYSIS_OVERRIDES = {
    "ignorePreRootHistory": False,
    "humanSLRootExploreProbWeightless": 0.5,
    "humanSLCpuctPermanent": 2.0,
    "rootNumSymmetriesToSample": 2,
}


class AnalysisError(RuntimeError):
    pass


class KataGoAnalysis:
    def __init__(
        self,
        model: str | None = None,
        human_model: str | None = None,
        config: str | None = None,
        extra_overrides: dict | None = None,
        num_threads: int = 1,
    ):
        self.model = model or main_model_path()
        self.human_model = human_model or human_model_path()
        self.config = config or ANALYSIS_CFG
        self.num_threads = num_threads
        self.extra_overrides = dict(extra_overrides or {})
        self.proc: subprocess.Popen | None = None
        self._pending: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._stderr: list[str] = []
        self._ids = 0
        self.version = ""

    # ------------------------------------------------------------- lifecycle

    def start(self, timeout: float = 1800.0) -> None:
        """Start the engine.

        The timeout is deliberately generous: a net architecture that has never
        run on this GPU needs OpenCL autotuning first -- 354s for the
        768-channel transformer on this machine. The tuning is cached, so later
        starts take about 14s.
        """
        if self.proc and self.proc.poll() is None:
            return
        exe, dll_dir, backend = pick_backend()
        self.exe = exe
        self.backend = backend
        if not os.path.exists(exe):
            raise AnalysisError(f"engine binary not found: {exe}")

        cmd = [exe, "analysis", "-model", self.model]
        if self.human_model:
            cmd += ["-human-model", self.human_model]
        if self.config and os.path.exists(self.config):
            cmd += ["-config", self.config]
        overrides = {
            "logToStderr": "false",
            "logDir": os.path.join(ROOT, "logs"),
            "logAllRequests": "false",
            # force a known winrate perspective; we normalise ourselves
            "reportAnalysisWinratesAs": "BLACK",
            "numAnalysisThreads": str(self.num_threads),
            **self.extra_overrides,
        }
        for k, v in overrides.items():
            cmd += ["-override-config", f"{k}={json.dumps(v) if isinstance(v, bool) else v}"]

        self.cmdline = cmd
        self.proc = subprocess.Popen(
            cmd, env=child_env(dll_dir),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, cwd=ENGINE_DIR,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

        r = self.query({"action": "query_version"}, timeout=timeout)
        self.version = r.get("version", "")
        self.git_hash = r.get("git_hash", "")

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                qid = obj.get("id")
                with self._lock:
                    slot = self._pending.get(qid)
                if slot is not None:
                    with slot["lock"]:
                        slot["responses"].append(obj)
                        slot["event"].set()
                elif obj.get("error"):
                    with self._lock:
                        for s in self._pending.values():
                            with s["lock"]:
                                s["errors"].append(obj)
                                s["event"].set()
        except Exception:
            pass

    def _read_stderr(self):
        try:
            for line in self.proc.stderr:
                self._stderr.append(line.rstrip())
                if len(self._stderr) > 500:
                    del self._stderr[:250]
        except Exception:
            pass

    def query(self, q: dict, expect_turns: int = 1, timeout: float = 900.0) -> dict:
        """Send one query. Returns a dict; for multi-turn queries the responses
        are collected under "_turns" keyed by turnNumber."""
        if not self.proc or self.proc.poll() is not None:
            raise AnalysisError("analysis engine is not running")
        with self._lock:
            self._ids += 1
            qid = q.get("id") or f"q{self._ids}"
            q["id"] = qid
            slot = {"lock": threading.RLock(), "responses": [], "errors": [],
                    "event": threading.Event()}
            self._pending[qid] = slot

        try:
            self.proc.stdin.write(json.dumps(q) + "\n")
            self.proc.stdin.flush()
            deadline = time.time() + timeout
            got: list[dict] = []
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    raise AnalysisError(
                        f"timeout waiting for query {qid}; stderr tail: {self._stderr[-4:]}")
                if not slot["event"].wait(timeout=min(remain, 5.0)):
                    if self.proc.poll() is not None:
                        raise AnalysisError(
                            f"engine died during {qid}; stderr tail: {self._stderr[-6:]}")
                    continue
                with slot["lock"]:
                    slot["event"].clear()
                    if slot["errors"]:
                        err = slot["errors"][0]
                        raise AnalysisError(f"query error: {err.get('error')}")
                    got = list(slot["responses"])
                # An error response echoes the same id as the query, so without
                # this check it is returned to the caller as if it were a
                # result -- a silent failure. Verify before accepting.
                bad = [r for r in got if "error" in r]
                if bad:
                    raise AnalysisError(
                        f"query {qid} rejected: {bad[0].get('error')}")
                if len(got) >= expect_turns:
                    break
            if len(got) == 1:
                return got[0]
            out: dict = {"_turns": {}, "_multi": True}
            for r in got:
                out["_turns"][r.get("turnNumber", 0)] = r
            return out
        finally:
            with self._lock:
                self._pending.pop(qid, None)

    def close(self):
        p = self.proc
        if not p:
            return
        self.proc = None
        try:
            if p.poll() is None:
                try:
                    p.stdin.write(json.dumps({"id": "bye", "action": "terminate"}) + "\n")
                    p.stdin.flush()
                except Exception:
                    pass
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
        except Exception:
            try:
                p.kill()
            except Exception:
                pass

    @property
    def alive(self) -> bool:
        return bool(self.proc) and self.proc.poll() is None

    def stderr_tail(self, n: int = 12) -> list[str]:
        return self._stderr[-n:]

    # ------------------------------------------------------------- game level

    def analyze_game(
        self,
        moves: list[list[str]],
        profile_for_turn: list[str] | None = None,
        turns: list[int] | None = None,
        max_visits: int = 300,
        rules: str = "chinese",
        komi: float = 7.5,
        board_size: int = 19,
        human_analysis: bool = True,
        extra_overrides: dict | None = None,
    ) -> dict[int, dict]:
        """Analyse a whole game. One query per distinct humanSLProfile, since
        the profile is per-query. Returns {turnNumber: response}."""
        if turns is None:
            turns = list(range(len(moves)))
        # the analysis engine errors out on an out-of-range turn, so clamp here
        bad = [t for t in turns if t < 0 or t >= len(moves)]
        if bad:
            turns = [t for t in turns if 0 <= t < len(moves)]
            if not turns:
                raise AnalysisError(
                    f"no valid turns to analyse (asked {bad[:5]}, game has {len(moves)} moves)")
        if profile_for_turn is None:
            profile_for_turn = ["rank_5k"] * len(moves)

        groups: dict[str | None, list[int]] = {}
        for t in turns:
            prof = profile_for_turn[t] if human_analysis else None
            groups.setdefault(prof, []).append(t)

        results: dict[int, dict] = {}
        for prof, tlist in groups.items():
            q: dict = {
                "moves": moves,
                "rules": rules,
                "komi": komi,
                "boardXSize": board_size,
                "boardYSize": board_size,
                "analyzeTurns": sorted(tlist),
                "maxVisits": max_visits,
                "includeOwnership": True,
                "includeMovesOwnership": True,
                "includePolicy": True,
                "includePVVisits": True,
            }
            ov = {}
            if prof:
                ov["humanSLProfile"] = prof
                if human_analysis:
                    ov.update(HUMAN_ANALYSIS_OVERRIDES)
            if extra_overrides:
                ov.update(extra_overrides)
            if ov:
                q["overrideSettings"] = ov
            r = self.query(q, expect_turns=len(tlist), timeout=3600)
            if r.get("_multi"):
                for tn, resp in r["_turns"].items():
                    results[int(tn)] = resp
            else:
                results[r.get("turnNumber", tlist[0])] = r
        return results


# ------------------------------------------------------------------ helpers

def ownership_at(ownership: list[float], x: int, y: int, board_size: int = 19) -> float:
    """Row-major, top-left origin."""
    return ownership[y * board_size + x]


def policy_at(policy: list[float], x: int, y: int, board_size: int = 19) -> float:
    return policy[y * board_size + x]


def pass_policy(policy: list[float]) -> float:
    return policy[-1] if policy else 0.0


if __name__ == "__main__":
    a = KataGoAnalysis()
    print("katago   :", KATAGO_EXE, os.path.exists(KATAGO_EXE))
    print("model    :", a.model)
    print("human    :", a.human_model)
    print("config   :", a.config, os.path.exists(a.config))
    a.start()
    print("version  :", a.version, a.git_hash)
    print("alive    :", a.alive)

    # perspective probe: a position where black is clearly ahead
    probe = [["B", "Q16"], ["W", "A1"], ["B", "Q4"], ["W", "A19"],
             ["B", "D16"], ["W", "T1"], ["B", "D4"], ["W", "T19"]]
    res = a.analyze_game(probe, turns=[0, 1, 2], max_visits=40, human_analysis=False)
    for t in sorted(res):
        r = res[t]
        ri = r.get("rootInfo", {})
        print(f"\nturn {t}: currentPlayer={ri.get('currentPlayer')} "
              f"winrate={ri.get('winrate'):.4f} scoreLead={ri.get('scoreLead'):.2f}")
        print("  top-level keys:", sorted(k for k in r.keys() if k != "moveInfos")[:14])
        if r.get("moveInfos"):
            m0 = r["moveInfos"][0]
            print("  moveInfo keys:", sorted(m0.keys()))
    a.close()
    print("\nOK")
