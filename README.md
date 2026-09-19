# 围棋 AI 教学助手（Baduk Trainer）

一个能跟**最强围棋 AI** 下棋、并且**用中文讲清「AI 为什么这么下」**的教学软件。

完全离线运行，不联网、不注册、不收费。引擎用开源的 [KataGo](https://github.com/lightvector/KataGo)，权重是官方标注的**当前最强网络**。

> 市面工具都只给「胜率/目差」这些数字，没法告诉学生**为什么**。这个程序把 AI 的判断翻译成自然语言：哪块棋危险、哪里可攻、该往哪走、为什么，以及"你这个水平的人常下哪、差多少目"。

---

## 三个界面

| 界面 | 干什么 |
|---|---|
| **对弈** | 跟最强（满血 KataGo）或指定段位的 AI 下棋；支持让子、悔棋、真实形势判断、导出 SGF |
| **打谱分析** | 自由摆子，每手即时给出候选点、地盘归属图、后续变化图，以及**叙述式讲解**（局面 / 为什么下这里 / 紧迫度 / 三级对照 / 棋理） |
| **错题本** | 导入学生棋谱自动分析，**把自己的失误自动生成题目**，摆上局面自己答、判对错 |

另外两个界面里都有「**存入错题本**」按钮——下完棋一键把棋谱送进去出题。

---

## 怎么安装（普通用户）

不用装 Python，不用装 CUDA，不用联网。

1. 去右侧 **Releases** 下载 `weiqi-peilian.zip`
2. 解压，得到 `weiqi-peilian` 文件夹
3. 双击里面的 `weiqi-peilian.exe`

> ⚠️ 必须整个文件夹一起解压，不能只把 exe 单独拖出来——引擎和模型在旁边的 `engine`、`models` 文件夹里。

**第一次打开要等 10~20 秒**（正在加载 AI 引擎）。

### 电脑要求

- Windows 10 / 11，64 位
- **有 NVIDIA 显卡最好**（CUDA 后端，比 OpenCL 快 2.5 倍）
- 没有独显也能跑，但分析会慢
- 约 3 GB 空闲磁盘

---

## 从源码运行 / 编译（开发者）

### 环境

- Python 3.10+
- 无第三方 Python 依赖（只用标准库 + tkinter）

### 一键补齐引擎和模型

程序运行需要 KataGo 引擎（约 100MB）、神经网络（约 370MB）、CUDA 运行库（可选，约 1.7GB）。

```powershell
python setup.py
```

`setup.py` 会自动从官方源下载并解压到 `engine/`、`models/`。

### 运行

```powershell
python main.py
```

### 打包成 exe（Windows）

```powershell
python scripts/build_exe.py
```

产物在 `dist/weiqi-peilian/`。

### 测试

```powershell
python scripts/test_local.py     # 棋盘规则 + 坐标 + 区域
python scripts/test_study.py     # 打谱模式
python scripts/test_main_app.py  # 主窗口三标签
python scripts/test_trainer.py   # 错题本
python main.py --selftest        # 打包后的端到端自检
```

---

## 讲解是怎么来的（为什么它不会瞎说）

这是这个程序和纯大模型讲解的本质区别：

```
KataGo 出事实（数字绝对准确）
        ↓
结构层（所有权差分 / 棋块连通分析 / 紧迫度 / 人类段位对照）
        ↓
模板生成中文（数字全部来自引擎，一个字都不编）
```

每个数字都有出处。我们实测过：该网络的**胜率**输出在低搜索量下校准不佳（贴目差 1 目，胜率会跳 20 个百分点），所以所有判断一律以**目差**为准。

---

## 技术栈

- **引擎**：KataGo v1.18.1（CUDA + cuDNN，无 N 卡时回退 OpenCL）
- **权重**：`kata1-tf3-b11c768-s11750M-d6216M`（官方标注 Latest network）+ `b18c384nbt-humanv0`（人类段位模型）
- **界面**：Python tkinter（无第三方依赖）
- **打包**：PyInstaller

## 许可证

- 本项目的代码：**MIT**（见 [LICENSE](LICENSE)）
- KataGo：MIT，见 `licenses/KataGo-LICENSE.txt`

## 致谢

- [KataGo](https://github.com/lightvector/KataGo) by David J Wu (lightvector)
- KataGo 官方训练组（katagotraining.org）提供的神经网络
