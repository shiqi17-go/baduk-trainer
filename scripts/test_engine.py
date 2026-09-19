"""Verify the downloaded KataGo engine: run it, list GTP commands, and find the
exact humanSL option names from the config templates shipped in the release.
ASCII output only.

Usage: python test_engine.py
"""
import glob
import os
import re
import subprocess
import sys

ROOT = r"F:\harness\baduk-trainer"
ENGINE = os.path.join(ROOT, "engine")
MODELS = os.path.join(ROOT, "models")


def find(pattern, base):
    hits = glob.glob(os.path.join(base, "**", pattern), recursive=True)
    return hits


print("=== locate katago.exe ===")
exes = find("katago.exe", ENGINE)
for e in exes:
    print("  ", e)
if not exes:
    print("  NOT FOUND -- engine extraction failed?")
    sys.exit(1)
KATAGO = exes[0]
print("  using:", KATAGO)

print()
print("=== version ===")
try:
    r = subprocess.run([KATAGO, "version"], capture_output=True, text=True, timeout=60)
    print("  rc:", r.returncode)
    for line in (r.stdout or "").strip().splitlines()[:6]:
        print("  ", line)
    if r.stderr.strip():
        print("  stderr:", r.stderr.strip()[:300])
except Exception as e:
    print("  ERR", type(e).__name__, e)

print()
print("=== config templates shipped with the release ===")
cfgs = [p for p in find("*.cfg", ENGINE)]
for c in cfgs:
    print("  ", os.path.relpath(c, ENGINE))

print()
print("=== humanSL-related lines in the templates ===")
seen = set()
for c in cfgs:
    try:
        with open(c, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"  ERR reading {c}: {e}")
        continue
    for i, ln in enumerate(lines):
        if re.search(r"human", ln, re.I):
            s = ln.rstrip()
            if s not in seen:
                seen.add(s)
                print(f"  [{os.path.basename(c)}:{i+1}] {s}")

print()
print("=== models present ===")
for m in sorted(glob.glob(os.path.join(MODELS, "*"))):
    print(f"  {os.path.basename(m):45s} {os.path.getsize(m)/1e6:8.1f} MB")

print()
print("=== GTP smoke test ===")
human = None
for m in glob.glob(os.path.join(MODELS, "*.bin")):
    if "human" in os.path.basename(m).lower():
        human = m
        break
main = None
for m in glob.glob(os.path.join(MODELS, "*.bin")):
    if m != human:
        main = m
        break
print("  main model :", main)
print("  human model:", human)

default_cfg = os.path.join(os.path.dirname(KATAGO), "default_gtp.cfg")
gtp_cfg = os.path.join(os.path.dirname(KATAGO), "gtp_example.cfg")
cfg = default_cfg if os.path.exists(default_cfg) else (gtp_cfg if os.path.exists(gtp_cfg) else None)
print("  config     :", cfg)

cmd = [KATAGO, "gtp", "-model", main]
if human:
    cmd += ["-human-model", human]
if cfg:
    cmd += ["-config", cfg]

print("  cmd:", " ".join(f'"{c}"' if " " in c else c for c in cmd))
print()

cmds = [
    "version",
    "list_commands",
    "boardsize 19",
    "komi 7.5",
    "clear_board",
    "play B Q16",
    "play W D4",
    "genmove B",
    "showboard",
    "quit",
]

try:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    out_lines = []

    def read_until(pred, limit=400):
        got = []
        for _ in range(limit):
            line = proc.stdout.readline()
            if not line:
                break
            line = line.rstrip()
            got.append(line)
            if pred(line):
                break
        return got

    for c in cmds:
        proc.stdin.write(c + "\n")
        proc.stdin.flush()
        expect = "=" if c != "list_commands" else None
        block = read_until(lambda l: l.strip() == "" or (expect and l.startswith(expect)))
        out_lines.append(f">>> {c}")
        for l in block:
            if l.strip():
                out_lines.append("    " + l)
        if c == "list_commands":
            # command list is long; summarise instead of dumping
            joined = " ".join(l.strip() for l in block)
            out_lines = out_lines[:-len(block) - 0]
            out_lines.append(f">>> list_commands -> {len(joined.split())} commands")
            for kw in ("kata-analyze", "kata-genmove_analyze", "genmove", "showboard",
                       "kata-set-param", "kata-get-param"):
                out_lines.append(f"    has {kw}: {kw in joined}")
    proc.stdin.close()
    proc.wait(timeout=30)
    print("\n".join(out_lines))
except Exception as e:
    print("  GTP ERR", type(e).__name__, e)
    try:
        proc.kill()
    except Exception:
        pass

print()
print("DONE")
