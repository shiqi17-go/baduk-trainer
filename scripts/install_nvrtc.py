"""Install the remaining CUDA dependency: NVRTC.

The dependency walk found katago-cuda.exe imports nvrtc64_120_0.dll, which none
of the three wheels we fetched provides. It lives in nvidia-cuda-nvrtc-cu12,
along with nvrtc-builtins64_120.dll which is needed at compile time.
"""
import json
import os
import shutil
import ssl
import sys
import time
import urllib.request
import zipfile

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}

ROOT = r"F:\harness\baduk-trainer"
DL = os.path.join(ROOT, "downloads", "cuda")
CUDA_DIR = os.path.join(ROOT, "engine", "cuda")
ENGINE = os.path.join(ROOT, "engine")
os.makedirs(DL, exist_ok=True)

PKG = "nvidia-cuda-nvrtc-cu12"
WANT = "12.8"

print(f"looking up {PKG} {WANT}.x ...")
req = urllib.request.Request(f"https://pypi.org/pypi/{PKG}/json", headers=HDR)
with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
    data = json.load(r)

cands = []
for ver, files in data.get("releases", {}).items():
    if not ver.startswith(WANT):
        continue
    for f in files:
        if f["filename"].endswith(".whl") and "win_amd64" in f["filename"]:
            cands.append((ver, f["filename"], f["size"], f["url"]))


def key(v):
    try:
        return [int(x) for x in v.split(".")[:3]]
    except ValueError:
        return [0]


cands.sort(key=lambda c: key(c[0]), reverse=True)
if not cands:
    print("no matching wheel")
    sys.exit(1)
ver, name, size, url = cands[0]
print(f"  {name}  ({size/1e6:.1f} MB)")

dest = os.path.join(DL, name)
part = dest + ".part"
if not (os.path.exists(dest) and os.path.getsize(dest) >= size * 0.95):
    have = os.path.getsize(part) if os.path.exists(part) else 0
    headers = dict(HDR)
    if have:
        headers["Range"] = f"bytes={have}-"
    print("  downloading ...")
    rq = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(rq, timeout=60, context=CTX) as r:
        mode = "ab" if (have and r.status == 206) else "wb"
        if mode == "wb":
            have = 0
        got = have
        t0 = time.time()
        with open(part, mode) as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if time.time() - t0 > 6 and got % (8 << 20) < (1 << 20):
                    print(f"    {100*got/size:5.1f}%  {got/1e6:.1f}/{size/1e6:.1f} MB",
                          flush=True)
    os.replace(part, dest)
print(f"  have {name}  {os.path.getsize(dest)/1e6:.1f} MB")

print()
print("extracting DLLs ...")
n = 0
with zipfile.ZipFile(dest) as z:
    for info in z.infolist():
        if not info.filename.lower().endswith(".dll"):
            continue
        base = os.path.basename(info.filename)
        out = os.path.join(CUDA_DIR, base)
        with z.open(info) as src, open(out, "wb") as dst:
            shutil.copyfileobj(src, dst)
        print(f"  {base:40s} {os.path.getsize(out)/1e6:8.2f} MB")
        n += 1
print(f"  {n} DLL(s) extracted")

# keep the copy beside the exe in sync -- that is the location Windows searches
# first, and the earlier failure showed PATH alone was not enough
print()
print("mirroring CUDA DLLs next to the exe ...")
copied = 0
for f in os.listdir(CUDA_DIR):
    if f.lower().endswith(".dll"):
        shutil.copy2(os.path.join(CUDA_DIR, f), os.path.join(ENGINE, f))
        copied += 1
print(f"  {copied} DLL(s) beside katago-cuda.exe")
print()
print("DONE")
