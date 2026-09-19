"""Build the shareable Windows program.

Produces  dist/weiqi-peilian/  containing:
    weiqi-peilian.exe     the app (Python + tkinter bundled by PyInstaller)
    engine/               KataGo v1.18.1 (OpenCL) + official config templates
    models/               b10 main net + b18 human-SL (rank) net

The whole folder is what you hand to a classmate: unzip, double-click the exe.
Nothing to install -- Python, KataGo and the neural nets are all inside.

Run:  python scripts/build_exe.py
"""
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
APP_NAME = "weiqi-peilian"
OUT_DIR = os.path.join(DIST, APP_NAME)

ENTRY = os.path.join(ROOT, "main.py")


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def dir_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def run(cmd, **kw):
    print("$", " ".join(cmd if isinstance(cmd, list) else [cmd]), flush=True)
    return subprocess.run(cmd, cwd=ROOT, **kw)


print("=" * 70)
print("BUILD  weiqi-peilian")
print("=" * 70)
print("project :", ROOT)
print("python  :", sys.version.split()[0])
print("frozen  :", getattr(sys, "frozen", False))

# Keep every temporary file inside the project: the sandbox on this machine
# blocks writes to the usual system temp area.
tmp = os.path.join(ROOT, ".buildtmp")
shutil.rmtree(tmp, ignore_errors=True)
os.makedirs(tmp, exist_ok=True)
env = dict(os.environ, TEMP=tmp, TMP=tmp, PYTHONIOENCODING="utf-8")

print()
print("=== 1/4 cleaning previous output ===")
for d in (DIST, BUILD):
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        print("  removed", os.path.relpath(d, ROOT))

print()
print("=== 2/4 running PyInstaller ===")
t0 = time.time()
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean",
    "--onedir",
    "--windowed",
    "--name", APP_NAME,
    "--distpath", DIST,
    "--workpath", BUILD,
    "--specpath", BUILD,
    "--paths", ROOT,
    # the trainer tab is imported lazily inside try/except, which PyInstaller's
    # static analysis missed -- the packaged exe shipped without it, showing
    # only two tabs. Declare it (and every module it needs) explicitly.
    "--hidden-import", "trainer",
    "--hidden-import", "library",
    "--hidden-import", "analysis",
    "--hidden-import", "goban",
    "--hidden-import", "explain",
    "--hidden-import", "katago_engine",
    # trainer needs sqlite3 for the game library; PyInstaller omitted the
    # _sqlite3 C-extension DLL, which broke the whole exe at import time.
    "--collect-all", "sqlite3",
    "--exclude-module", "matplotlib",
    "--exclude-module", "numpy",
    "--exclude-module", "PIL",
    "--exclude-module", "pandas",
    "--exclude-module", "scipy",
    ENTRY,
]
r = run(cmd, env=env)
print(f"  PyInstaller finished in {time.time()-t0:.1f}s  rc={r.returncode}")
if r.returncode != 0:
    print("  BUILD FAILED")
    sys.exit(r.returncode)

exe = os.path.join(OUT_DIR, APP_NAME + ".exe")
if not os.path.exists(exe):
    print("  expected exe missing:", exe)
    sys.exit(1)
print("  exe:", os.path.relpath(exe, ROOT), human(os.path.getsize(exe)))

print()
print("=== 3/5 fixing the Tcl/Tk DLL version mismatch ===")
# Anaconda ships tcl/tk twice: conda's copy under Library\bin (8.6.15 here) and
# an older one in the base env's DLLs (8.6.8). PyInstaller's dependency scan
# picks the old DLL but bundles the new *data* files (init.tcl), so the frozen
# app dies at startup with "version conflict for package Tcl".
# Overwrite the bundled DLLs with the ones matching the data files.
internal = os.path.join(OUT_DIR, "_internal")
env_bin = os.path.join(sys.prefix, "Library", "bin")
for dll in ("tcl86t.dll", "tk86t.dll"):
    dst = os.path.join(internal, dll)
    src = os.path.join(env_bin, dll)
    if os.path.exists(src) and os.path.exists(dst):
        before = os.path.getsize(dst)
        shutil.copy2(src, dst)
        after = os.path.getsize(dst)
        note = "replaced" if before != after else "same size"
        print(f"  {dll:12s} {before:>9,} -> {after:>9,} bytes  ({note})")
    elif not os.path.exists(src):
        print(f"  {dll:12s} source not found at {src} (skipped)")
    else:
        print(f"  {dll:12s} not present in the bundle (skipped)")

# show what the bundled data files demand, for the record
for data, fname, pat in (("_tcl_data", "init.tcl", "Tcl"),
                         ("_tk_data", "tk.tcl", "Tk")):
    p = os.path.join(internal, data, fname)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "package require -exact" in line and pat in line:
                        print(f"  {data}/{fname} demands:{line.split('-exact')[1].strip()}")
                        break
        except Exception:
            pass

# Anaconda also mismatches the SQLite DLL: PyInstaller bundles a stale 0.92 MB
# sqlite3.dll, but Anaconda's own _sqlite3.pyd is built against the 1.58 MB one
# in Library\bin, so the frozen exe died at import time with WinError 127
# (function not found). Overwrite with the matching copy.
sq_src = os.path.join(env_bin, "sqlite3.dll")
sq_dst = os.path.join(internal, "sqlite3.dll")
if os.path.exists(sq_src) and os.path.exists(sq_dst):
    before = os.path.getsize(sq_dst)
    shutil.copy2(sq_src, sq_dst)
    print(f"  sqlite3.dll  {before:>9,} -> {os.path.getsize(sq_dst):>9,} bytes  (replaced)")
