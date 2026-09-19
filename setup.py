#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键补齐运行所需的引擎和模型。

本仓库只放源码（约 250KB）。程序要跑起来还需要：
  1. KataGo 引擎（约 100MB，官方发行版）
  2. 神经网络（约 370MB，官方训练组）
  3. CUDA 运行库（可选，约 1.7GB，NVIDIA pip wheel——有 NVIDIA 显卡时更快）

这些全部能从官方源下载，不该进 GitHub。跑这个脚本自动补齐：

    python setup.py             # 引擎 + 模型（必需）
    python setup.py --cuda      # 另外补齐 CUDA 运行库（有 NVIDIA 显卡推荐）

全部是幂等的：已有的文件会跳过，可以重复跑。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(ROOT, "scripts")
PY = sys.executable


def run(name: str, args: list[str]) -> bool:
    script = os.path.join(SCRIPTS, name)
    if not os.path.exists(script):
        print(f"  [skip] 找不到 {name}")
        return False
    print(f"\n>>> 运行 {name} {' '.join(args)}")
    r = subprocess.run([PY, "-u", script, *args])
    return r.returncode == 0


def main() -> int:
    want_cuda = "--cuda" in sys.argv

    print("=" * 72)
    print("围棋 AI 教学助手 — 补齐引擎和模型")
    print("=" * 72)
    print("会下载（都是官方源）：")
    print("  · KataGo v1.18.1 引擎（OpenCL）")
    print("  · humanSL 人类段位模型 b18c384nbt-humanv0")
    print("  · 最强网络 kata1-tf3-b11c768-s11750M-d6216M")
    if want_cuda:
        print("  · KataGo CUDA 版 + CUDA/cuDNN/nvrtc 运行库（约 1.7GB）")

    ok = True

    # 1) 引擎 + humanSL 模型（必需，否则打不开）
    ok = run("download_katago.py", []) and ok

    # 2) 最强网络（必需）
    ok = run("download_strong_net.py", []) and ok

    # 3) CUDA 后端（可选，仅 --cuda 时）
    if want_cuda:
        ok = run("install_cuda_backend.py", []) and ok
        ok = run("install_nvrtc.py", []) and ok

    print()
    print("=" * 72)
    if ok:
        print("OK - 全部补齐。运行 python main.py 即可。")
    else:
        print("FAILED - 有下载失败。多半是网络问题——挂个梯子，或者把上面"
              "没下完的文件手动下载后重跑本脚本（已下载的部分会跳过）。")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
