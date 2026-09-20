"""Download the GitHub CLI (gh) for Windows -- portable zip, no admin needed.

PowerShell on this box has a broken Schannel, so all HTTPS goes through Python.
"""
import json
import shutil
import urllib.request
import zipfile
import os

UA = {"User-Agent": "Mozilla/5.0"}
DEST_DIR = os.path.join(r"F:\harness\baduk-trainer", "tools")
GH_ZIP = os.path.join(DEST_DIR, "gh.zip")


def latest_gh():
    req = urllib.request.Request(
        "https://api.github.com/repos/cli/cli/releases/latest", headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    tag = data["tag_name"]
    ver = tag.lstrip("v")
    url = None
    for a in data["assets"]:
        n = a["name"]
        if n.endswith("_windows_amd64.zip"):
            url = a["browser_download_url"]
            break
    if url is None:
        url = f"https://github.com/cli/cli/releases/download/{tag}/gh_{ver}_windows_amd64.zip"
    return tag, url


def main():
    os.makedirs(DEST_DIR, exist_ok=True)
    tag, url = latest_gh()
    print(f"latest gh: {tag}")
    print(f"url: {url}")
    if not os.path.exists(GH_ZIP):
        print("downloading...")
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=120) as r, open(GH_ZIP, "wb") as f:
            shutil.copyfileobj(r, f)
        print(f"  -> {GH_ZIP}  {os.path.getsize(GH_ZIP)/1e6:.1f} MB")
    else:
        print(f"  already have {GH_ZIP}")
    print("extracting...")
    with zipfile.ZipFile(GH_ZIP) as z:
        z.extractall(DEST_DIR)
    # find gh.exe
    for root, _dirs, files in os.walk(DEST_DIR):
        if "gh.exe" in files:
            print("gh.exe at:", os.path.join(root, "gh.exe"))
            break
    print("DONE")


if __name__ == "__main__":
    main()
