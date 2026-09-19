"""Try to fetch a strong net from katagotraining's media host (403 might just
be hotlink protection), and report which candidate URLs are actually usable."""
import re
import ssl
import urllib.error
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

CANDIDATES = [
    ("kata1-tf3-b11c768-s11750M-d6216M",
     "https://media.katagotraining.org/uploaded/networks/models/kata1/"
     "kata1-tf3-b11c768-s11750M-d6216M.bin.gz"),
    ("kata1-tf3-b11c768-s11001M-d5973M",
     "https://media.katagotraining.org/uploaded/networks/models/kata1/"
     "kata1-tf3-b11c768-s11001M-d5973M.bin.gz"),
    ("kata1-b18c384nbt (latest kata1 run)",
     "https://media.katagotraining.org/uploaded/networks/models/kata1/"
     "kata1-b18c384nbt-s6018394112-d3271316900.bin.gz"),
    ("b28c512nbt-v16",
     "https://media.katagotraining.org/uploaded/networks/models/kata1/"
     "kata-b28c512nbt-v16-s9549M-d4951M.bin.gz"),
]

HEADERS = [
    ("plain", {"User-Agent": "Mozilla/5.0"}),
    ("with referer", {"User-Agent": "Mozilla/5.0",
                      "Referer": "https://katagotraining.org/networks/"}),
    ("browser-ish", {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                                   "Chrome/120.0 Safari/537.36",
                     "Referer": "https://katagotraining.org/",
                     "Accept": "*/*"}),
]

for name, url in CANDIDATES:
    print(f"\n{name}")
    print(f"  {url}")
    for label, hdr in HEADERS:
        try:
            req = urllib.request.Request(url, headers={**hdr, "Range": "bytes=0-1023"})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                head = r.read(64)
                total = r.headers.get("Content-Range") or r.headers.get("Content-Length")
                print(f"    OK   [{label}] status={r.status} size={total} "
                      f"magic={head[:2].hex()}")
                break
        except urllib.error.HTTPError as e:
            print(f"    FAIL [{label}] HTTP {e.code}")
        except Exception as e:
            print(f"    FAIL [{label}] {type(e).__name__}: {str(e)[:60]}")

print()
print("=" * 76)
print("GitHub fallback (known good)")
print("=" * 76)
u = ("https://github.com/lightvector/KataGo/releases/download/v1.17.1/"
     "b11c768h12nbt3tflrs-fson-silu.bin.gz")
try:
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0",
                                             "Range": "bytes=0-1023"})
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        print(f"  OK  status={r.status} magic={r.read(2).hex()}  (gzip should be 1f8b)")
except Exception as e:
    print(f"  FAIL {type(e).__name__}: {str(e)[:80]}")
