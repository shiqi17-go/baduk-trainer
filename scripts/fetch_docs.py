"""Fetch the official KataGo analysis docs and pull out the parts we need.

The search bridge is blocked, but raw.githubusercontent.com works via Python's
own TLS stack.
"""
import os
import re
import ssl
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ROOT = r"F:\harness\baduk-trainer"
DOCS = os.path.join(ROOT, "docs")
os.makedirs(DOCS, exist_ok=True)

FILES = [
    ("Analysis_Engine.md",
     "https://raw.githubusercontent.com/lightvector/KataGo/master/docs/Analysis_Engine.md"),
    ("GTP_Extensions.md",
     "https://raw.githubusercontent.com/lightvector/KataGo/master/docs/GTP_Extensions.md"),
]

for name, url in FILES:
    path = os.path.join(DOCS, name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            text = r.read().decode("utf-8", "ignore")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"saved {name}: {len(text)} chars, {len(text.splitlines())} lines")
    except Exception as e:
        print(f"FAILED {name}: {type(e).__name__} {str(e)[:80]}")

print()
print("=" * 78)
print("Query / response fields mentioned in Analysis_Engine.md")
print("=" * 78)
p = os.path.join(DOCS, "Analysis_Engine.md")
if os.path.exists(p):
    with open(p, encoding="utf-8") as f:
        text = f.read()

    # the field list section
    for keyword in ["includeOwnership", "includePolicy", "includeMovesOwnership",
                    "includePVVisits", "humanSL", "humanPolicy", "ownership",
                    "overrideSettings", "maxVisits", "analyzeTurns"]:
        hits = [m.start() for m in re.finditer(re.escape(keyword), text)]
        print(f"\n### {keyword}  ({len(hits)} mentions)")
        for h in hits[:2]:
            line_start = text.rfind("\n", 0, h) + 1
            line_end = text.find("\n", h)
            line = text[line_start:line_end].strip()
            print(f"   {line[:150]}")

    print()
    print("=" * 78)
    print("Section headings")
    print("=" * 78)
    for line in text.splitlines():
        if line.startswith("#"):
            print("  " + line[:110])
