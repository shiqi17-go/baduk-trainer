"""Find the CUDA / cuDNN runtime wheels we need for the CUDA build of KataGo.

KataGo's Windows CUDA builds do not bundle CUDA or cuDNN (licensing), so the
DLLs have to come from somewhere. NVIDIA publishes them as pip wheels, and a
wheel is just a zip -- so we can fetch and unpack them without pip, which also
sidesteps the sandbox's pip temp-directory restrictions.
"""
import json
import ssl
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}


def pypi_versions(pkg: str):
    req = urllib.request.Request(f"https://pypi.org/pypi/{pkg}/json", headers=HDR)
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        data = json.load(r)
    return data


PACKAGES = [
    "nvidia-cuda-runtime-cu12",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
]

for pkg in PACKAGES:
    print("=" * 76)
    print(pkg)
    print("=" * 76)
    try:
        data = pypi_versions(pkg)
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:80])
        continue

    releases = data.get("releases", {})
    # only versions that have a win_amd64 wheel
    wins = {}
    for ver, files in releases.items():
        for f in files:
            if f["filename"].endswith(".whl") and "win_amd64" in f["filename"]:
                wins[ver] = (f["filename"], f["size"], f["url"])
    if not wins:
        print("  no win_amd64 wheels")
        continue

    def key(v):
        try:
            return [int(x) for x in v.split(".")[:3]]
        except ValueError:
            return [0]

    ordered = sorted(wins, key=key, reverse=True)
    print(f"  {len(ordered)} win_amd64 versions; newest few:")
    for v in ordered[:6]:
        name, size, url = wins[v]
        print(f"    {v:12s} {size/1e6:8.1f} MB  {name}")
    # show ones matching the 12.8 / 9.8 generation the KataGo build targets
    for want in ("12.8", "9.8"):
        hits = [v for v in ordered if v.startswith(want)]
        if hits:
            v = hits[0]
            name, size, url = wins[v]
            print(f"  --> {want}.x match: {v}  ({size/1e6:.1f} MB)")
            print(f"      {url}")
