"""Follow-up: look closely at the two Go projects that mention explanation."""
import json
import ssl
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
HDR = {"User-Agent": "Mozilla/5.0", "Accept": "application/vnd.github+json"}


def get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.status, r.read()


REPOS = ["Zhuang-A/dsh-go-sensei", "mbroadfo/baduk", "Shreyaj-pseudo/AI-Chess-Coach"]

for full in REPOS:
    print("=" * 78)
    print(full)
    print("=" * 78)
    try:
        code, body = get(f"https://api.github.com/repos/{full}", HDR)
        d = json.loads(body)
        print(f"  description : {d.get('description')}")
        print(f"  stars       : {d.get('stargazers_count')}")
        print(f"  language    : {d.get('language')}")
        print(f"  created     : {d.get('created_at')}")
        print(f"  pushed      : {d.get('pushed_at')}")
        print(f"  topics      : {d.get('topics')}")
        print(f"  homepage    : {d.get('homepage')}")
        print(f"  license     : {(d.get('license') or {}).get('spdx_id')}")
    except Exception as e:
        print("  repo API failed:", type(e).__name__, str(e)[:90])

    for branch in ("main", "master"):
        for name in ("README.md", "README_EN.md", "readme.md"):
            url = f"https://raw.githubusercontent.com/{full}/{branch}/{name}"
            try:
                code, body = get(url)
                text = body.decode("utf-8", "ignore")
                print(f"\n  --- {name} ({branch}) : {len(text)} chars ---")
                print("\n".join("  | " + ln for ln in text.splitlines()[:60]))
                break
            except Exception:
                continue
        else:
            continue
        break
    print()
print("DONE")
