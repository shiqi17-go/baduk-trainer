"""Work out what the actually-strongest available net is.

The shipped net was picked on size/speed grounds without a head-to-head test.
There is also a b28c512 net on katagotraining that was never compared.
"""
import re
import ssl
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def get(url, timeout=40):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")


print("=" * 78)
print("katagotraining.org/networks -- what does it say is latest?")
print("=" * 78)
html = get("https://katagotraining.org/networks/")
text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
plain = re.sub(r"<[^>]+>", " ", text)
plain = re.sub(r"\s+", " ", plain)

# look for "latest" style statements
for kw in ["Latest", "latest network", "strongest", "recommend"]:
    for m in re.finditer(kw, plain):
        s = max(0, m.start() - 150)
        print(f"  ...{plain[s:m.start()+300].strip()}...")
        print()
        break

print()
print("=" * 78)
print("all nets with a bNNcMMM architecture token, newest-looking first")
print("=" * 78)
nets = sorted(set(re.findall(r"[\w\-\.]+\.bin\.gz", html)))
arch = {}
for n in nets:
    m = re.match(r"(?:kata1?-)?(?:tf\d+-)?b(\d+)c(\d+)", n)
    if m:
        blocks, chans = int(m.group(1)), int(m.group(2))
        arch.setdefault((blocks, chans), []).append(n)
for (b, c), names in sorted(arch.items(), key=lambda kv: -(kv[0][0] * kv[0][1])):
    print(f"  b{b} c{c}  (blocks*chans={b*c:>7})   {len(names)} nets")
    for n in sorted(names, reverse=True)[:3]:
        print(f"        {n}")

print()
print("=" * 78)
print("the two real candidates")
print("=" * 78)
incoming = [n for n in nets if "incoming" in n.lower()]
print("  incoming/promoted nets mentioned:", len(incoming))
for n in sorted(set(incoming), reverse=True)[:8]:
    print("   ", n)

print()
print("=" * 78)
print("what the KataGo README says about transformer strength")
print("=" * 78)
try:
    rd = get("https://raw.githubusercontent.com/lightvector/KataGo/master/README.md")
    for m in re.finditer(r"[^.]*transformer[^.]*\.", rd, re.I):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if len(s) > 40:
            print("  *", s[:300])
except Exception as e:
    print("  FAILED:", e)
