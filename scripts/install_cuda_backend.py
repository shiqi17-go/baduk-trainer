"""Install a CUDA+cuDNN backend for KataGo, without pip.

Why this matters: KataGo's own README says OpenCL is not as optimised as the
NVIDIA backends, and the gap is roughly 5x for convolutional nets but 6-12x for
transformers -- and we run a transformer. Moving to CUDA+cuDNN is what turns
"a strong net at 300 visits" into "the strongest engine at thousands of visits".

KataGo's Windows CUDA builds cannot bundle CUDA/cuDNN for licensing reasons, so
the DLLs come from NVIDIA's pip wheels. A wheel is a zip, so we fetch and unpack
directly -- no pip, which also avoids the sandbox's pip temp restrictions.
"""
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

ROOT = r"F:\harness\baduk-trainer"
DL = os.path.join(ROOT, "downloads", "cuda")
ENGINE = os.path.join(ROOT, "engine")
CUDA_DIR = os.path.join(ENGINE, "cuda")
os.makedirs(DL, exist_ok=True)
os.makedirs(CUDA_DIR, exist_ok=True)

BASE = "https://files.pythonhosted.org/packages"

WHEELS = [
    ("nvidia_cuda_runtime_cu12-12.8.90-py3-none-win_amd64.whl", 900_000,
     f"{BASE}/30/a5/a515b7600ad361ea14bfa13fb4d6687abf500adc270f19e89849c0590492/"
     "nvidia_cuda_runtime_cu12-12.8.90-py3-none-win_amd64.whl"),
    ("nvidia_cublas_cu12-12.8.5.5-py3-none-win_amd64.whl", 567_500_000,
     f"{BASE}/74/65/d9db5b0754559f6ed279c4a6cf1192dbf581f7d01e5d3d2882f577936049/"
     "nvidia_cublas_cu12-12.8.5.5-py3-none-win_amd64.whl"),
    ("nvidia_cudnn_cu12-9.8.0.87-py3-none-win_amd64.whl", 684_600_000,
     f"{BASE}/39/6a/5e9910b2b2c9dcddee9aaef372b3db0f08b9f7eaf1d462f859461a79caf9/"
     "nvidia_cudnn_cu12-9.8.0.87-py3-none-win_amd64.whl"),
]

KATAGO_CUDA = ("katago-v1.18.1-cuda12.8-cudnn9.8.0-windows-x64.zip", 10_200_000,
               "https://github.com/lightvector/KataGo/releases/download/v1.18.1/"
               "katago-v1.18.1-cuda12.8-cudnn9.8.0-windows-x64.zip")


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def fetch(name, url, expected, dest_dir, attempts=5):
    dest = os.path.join(dest_dir, name)
    part = dest + ".part"
    for attempt in range(1, attempts + 1):
        if os.path.exists(dest) and os.path.getsize(dest) >= expected * 0.95:
            print(f"  [have] {name}  {human(os.path.getsize(dest))}", flush=True)
            return dest
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"User-Agent": "Mozilla/5.0"}
        if have:
            headers["Range"] = f"bytes={have}-"
        print(f"  [get ] {name}  attempt {attempt}"
              + (f"  resume@{human(have)}" if have else ""), flush=True)
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                got = have
                t0 = time.time()
                last = 0.0
                with open(part, mode) as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        now = time.time()
                        if now - last > 8:
                            last = now
                            spd = (got - have) / max(now - t0, 0.1)
                            pct = 100 * got / expected if expected else 0
                            eta = (expected - got) / max(spd, 1)
                            print(f"        {pct:5.1f}%  {human(got)}/{human(expected)}"
                                  f"  {human(spd)}/s  ETA {eta/60:4.1f} min", flush=True)
            if os.path.getsize(part) < expected * 0.95:
                print(f"        incomplete ({human(os.path.getsize(part))}), retrying")
                time.sleep(3)
                continue
            os.replace(part, dest)
            print(f"  [done] {name}  {human(os.path.getsize(dest))}", flush=True)
            return dest
        except Exception as e:
            print(f"        error: {type(e).__name__}: {str(e)[:80]}", flush=True)
            time.sleep(5)
    print(f"  [FAIL] {name}", flush=True)
    return None


def main():
    print("=" * 76)
    print("downloading the KataGo CUDA build")
    print("=" * 76)
    kg = fetch(KATAGO_CUDA[0], KATAGO_CUDA[2], KATAGO_CUDA[1], DL)

    print()
    print("=" * 76)
    print("downloading CUDA / cuDNN runtime wheels")
    print("=" * 76)
    wheels = []
    for name, size, url in WHEELS:
        p = fetch(name, url, size, DL)
        if p:
            wheels.append(p)

    print()
    print("=" * 76)
    print("extracting DLLs")
    print("=" * 76)
    if kg:
        with zipfile.ZipFile(kg) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".exe")]
            print(f"  katago zip: {names}")
            for n in names:
                data = z.read(n)
                target = os.path.join(ENGINE, "katago-cuda.exe")
                with open(target, "wb") as f:
                    f.write(data)
                print(f"  -> {target}  {human(len(data))}")
    total = 0
    for w in wheels:
        print(f"  from {os.path.basename(w)}")
        with zipfile.ZipFile(w) as z:
            dlls = [n for n in z.namelist() if n.lower().endswith(".dll")]
            for n in dlls:
                base = os.path.basename(n)
                out = os.path.join(CUDA_DIR, base)
                if os.path.exists(out):
                    print(f"      (skip) {base}")
                    continue
                with z.open(n) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                sz = os.path.getsize(out)
                total += sz
                print(f"      {base:36s} {human(sz)}")

    print()
    print("=" * 76)
    print(f"CUDA runtime dir: {CUDA_DIR}  total {human(total)}")
    print("=" * 76)
    for f in sorted(os.listdir(CUDA_DIR)):
        print(f"  {f:40s} {human(os.path.getsize(os.path.join(CUDA_DIR, f)))}")
    print()
    print("engine dir:")
    for f in sorted(os.listdir(ENGINE)):
        if f.lower().endswith(".exe"):
            print(f"  {f:40s} {human(os.path.getsize(os.path.join(ENGINE, f)))}")
    print()
    print("DONE")


if __name__ == "__main__":
    main()
