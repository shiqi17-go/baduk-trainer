"""Check the licences that matter for registering and redistributing this."""
import os
import re
import ssl
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0"}


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")


FILES = [
    ("KataGo LICENSE", "https://raw.githubusercontent.com/lightvector/KataGo/master/LICENSE"),
    ("KataGo README (licence section)",
     "https://raw.githubusercontent.com/lightvector/KataGo/master/README.md"),
]

for name, url in FILES:
    print("=" * 76)
    print(name)
    print("=" * 76)
    try:
        text = get(url)
        if "LICENSE" in name:
            print(text[:1400])
        else:
            # pull out any licence / model-licence discussion
            for m in re.finditer(r"(?i)(licen[cs]e|MIT|public domain|model[s]? (are|is))", text):
                s = max(0, m.start() - 200)
                snippet = re.sub(r"\s+", " ", text[s:m.start() + 400])
                print("  ...", snippet[:420], "\n")
    except Exception as e:
        print("  FAILED:", type(e).__name__, str(e)[:80])
    print()

print("=" * 76)
print("Local files we ship")
print("=" * 76)
for rel in ["engine/README.txt"]:
    p = os.path.join(r"F:\harness\baduk-trainer", rel)
    if os.path.exists(p):
        with open(p, encoding="utf-8", errors="ignore") as f:
            t = f.read()
        print(f"--- {rel} (first 900 chars) ---")
        print(t[:900])
