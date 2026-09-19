"""Download the strong net used for high-level teaching analysis.

The shipped b10 net was picked to be small and fast because the app simulated
weak human ranks. Teaching top-level play needs a much stronger net; this one
is the flagship transformer from KataGo's main "kata1" training run.
"""
import gzip
import os
import shutil
import ssl
import sys
import time
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ROOT = r"F:\harness\baduk-trainer"
DL = os.path.join(ROOT, "downloads")
MODELS = os.path.join(ROOT, "models")
os.makedirs(DL, exist_ok=True)
os.makedirs(MODELS, exist_ok=True)

NAME = "kata1-tf3-b11c768-s11750M-d6216M.bin.gz"
URL = ("https://media.katagotraining.org/uploaded/networks/models/kata1/" + NAME)
EXPECTED = 211575408

dest = os.path.join(DL, NAME)
part = dest + ".part"


def download():
    for attempt in range(1, 5):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if os.path.exists(dest) and os.path.getsize(dest) > EXPECTED * 0.95:
            print(f"  already downloaded: {os.path.getsize(dest)/1e6:.1f} MB")
            return dest
        headers = {"User-Agent": "Mozilla/5.0"}
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"  resuming from {have/1e6:.1f} MB (attempt {attempt})", flush=True)
        else:
            print(f"  starting (attempt {attempt})", flush=True)
        try:
            req = urllib.request.Request(URL, headers=headers)
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
                        if now - last > 4:
                            last = now
                            spd = (got - have) / max(now - t0, 0.1) / 1e6
                            pct = 100 * got / EXPECTED
                            eta = (EXPECTED - got) / max(spd * 1e6, 1)
                            print(f"    {pct:5.1f}%  {got/1e6:7.1f}/{EXPECTED/1e6:.1f} MB  "
                                  f"{spd:5.2f} MB/s  ETA {eta:4.0f}s", flush=True)
            if os.path.getsize(part) < EXPECTED * 0.95:
                print(f"    incomplete ({os.path.getsize(part)/1e6:.1f} MB), retrying")
                time.sleep(3)
                continue
            os.replace(part, dest)
            print(f"  done: {os.path.getsize(dest)/1e6:.1f} MB")
            return dest
        except Exception as e:
            print(f"    error: {type(e).__name__}: {str(e)[:90]}")
            time.sleep(4)
    return None


print("strong net:", NAME)
print(f"expected  : {EXPECTED/1e6:.1f} MB")
print(f"free disk : {shutil.disk_usage(ROOT).free/1e9:.1f} GB")
print()
print("=== downloading ===")
src = download()
if not src:
    print("DOWNLOAD FAILED")
    sys.exit(1)

print()
print("=== decompressing ===")
out = os.path.join(MODELS, NAME[:-3])       # strip .gz
if os.path.exists(out) and os.path.getsize(out) > 10_000_000:
    print(f"  already extracted: {os.path.getsize(out)/1e6:.1f} MB")
else:
    t0 = time.time()
    with gzip.open(src, "rb") as fi, open(out + ".part", "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.replace(out + ".part", out)
    print(f"  extracted in {time.time()-t0:.0f}s -> {os.path.getsize(out)/1e6:.1f} MB")

print()
print("=== models now present ===")
for f in sorted(os.listdir(MODELS)):
    p = os.path.join(MODELS, f)
    print(f"  {f:50s} {os.path.getsize(p)/1e6:8.1f} MB")
print()
print("DONE")
