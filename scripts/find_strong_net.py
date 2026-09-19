"""Find and download the strongest available neural net.

The b10 net we shipped was chosen to be small and fast because the app was
simulating weak human ranks. Teaching the best play needs a much stronger net.
"""
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}


def get(url, headers=None, timeout=40):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.status, r.read()


print("=" * 76)
print("A) strongest nets on GitHub releases")
print("=" * 76)
try:
    code, body = get("https://api.github.com/repos/lightvector/KataGo/releases?per_page=6", HDR)
    rels = json.loads(body)
    for rel in rels:
        nets = [(a["name"], a["size"], a["browser_download_url"])
                for a in rel["assets"]
                if a["name"].endswith(".bin.gz") and "human" not in a["name"].lower()]
        if nets:
            print(f"\n[{rel['tag_name']}]")
            for name, size, url in sorted(nets, key=lambda x: -x[1]):
                print(f"  {size/1e6:7.1f} MB  {name}")
except Exception as e:
    print("  FAILED:", type(e).__name__, str(e)[:90])

print()
print("=" * 76)
print("B) katagotraining.org latest nets")
print("=" * 76)
for url in ["https://katagotraining.org/networks/", "https://katagotraining.org/"]:
    try:
        code, body = get(url)
        text = body.decode("utf-8", "ignore")
        nets = sorted(set(re.findall(r"kata[\w\-\.]+\.bin\.gz", text)))
        print(f"\n{url} -> {code}, {len(nets)} nets mentioned")
        # show the newest-looking ones
        interesting = [n for n in nets if re.search(r"b(1[5-9]|2[0-9]|3[0-9])c", n)]
        for n in (interesting or nets)[:14]:
            print("   ", n)
        links = re.findall(r'href="([^"]*\.bin\.gz)"', text)
        if links:
            print("   download links found:", links[:3])
    except Exception as e:
        print(f"\n{url} FAILED: {type(e).__name__} {str(e)[:80]}")

print()
print("=" * 76)
print("C) is the media host reachable (that is where nets usually live)")
print("=" * 76)
for host in ["https://media.katagotraining.org/",
             "https://katagotraining.org/networks/",
             "https://katagoarchive.org/"]:
    try:
        code, body = get(host, timeout=25)
        print(f"  OK   [{code}] {host}")
    except Exception as e:
        print(f"  FAIL      {host}  {type(e).__name__}: {str(e)[:60]}")
