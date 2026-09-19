"""Download KataGo engine + models. Resumable, with retries. ASCII output only.

Layout:
  F:\harness\baduk-trainer\downloads\   raw zips / .bin.gz
  F:\harness\baduk-trainer\engine\      extracted engine
  F:\harness\baduk-trainer\models\      .bin model files
"""
import gzip
import hashlib
import os
import shutil
import ssl
import sys
import time
import urllib.request
import zipfile

ROOT = r"F:\harness\baduk-trainer"
DL = os.path.join(ROOT, "downloads")
ENGINE = os.path.join(ROOT, "engine")
MODELS = os.path.join(ROOT, "models")
for d in (DL, ENGINE, MODELS):
    os.makedirs(d, exist_ok=True)

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
GH = "https://github.com/lightvector/KataGo/releases/download"

ITEMS = [
    {
        "name": "katago-v1.18.1-opencl-windows-x64.zip",
        "url": f"{GH}/v1.18.1/katago-v1.18.1-opencl-windows-x64.zip",
        "size": 6_000_000,
        "kind": "engine",
    },
    {
        "name": "b18c384nbt-humanv0.bin.gz",
        "url": f"{GH}/v1.15.0/b18c384nbt-humanv0.bin.gz",
        "size": 99_100_000,
        "kind": "model",
    },
    {
        "name": "b10c384h6nbttflrs.bin.gz",
        "url": f"{GH}/v1.17.1/b10c384h6nbttflrs.bin.gz",
        "size": 38_200_000,
        "kind": "model",
    },
]


def disk_free(path):
    u = shutil.disk_usage(path)
    return u.free / 1e9


def download(item, attempts=4):
    dest = os.path.join(DL, item["name"])
    part = dest + ".part"
    for attempt in range(1, attempts + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if os.path.exists(dest) and os.path.getsize(dest) > item["size"] * 0.9:
            print(f"  [skip] {item['name']} already downloaded "
                  f"({os.path.getsize(dest)/1e6:.1f} MB)")
            return dest
        headers = {"User-Agent": "Mozilla/5.0"}
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"  [resume] {item['name']} from {have/1e6:.1f} MB (attempt {attempt})")
        else:
            print(f"  [start] {item['name']} (attempt {attempt})")
        try:
            req = urllib.request.Request(item["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
                mode = "ab" if (have and r.status == 206) else "wb"
                if mode == "wb":
                    have = 0
                total = item["size"]
                clen = r.headers.get("Content-Length")
                if clen:
                    total = have + int(clen)
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
                        if now - last > 3:
                            last = now
                            spd = (got - have) / max(now - t0, 0.1) / 1e6
                            pct = 100 * got / total if total else 0
                            eta = (total - got) / max(spd * 1e6, 1)
                            print(f"    {pct:5.1f}%  {got/1e6:7.1f}/{total/1e6:.1f} MB  "
                                  f"{spd:5.2f} MB/s  ETA {eta:4.0f}s", flush=True)
            if item["size"] and os.path.getsize(part) < item["size"] * 0.9:
                print(f"    incomplete: {os.path.getsize(part)/1e6:.1f} MB, retrying")
                time.sleep(3)
                continue
            os.replace(part, dest)
            print(f"  [done] {item['name']}  {os.path.getsize(dest)/1e6:.1f} MB")
            return dest
        except Exception as e:
            print(f"    error: {type(e).__name__}: {str(e)[:90]}")
            time.sleep(4)
    print(f"  [FAIL] {item['name']}")
    return None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def gunzip_model(src):
    base = os.path.basename(src)
    if not base.endswith(".gz"):
        return None
    out = os.path.join(MODELS, base[:-3])
    if os.path.exists(out) and os.path.getsize(out) > 1_000_000:
        print(f"  [skip] {os.path.basename(out)} already extracted")
        return out
    print(f"  [unzip] {base} -> {os.path.basename(out)}")
    with gzip.open(src, "rb") as fi, open(out + ".part", "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.replace(out + ".part", out)
    print(f"  [done] {os.path.basename(out)}  {os.path.getsize(out)/1e6:.1f} MB")
    return out


print(f"target root: {ROOT}")
print(f"free disk:   {disk_free(ROOT):.1f} GB")
print()
print("=== downloading ===")
results = {}
for it in ITEMS:
    p = download(it)
    results[it["name"]] = p
    if p:
        print(f"    sha256 {sha256(p)}")

print()
print("=== extracting engine ===")
eng_zip = results.get("katago-v1.18.1-opencl-windows-x64.zip")
if eng_zip:
    with zipfile.ZipFile(eng_zip) as z:
        names = z.namelist()
        print(f"  {len(names)} entries, top: {sorted(set(n.split('/')[0] for n in names))[:6]}")
        z.extractall(ENGINE)
    print(f"  extracted to {ENGINE}")

print()
print("=== extracting models ===")
for key, path in results.items():
    if key.endswith(".bin.gz") and path:
        gunzip_model(path)

print()
print("=== final tree ===")
for base in ("engine", "models"):
    d = os.path.join(ROOT, base)
    for root, dirs, files in os.walk(d):
        for f in sorted(files):
            fp = os.path.join(root, f)
            print(f"  {os.path.relpath(fp, ROOT):60s} {os.path.getsize(fp)/1e6:8.1f} MB")
print()
print("ALL DONE")
