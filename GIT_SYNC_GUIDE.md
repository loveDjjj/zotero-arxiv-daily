# Git 同步指南

这个仓库当前的远程关系是：

- `origin = https://github.com/loveDjjj/zotero-arxiv-daily.git`
- `upstream = https://github.com/TideDra/zotero-arxiv-daily.git`

推荐的分支职责：

- `main`：尽量保持和原仓库一致
- `my-custom`：放你自己的个性化修改

## 1. 首次初始化

先检查远程仓库配置：

```bash
git remote -v
```

创建并推送你的自定义分支：

```bash
git checkout main
git checkout -b my-custom
git push -u origin my-custom
```

如果本地已经创建过 `my-custom`，那就直接推送：

```bash
git checkout my-custom
git push -u origin my-custom
```

## 2. 日常修改自己的内容

平时自己的改动都放在 `my-custom`：

```bash
git checkout my-custom
git add .
git commit -m "描述你的修改"
git push
```

## 3. 同步原仓库更新

先把原仓库的更新同步到本地 `main`，再推送到你的 fork：

```bash
git checkout main
git fetch upstream
git merge upstream/main
git push origin main
```

然后再把更新后的 `main` 合并到你的 `my-custom`：

```bash
git checkout my-custom
git merge main
git push
```

同步链路如下：

```text
upstream/main -> local main -> origin/main -> local my-custom -> origin/my-custom
```

## 4. 如果发生冲突

冲突通常表示你和原仓库改了同一段代码。

手动处理冲突后执行：

```bash
git add .
git commit
git push
```

## 5. GitHub Actions 到底跑哪个分支

这个仓库的定时任务 workflow 文件定义在默认分支上，但实际运行的代码由
`.github/workflows/main.yml` 里的两个 GitHub Actions 变量控制：

- `REPOSITORY`
- `REF`

对你这个 fork，推荐设置为：

```text
REPOSITORY = loveDjjj/zotero-arxiv-daily
REF = my-custom
```

这意味着：

- workflow 文件仍然放在默认分支 `main`
- 定时任务实际可以运行 `my-custom` 上的代码

## 6. 最短命令版

你自己平时改代码：

```bash
git checkout my-custom
git add .
git commit -m "你的修改说明"
git push
```

同步原仓库更新：

```bash
git checkout main
git fetch upstream
git merge upstream/main
git push origin main
git checkout my-custom
git merge main
git push
```
