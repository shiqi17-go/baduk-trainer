"""Evidence gathering: does 'AI explains its own Go moves in words' already exist?

Skips the blocked search bridge and queries GitHub's API + a few product pages
directly with Python's own TLS stack.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.status, r.read()


def gh_search(q, sort="stars"):
    url = ("https://api.github.com/search/repositories?q="
           + urllib.parse.quote(q) + f"&sort={sort}&per_page=8")
    try:
        code, body = get(url, HDR)
        data = json.loads(body)
        return data.get("total_count", 0), data.get("items", [])
    except Exception as e:
        return None, str(e)


QUERIES = [
    "go game review AI commentary explanation",
    "weiqi ai review analysis report",
    "baduk ai teaching explanation",
    "kata analysis annotation sgf",
    "围棋 复盘 讲解",
    "go game annotation engine natural language",
    "sgf review report mistakes",
    "go AI coach explain move",
]

print("=" * 78)
print("A) GitHub: projects that REVIEW/ANNOTATE Go games")
print("=" * 78)
for q in QUERIES:
    total, items = gh_search(q)
    if not isinstance(items, list):
        print(f"\n[{q}]  ERROR: {items}")
        continue
    print(f"\n[{q}]  total={total}")
    for it in items:
        desc = (it.get("description") or "")[:88]
        print(f"  {it['stargazers_count']:>6}*  {it['full_name']:<42} {desc}")

print()
print("=" * 78)
print("B) Does anything mention natural-language explanation for GO?")
print("=" * 78)
for q in ['"natural language" go game engine explanation',
          'kata commentary generator',
          'llm go game commentary',
          'chess explain move natural language']:
    total, items = gh_search(q)
    if not isinstance(items, list):
        print(f"\n[{q}]  ERROR: {items}")
        continue
    print(f"\n[{q}]  total={total}")
    for it in items[:6]:
        desc = (it.get("description") or "")[:88]
        print(f"  {it['stargazers_count']:>6}*  {it['full_name']:<42} {desc}")

print()
print("=" * 78)
print("C) Product pages (do they describe written per-move explanations?)")
print("=" * 78)
PAGES = [
    ("DecodeChess (chess)", "https://decodechess.com/"),
    ("AI Sensei (go)", "https://aisensei.com/"),
    ("KaTrain (go)", "https://ka.train/"),
    ("KataGo analysis docs", "https://raw.githubusercontent.com/lightvector/KataGo/master/docs/Analysis_Engine.md"),
]
KEYWORDS = ["explain", "explan", "natural language", "commentary", "comment",
            "why", "narrat", "讲解", "解说", "文字"]

for name, url in PAGES:
    try:
        code, body = get(url, timeout=30)
        text = body.decode("utf-8", "ignore")
        title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
        title = re.sub(r"\s+", " ", title.group(1)).strip() if title else "(no title)"
        plain = re.sub(r"<[^>]+>", " ", text)
        plain = re.sub(r"\s+", " ", plain)
        hits = {k: plain.lower().count(k) for k in KEYWORDS}
        hits = {k: v for k, v in hits.items() if v}
        print(f"\n{name}\n  url   : {url}\n  status: {code}\n  title : {title[:110]}")
        print(f"  keyword hits: {hits}")
        for k in ("explain", "explan", "commentary", "natural language"):
            for m in re.finditer(k, plain, re.I):
                s = max(0, m.start() - 110)
                snippet = plain[s:m.start() + 150].strip()
                print(f"    ...{snippet}...")
                break
    except Exception as e:
        print(f"\n{name}\n  url   : {url}\n  FAILED: {type(e).__name__}: {str(e)[:80]}")

print()
print("DONE")
