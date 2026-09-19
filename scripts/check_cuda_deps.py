"""Find which DLL the CUDA build of KataGo is missing.

Windows reports only 0xC0000135 (STATUS_DLL_NOT_FOUND) with no name, so walk the
import table recursively with pefile and report anything unresolvable.
"""
import os
import sys

try:
    import pefile
except ImportError:
    print("pefile not available")
    sys.exit(1)

ENGINE = r"F:\harness\baduk-trainer\engine"
CUDA = os.path.join(ENGINE, "cuda")

SEARCH = [ENGINE, CUDA, r"C:\Windows\System32", r"C:\Windows\SysWOW64",
          r"C:\Windows", os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                      "System32")]
# Python's own dir has the MSVC runtime DLLs too
SEARCH.append(os.path.dirname(sys.executable))


def find_dll(name: str):
    for d in SEARCH:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


seen: set[str] = set()
missing: dict[str, list[str]] = {}


def walk(path: str, depth: int = 0, parent: str = ""):
    if depth > 4:
        return
    key = os.path.basename(path).lower()
    if key in seen:
        return
    seen.add(key)
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ])
    except Exception as e:
        print(f"  ! cannot parse {os.path.basename(path)}: {e}")
        return

    deps = []
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for entry in getattr(pe, attr, []) or []:
            if entry.dll:
                deps.append(entry.dll.decode("ascii", "ignore"))
    pe.close()

    for d in deps:
        low = d.lower()
        # system DLLs that always exist
        if low.startswith(("api-ms-", "ext-ms-")) or low in (
                "kernel32.dll", "user32.dll", "advapi32.dll", "ws2_32.dll",
                "shell32.dll", "ole32.dll", "oleaut32.dll", "gdi32.dll",
                "ntdll.dll", "crypt32.dll", "bcrypt.dll", "version.dll",
                "powrprof.dll", "shlwapi.dll", "winmm.dll", "dbghelp.dll",
                "psapi.dll", "iphlpapi.dll", "setupapi.dll", "cfgmgr32.dll",
                "userenv.dll", "mswsock.dll", "dnsapi.dll", "winhttp.dll",
                "secur32.dll", "normaliz.dll", "imm32.dll", "comdlg32.dll"):
            continue
        found = find_dll(d)
        pad = "  " * (depth + 1)
        if found:
            if depth == 0:
                print(f"{pad}ok   {d}")
            walk(found, depth + 1, path)
        else:
            print(f"{pad}MISSING  {d}   <- needed by {os.path.basename(parent or path)}")
            missing.setdefault(d.lower(), []).append(os.path.basename(parent or path))


print("=" * 74)
print("dependency walk: katago-cuda.exe")
print("=" * 74)
exe = os.path.join(ENGINE, "katago-cuda.exe")
walk(exe)

print()
print("=" * 74)
if missing:
    print(f"UNRESOLVED: {len(missing)}")
    for k, v in sorted(missing.items()):
        print(f"  {k}   needed by {v}")
else:
    print("no unresolved non-system DLLs were found")
print("=" * 74)