elif not os.path.exists(sq_src):
    print("  sqlite3.dll  source not found (skipped)")
else:
    print("  sqlite3.dll  not present in the bundle (skipped)")

print()
print("=== 4/5 copying engine, models and licences next to the exe ===")
# The small b10 net shipped in earlier builds only because the app imitated weak
# human ranks. It now teaches best play, so only the strong net goes out -- that
# keeps roughly 42 MB out of the package.
MODEL_EXCLUDE = ("b10c384h6nbttflrs.bin",)
for name in ("engine", "models"):
    src = os.path.join(ROOT, name)
    dst = os.path.join(OUT_DIR, name)
    if not os.path.isdir(src):
        print(f"  MISSING {name}/ -- skipped")
        continue
    shutil.copytree(src, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(*MODEL_EXCLUDE) if name == "models" else None)
    print(f"  {name:8s} -> {os.path.relpath(dst, ROOT):45s} {human(dir_size(dst))}")
    if name == "models":
        for f in sorted(os.listdir(dst)):
            print(f"             {f}  ({os.path.getsize(os.path.join(dst, f))/1e6:.1f} MB)")
        for f in MODEL_EXCLUDE:
            if os.path.exists(os.path.join(dst, f)):
                print(f"             WARNING: {f} should have been excluded")

# KataGo is MIT licensed: redistribution has to carry its copyright notice and
# licence text. Earlier builds omitted this, which was a compliance gap.
lic_src = os.path.join(ROOT, "licenses")
lic_dst = os.path.join(OUT_DIR, "licenses")
if os.path.isdir(lic_src):
    shutil.copytree(lic_src, lic_dst, dirs_exist_ok=True)
    print(f"  licenses -> {os.path.relpath(lic_dst, ROOT):45s} {human(dir_size(lic_dst))}")
else:
    print("  WARNING: licenses/ missing -- the package would lack KataGo's MIT notice")

# a plain-text readme inside the shipped folder
readme = os.path.join(OUT_DIR, "使用说明.txt")
with open(readme, "w", encoding="utf-8") as f:
    f.write(
        "围棋 AI 教学助手 —— 使用说明\n"
        "============================\n\n"
        "双击 weiqi-peilian.exe 启动（不要把这个文件夹里的文件单独挪出去）。\n"
        "程序有两个模式，用窗口顶部的标签切换。\n\n"
        "【对弈】和 AI 下棋\n"
        "  1. 左侧「对手段位」有两种含义，别搞混：\n"
        "       · 最强（满血 KataGo）——真·最强，关掉人类风格模型，搜索量大。\n"
        "         想学最好的棋就选这个。\n"
        "       · 5级 / 9段 等——模仿「该段位人类的下法风格」，不是满血强度。\n"
        "         按 KataGo 官方文档，高段位的模仿达不到被模仿者的真实棋力。\n"
        "  2. 你执黑还是执白、要不要让子。点「新对局」，等约 15 秒加载。\n"
        "  3. 在棋盘上点交叉点落子，AI 会自动应手。\n"
        "  4. 「悔棋」一次退两手（你一手 + AI 一手）。\n"
        "  5. 「保存棋谱」导出 SGF。\n\n"
        "【打谱分析】自由摆子，每手即时看 AI 的判断\n"
        "  1. 左键点棋盘落子（右键可擦掉一颗），约 1 秒出分析。\n"
        "  2. 棋盘上带序号的圆点是 AI 的候选点，绿=首选，蓝/橙/灰依次变差。\n"
        "  3. 勾「显示地盘归属」可以把 AI 的形势判断直接画在棋盘上。\n"
        "  4. 「讲解这一手」把当前局面的 AI 判断写成中文，含战略方向、\n"
        "     脱先代价、术语解释，以及「人类常下哪、差多少目」。\n"
        "  5. 「整盘复盘报告」逐手分析，报告写到 samples 文件夹。\n\n"
        "说明：\n"
        "- 打谱模式第一次切过去要等约 15 秒启动分析引擎。\n"
        "- 换新网络后第一次启动需要几分钟自动调优（每个显卡一次），之后正常。\n"
        "- 所有判断以 AI 的「目差」为准。实测胜率输出在低搜索量下校准不佳\n"
        "  （贴目差 1 目，胜率会跳 20 个百分点），故不采用。\n"
        "- 段位模拟的是「该段位人类的下法风格」，不同人的体感会有差异。\n"
        "- 需要 NVIDIA 显卡驱动（用 OpenCL，不需要装 CUDA）。\n"
        "- 如果启动失败，同目录下会生成 error.log，把它发给我。\n"
        "- licenses 文件夹里是 KataGo 的 MIT 许可证，请勿删除。\n"
    )
print("  readme   ->", os.path.relpath(readme, ROOT))

print()
print("=== 5/5 done ===")
print(f"  folder : {OUT_DIR}")
print(f"  size   : {human(dir_size(OUT_DIR))}")
print(f"  files  : {sum(len(f) for _, _, f in os.walk(OUT_DIR))}")
print()
print("把整个文件夹压缩后发给同学即可：")
print(f'  Compress-Archive -Path "{OUT_DIR}" -DestinationPath "{DIST}\\weiqi-peilian.zip"')
