"""KataGo GTP engine wrapper.

Core building block shared by every front-end (terminal, web, local server).

Verified working configuration on this machine:
    engine : F:\\harness\\baduk-trainer\\engine\\katago.exe   (v1.18.1, OpenCL)
    main   : models\\b10c384h6nbttflrs.bin
    human  : models\\b18c384nbt-humanv0.bin
    config : engine\\gtp_human5k_example.cfg   (official human-like-play config)

Rank profiles come from the human SL model. Valid values (per the official cfg):
    rank_20k .. rank_9d      imitate a player of that rank, modern (post-AlphaZero) style
    preaz_20k .. preaz_9d    same, but pre-AlphaZero opening style
    rank_5k_2d               imitate black at 5k vs white at 2d
    proyear_1800 .. 2023     imitate historical pros / insei of that year
"""

from __future__ import annotations

import atexit
import glob
import os
import queue
import re
import subprocess
import sys
import threading
import time


def app_root() -> str:
    """Folder that holds engine/ and models/.
    Frozen by PyInstaller this is the folder containing the .exe; otherwise it
    is the source folder containing this file."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _find_dir(name: str) -> str:
    """Look for engine/ or models/ next to the exe, then in the bundle, then
    next to the source file."""
    candidates = [app_root(), getattr(sys, "_MEIPASS", ""),
                  os.path.dirname(os.path.abspath(__file__))]
    for base in candidates:
        if base and os.path.isdir(os.path.join(base, name)):
            return os.path.join(base, name)
    return os.path.join(app_root(), name)


ROOT = app_root()
ENGINE_DIR = _find_dir("engine")
MODELS_DIR = _find_dir("models")

KATAGO_EXE = os.path.join(ENGINE_DIR, "katago.exe")
HUMAN_CFG = os.path.join(ENGINE_DIR, "gtp_human5k_example.cfg")
DEFAULT_CFG = os.path.join(ENGINE_DIR, "default_gtp.cfg")

# KataGo's README: OpenCL is not as optimised as the NVIDIA backends, and for
# transformer nets the gap is 6-12x. We run a transformer, so use the CUDA build
# whenever its runtime DLLs are present, and fall back to OpenCL if not.
KATAGO_CUDA_EXE = os.path.join(ENGINE_DIR, "katago-cuda.exe")
CUDA_DLL_DIR = os.path.join(ENGINE_DIR, "cuda")


def pick_backend() -> tuple[str, str | None, str]:
    """Return (exe, dll_dir_or_None, backend_name)."""
    if os.path.exists(KATAGO_CUDA_EXE) and os.path.isdir(CUDA_DLL_DIR):
        dlls = [f.lower() for f in os.listdir(CUDA_DLL_DIR) if f.lower().endswith(".dll")]
        has_cudnn = any("cudnn" in d for d in dlls)
        has_cublas = any("cublas" in d for d in dlls)
        has_cudart = any("cudart" in d for d in dlls)
        if has_cudnn and has_cublas and has_cudart:
            return KATAGO_CUDA_EXE, CUDA_DLL_DIR, "cuda"
        missing = [n for n, ok in (("cudnn", has_cudnn), ("cublas", has_cublas),
                                   ("cudart", has_cudart)) if not ok]
        print(f"[katago] CUDA build present but missing {missing}; using OpenCL",
              file=sys.stderr)
    return KATAGO_EXE, None, "opencl"


def child_env(dll_dir: str | None) -> dict:
    """Environment for the engine process: put the CUDA DLLs on PATH so the
    CUDA build can find them without polluting the engine folder."""
    env = dict(os.environ)
    if dll_dir:
        env["PATH"] = dll_dir + os.pathsep + env.get("PATH", "")
    return env


def find_file(models_dir: str, needle: str) -> str | None:
    for p in sorted(glob.glob(os.path.join(models_dir, "*.bin"))):
        if needle in os.path.basename(p).lower():
            return p
    return None


def human_model_path() -> str | None:
    return find_file(MODELS_DIR, "human")


# Nets in order of preference, strongest first.
MODEL_PREFERENCE = [
    "b28c512",            # newest architecture, biggest
    "kata1-tf3-b11c768",  # flagship transformer from the main training run
    "b11c768",
    "b10c512",
    "b10c384",
]


def main_model_path(preference: list[str] | None = None) -> str | None:
    """Pick the main net, strongest available first.

    The small b10 net was only ever chosen because the app was imitating weak
    human ranks -- a job it no longer does. Teaching the best play wants the
    biggest net on disk. Override with the WEIQI_MODEL environment variable
    (either a filename fragment or an absolute path).
    """
    override = os.environ.get("WEIQI_MODEL")
    if override:
        if os.path.isabs(override) and os.path.exists(override):
            return override
        hit = find_file(MODELS_DIR, override)
        if hit:
            return hit
    nets = [p for p in sorted(glob.glob(os.path.join(MODELS_DIR, "*.bin")))
            if "human" not in os.path.basename(p).lower()]
    if not nets:
        return None
    for key in (preference or MODEL_PREFERENCE):
        for p in nets:
            if key in os.path.basename(p):
                return p
    # nothing recognised: the biggest file is the best guess
    return max(nets, key=os.path.getsize)


class KataGoError(RuntimeError):
    pass


class KataGoEngine:
    """One GTP engine process. All calls are serialised through a lock."""

    def __init__(
        self,
        profile: str = "rank_5k",
        rules: str = "chinese",
        board_size: int = 19,
        komi: float = 7.5,
        visits: int = 40,
        main_model: str | None = None,
        human_model: str | None = None,
        use_human_model: bool = True,
        config: str | None = None,
        extra_overrides: dict | None = None,
        name: str = "engine",
    ):
        self.profile = profile
        self.rules = rules
        self.board_size = board_size
        self.komi = komi
        self.visits = visits
        self.main_model = main_model or main_model_path()
        # use_human_model=False gives full-strength KataGo: the human SL model
        # imitates a rank by intuition and, per KataGo's own docs, cannot match
        # the real strength of the players it imitates at high ranks.
        self.use_human_model = use_human_model
        self.human_model = (human_model or human_model_path()) if use_human_model else None
        self.config = config or HUMAN_CFG
        self.extra_overrides = dict(extra_overrides or {})
        self.name = name

        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        self._stderr_lines: list[str] = []
        self.name = ""
        self.version = ""
        atexit.register(self.close)

    # ---------------------------------------------------------------- process

    def start(self, timeout: float = 180.0) -> None:
        if self._proc and self._proc.poll() is None:
            return
        exe, dll_dir, backend = pick_backend()
        self.exe = exe
        self.backend = backend
        if not os.path.exists(exe):
            raise KataGoError(f"engine binary not found at {exe}")
        if not self.main_model:
            raise KataGoError("no main model found in models/")

        overrides = {
            "humanSLProfile": self.profile,
            "rules": self.rules,
            "maxVisits": str(self.visits),
            "logToStderr": "false",
            "logAllGTPCommunication": "false",
            "logSearchInfo": "false",
            "logDir": os.path.join(ROOT, "logs"),
            **self.extra_overrides,
        }

        cmd = [exe, "gtp", "-model", self.main_model]
        if self.human_model:
            cmd += ["-human-model", self.human_model]
        if self.config and os.path.exists(self.config):
            cmd += ["-config", self.config]
        for k, v in overrides.items():
            cmd += ["-override-config", f"{k}={v}"]

        self._cmdline = cmd
        self._proc = subprocess.Popen(
            cmd,
            env=child_env(dll_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=ENGINE_DIR,
        )
        self._q = queue.Queue()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        # ask the engine what it actually is, rather than hardcoding a version
        self.name = self._cmd("name", timeout=timeout).strip()
        self.version = self._cmd("version", timeout=timeout).strip()
        self._cmd(f"boardsize {self.board_size}")
        self._cmd(f"komi {self.komi}")
        self.clear_board()

    def _pump_stdout(self):
        try:
            for line in self._proc.stdout:
                self._q.put(line)
        except Exception:
            pass
        finally:
            self._q.put(None)

    def _pump_stderr(self):
        try:
            for line in self._proc.stderr:
                self._stderr_lines.append(line.rstrip())
                if len(self._stderr_lines) > 400:
                    del self._stderr_lines[:200]
        except Exception:
            pass

    def _cmd(self, line: str, timeout: float = 300.0) -> str:
        with self._lock:
            if not self._proc or self._proc.poll() is not None:
                raise KataGoError(
                    "engine is not running"
                    + (f" (exit={self._proc.returncode})" if self._proc else "")
                )
            self._proc.stdin.write(line + "\n")
            self._proc.stdin.flush()

            out: list[str] = []
            deadline = time.time() + timeout
            status = None
            while True:
                remain = deadline - time.time()
                if remain <= 0:
                    raise KataGoError(f"timeout waiting for response to: {line}")
                try:
                    raw = self._q.get(timeout=min(remain, 5.0))
                except queue.Empty:
                    if self._proc.poll() is not None:
                        raise KataGoError(
                            f"engine died while handling '{line}'. "
                            f"last stderr: {self._stderr_lines[-5:]}"
                        )
                    continue
                if raw is None:
                    raise KataGoError(
                        f"engine closed stdout while handling '{line}'. "
                        f"last stderr: {self._stderr_lines[-5:]}"
                    )
                s = raw.rstrip("\r\n")
                if status is None:
                    if s.startswith("="):
                        status = "ok"
                        s = s[1:].strip()
                        if not s:
                            continue
                    elif s.startswith("?"):
                        status = "err"
                        s = s[1:].strip()
                        if not s:
                            continue
                    else:
                        # stray log line on stdout; keep it for diagnostics
                        continue
                if s == "":
                    break
                out.append(s)

            text = "\n".join(out)
            if status == "err":
                raise KataGoError(f"GTP error for '{line}': {text}")
            return text

    # ------------------------------------------------------------- game moves

    def clear_board(self) -> str:
        return self._cmd("clear_board")

    def play(self, color: str, move: str) -> str:
        return self._cmd(f"play {color} {move}")

    def genmove(self, color: str, timeout: float = 300.0) -> str:
        return self._cmd(f"genmove {color}", timeout=timeout).strip()

    def undo(self) -> str:
        return self._cmd("undo")

    def showboard(self) -> str:
        return self._cmd("showboard")

    def final_score(self) -> str:
        return self._cmd("final_score")

    def resign(self, color: str) -> str:
        return self._cmd(f"play {color} resign")

    def evaluate(self, timeout: float = 60.0) -> dict:
        """Position assessment straight from the neural net, without searching.

        `kata-analyze` was the obvious candidate, but it streams partial output
        and never terminates the GTP response, so it cannot be driven through a
        request/response wrapper. `kata-raw-nn` answers in one response in about
        10ms and gives the net's own score and winrate judgement -- which is what
        should be displayed instead of a hand-rolled area count.

        whiteLead is from White's point of view; blackLead is the negation.
        """
        out = self._cmd("kata-raw-nn 0", timeout=timeout)
        vals: dict = {}
        for key in ("whiteWin", "whiteLoss", "whiteLead", "whiteScoreSelfplay",
                    "varTimeLeft"):
            m = re.search(rf"\b{key}\s+(-?[0-9.eE+]+)", out)
            if m:
                try:
                    vals[key] = float(m.group(1))
                except ValueError:
                    pass
        if "whiteLead" in vals:
            vals["blackLead"] = -vals["whiteLead"]
        if "whiteWin" in vals and "whiteLoss" in vals:
            total = vals["whiteWin"] + vals["whiteLoss"]
            if total > 0:
                vals["blackWinrate"] = vals["whiteLoss"] / total
                vals["whiteWinrate"] = vals["whiteWin"] / total
        return vals

    # ------------------------------------------------------------------ ranks

    def set_profile(self, profile: str, restart: bool = True) -> None:
        """Change the imitated rank. Requires an engine restart: humanSLProfile
        is a startup config value, not a runtime GTP parameter."""
        if profile == self.profile:
            return
        self.profile = profile
        if restart:
            self.close()
            self.start()

    def set_visits(self, visits: int, restart: bool = True) -> None:
        self.visits = visits
        if restart:
            self.close()
            self.start()

    def set_rules(self, rules: str, restart: bool = True) -> None:
        self.rules = rules
        if restart:
            self.close()
            self.start()

    # ---------------------------------------------------------------- cleanup

    def close(self) -> None:
        p = self._proc
        if not p:
            return
        self._proc = None
        try:
            if p.poll() is None:
                try:
                    p.stdin.write("quit\n")
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
        return bool(self._proc) and self._proc.poll() is None

    def stderr_tail(self, n: int = 10) -> list[str]:
        return self._stderr_lines[-n:]

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()


ALL_PROFILES = (
    [f"rank_{i}k" for i in range(20, 0, -1)]
    + [f"rank_{i}d" for i in range(1, 10)]
    + [f"preaz_{i}k" for i in range(20, 0, -1)]
    + [f"preaz_{i}d" for i in range(1, 10)]
)


if __name__ == "__main__":
    print("engine  :", KATAGO_EXE, os.path.exists(KATAGO_EXE))
    print("main    :", main_model_path())
    print("human   :", human_model_path())
    print("config  :", HUMAN_CFG, os.path.exists(HUMAN_CFG))
    print("profiles:", len(ALL_PROFILES), "e.g.", ALL_PROFILES[:3], ALL_PROFILES[-3:])
