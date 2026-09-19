"""Non-interactive GTP test: pipe a fixed command list in, capture all output.

First OpenCL run does autotuning and can take several minutes, so this uses
subprocess.run with a long timeout and is meant to be run in the background.
"""
import glob
import os
import subprocess
import sys
import time

ROOT = r"F:\harness\baduk-trainer"
ENGINE = os.path.join(ROOT, "engine")
MODELS = os.path.join(ROOT, "models")

KATAGO = glob.glob(os.path.join(ENGINE, "katago.exe"))[0]
human = glob.glob(os.path.join(MODELS, "*human*.bin"))
human = human[0] if human else None
mains = [m for m in glob.glob(os.path.join(MODELS, "*.bin")) if m != human]
main = mains[0] if mains else None

CFG_HUMAN = os.path.join(ENGINE, "gtp_human5k_example.cfg")
CFG_DEFAULT = os.path.join(ENGINE, "default_gtp.cfg")

GTP_IN = "\n".join([
    "version",
    "boardsize 19",
    "komi 7.5",
    "clear_board",
    "genmove B",
    "genmove W",
    "genmove B",
    "showboard",
    "quit",
]) + "\n"


def run(label, cmd, timeout=1500):
    print("=" * 70)
    print("TEST:", label)
    print("CMD :", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print("-" * 70, flush=True)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=GTP_IN, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        dt = time.time() - t0
        print(f"[rc={r.returncode}  elapsed={dt:.1f}s]", flush=True)
        print("--- stdout ---")
        print(r.stdout)
        if r.stderr.strip():
            print("--- stderr (last 3000 chars) ---")
            print(r.stderr[-3000:])
        return r.returncode, dt, r.stdout
    except subprocess.TimeoutExpired as e:
        print(f"[TIMEOUT after {timeout}s]")
        if e.stdout:
            print("partial stdout:", str(e.stdout)[-2000:])
        if e.stderr:
            print("partial stderr:", str(e.stderr)[-3000:])
        return None, time.time() - t0, None
    except Exception as e:
        print("[ERR]", type(e).__name__, e)
        return None, 0, None


print("engine     :", KATAGO)
print("main model :", main)
print("human model:", human)
print("human cfg  :", CFG_HUMAN)
sys.stdout.flush()

# 1) plain default config, no human model -- baseline, also warms OpenCL tuning cache
run("default cfg, main model only", [KATAGO, "gtp", "-model", main, "-config", CFG_DEFAULT])

# 2) the human 5k config with the human model -- the feature we actually need
run("human5k cfg, main + human model", [KATAGO, "gtp", "-model", main,
                                        "-human-model", human, "-config", CFG_HUMAN])

# 3) human model used alone as the main model (the documented alternative)
run("human model alone as -model", [KATAGO, "gtp", "-model", human, "-config", CFG_DEFAULT])

# 4) swap the profile to something else to prove the parameter is live
print("=" * 70)
print("TEST: profile override -> preaz_15k (weakest range)")
print("-" * 70, flush=True)
cmd = [KATAGO, "gtp", "-model", main, "-human-model", human, "-config", CFG_HUMAN,
       "-override-config", "humanSLProfile=preaz_15k"]
t0 = time.time()
try:
    r = subprocess.run(cmd, input=GTP_IN, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    print(f"[rc={r.returncode}  elapsed={time.time()-t0:.1f}s]")
    print(r.stdout)
    if r.stderr.strip():
        print("--- stderr ---")
        print(r.stderr[-2000:])
except Exception as e:
    print("[ERR]", type(e).__name__, e)

print()
print("ALL TESTS DONE")
