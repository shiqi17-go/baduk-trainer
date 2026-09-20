---
name: github-publish
description: 把 baduk-trainer 发布或更新到 GitHub（shiqi17-go/baduk-trainer）时使用。涵盖建仓、推送代码、创建/更新 Release 并上传可运行 zip 的完整流程，以及本机已知的坑（Schannel 坏、沙盒不让写 AppData、梯子代理、.gitignore 行内注释失效、shell 凭据助手起不了管道）。
---

# 发布 baduk-trainer 到 GitHub

把源码推到 `shiqi17-go/baduk-trainer`（公开、MIT），并把可运行 zip 挂上 Release。
全部命令都能在沙盒里跑，但有几个本机特定的环境要求，**缺一个就会失败**，先照下面配好再执行。

## 仓库与凭据（已就位，勿重复创建）

- 远程仓库：`https://github.com/shiqi17-go/baduk-trainer`（public，已建仓，勿 `gh repo create` 重名仓库）
- gh CLI：`F:\harness\baduk-trainer\tools\bin\gh.exe`
- gh 配置/token：`F:\harness\baduk-trainer\tools\ghconfig`（token 作用域 `repo`）
- 可运行包：`F:\harness\baduk-trainer\dist\weiqi-peilian.zip`（发布前先重新打包）

## 本机三个必须（每次都设）

```powershell
$gh  = 'F:\harness\baduk-trainer\tools\bin\gh.exe'
$env:GH_CONFIG_DIR = 'F:\harness\baduk-trainer\tools\ghconfig'   # 沙盒不让写 AppData，配置放工作区
$env:HTTP_PROXY    = 'http://127.0.0.1:6864'                      # 梯子代理（gh 和 git 都要）
$env:HTTPS_PROXY   = 'http://127.0.0.1:6864'
```

git 另外要两样（本机 Schannel 坏了，且不走系统代理）：

```powershell
cd F:\harness\baduk-trainer
git config http.sslBackend openssl            # 不用 Windows 的 Schannel
git config http.proxy http://127.0.0.1:6864   # 走梯子代理（repo 本地配置，只影响本仓库）
```

## 推送代码

token 从 gh 拿，**临时嵌进 remote URL** 推完再清掉（不要用 shell 凭据助手——沙盒里 `sh.exe` 起不了管道）：

```powershell
$tok = & $gh auth token
git add -A
git commit -m "说明这次改了什么"
git remote set-url origin "https://shiqi17-go:$tok@github.com/shiqi17-go/baduk-trainer.git"
git push
git remote set-url origin https://github.com/shiqi17-go/baduk-trainer.git   # 推完清回干净 URL
```

> push 输出里的 `sh.exe ... couldn't create signal pipe` 是钩子在沙盒里的噪音，**只要最后有 `main -> main` 就是成功了**。

## 创建 / 更新 Release

先重新打包（`python scripts/build_exe.py`，产物在 `dist/weiqi-peilian.zip`），再传：

```powershell
# 新建一个版本的 Release 并上传 zip（大文件，耐心等，后台跑）
& $gh release create v1.1 "F:\harness\baduk-trainer\dist\weiqi-peilian.zip" `
    --repo shiqi17-go/baduk-trainer --title "围棋 AI 教学助手 v1.1" --notes "本次更新说明"

# 只更新已有版本的 zip（重传附件）
& $gh release upload v1.0 "F:\harness\baduk-trainer\dist\weiqi-peilian.zip" --clobber `
    --repo shiqi17-go/baduk-trainer
```

## 安全红线

- **`tools/` 绝不能提交进仓库**（里面有登录 token）。`.gitignore` 已排除，但注意：**`.gitignore` 不支持行内注释**——`tools/   # 注释` 会被当成匹配一个带空格的路径，永远匹配不到。注释必须单独占一行。
- 推送前随手查一下：`git ls-files | Select-String 'hosts.yml|ghconfig|^tools/'` 应为空。
- 如果 token 疑似泄露：立刻去 https://github.com/settings/tokens 撤销，再重新 `gh auth login`。

## 本机踩过的坑（症状 → 解法，下次直接照抄）

| 症状 | 解法 |
|---|---|
| gh 直连超时 `dial tcp ... connectex` | 设 `HTTP_PROXY`/`HTTPS_PROXY` 指向梯子代理（系统代理 gh 默认不读） |
| gh 授权完 token 没存上、`auth status` 说没登录 | 沙盒不让写 AppData，设 `GH_CONFIG_DIR` 到工作区再登 |
| `gh auth login` 起的服务被悄悄杀掉 | 别用 `Start-Process -PassThru` 跑；用后台任务让 gh 阻塞等你授权 |
| git push 报 `schannel: ... SEC_E_NO_CREDENTIALS` | `git config http.sslBackend openssl` |
| git push 报 `could not read Username` / sh 管道错 | 别用 shell 凭据助手，token 直接嵌 remote URL |
| `git check-ignore tools/` 返回"未忽略" | 检查 .gitignore 是不是写了行内注释（`#` 只能行首） |

## 首次发布的完整顺序（已实现，仅作参考）

1. 写 `.gitignore`（排除 engine/models/dist/downloads/tools 等大文件与凭证）、`LICENSE`(MIT)、`README.md`、`setup.py`
2. `git init` → `git add .` → `git commit`（确认只有约 250KB 源码，大文件全排除）
3. `gh repo create baduk-trainer --public --description "..."`（**已做过，勿重复**）
4. 配好 sslBackend + proxy，token 嵌 URL 推送
5. `gh release create` 上传 zip
