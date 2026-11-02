---
name: chapter-html-versions
description: Use when 书籍章节 md 要转 HTML：做成一页图表为主、10 分钟读完的可视化阅读版（正文约为源文 30–45%）。
---

# 章节 Markdown → 可视化阅读版 HTML

把 `$BOOKS_ROOT/books/<uid>/md/<NN>_<章名>.md` 做成一页 HTML：读者 10 分钟抓住这一章在讲什么、有哪几张关键图、能带走什么判断。
凡能用图、表、卡片说清的，就不写成段落。下文用 `$BOOKS_ROOT` 指书库仓库根目录。没设就先把 `BOOKS_ROOT` 指向有 `books.toml` 的工作区根，例如 `export BOOKS_ROOT=${BOOKS_ROOT:-$PWD}`。

## 产物

- `$BOOKS_ROOT/books/<uid>/html/<NN>_<章名>.html`
- 单文件，CSS 内联在 `<style>`；图片用相对路径 `images/<文件名>`（图片收在同级 `html/images/`）
- 体量 25–60 KB

## 流程

**父代理**：每章派一个 subagent → 全部章节跑一次校验 → 抽看 2–3 页 → 更新索引页。

**子代理**（一次做完）：
1. 读 `design-spec.md`（任务书）、`themes.md` 里指定的那套主题、本章源 md。
2. 用 `read_image` 看本章的原书插图，照原图重建图表，并把扫描件放进折叠块作参照。
3. 一次写出 HTML。
4. 自检：7 个必备区块是否齐全、源文 H2/H3 是否都有对应区块。
5. 跑校验，回报 ≤8 行（路径、字节、主题、图表数、正文与源文字数比、校验结果、没把握处）。

## 校验

```bash
python3 scripts/verify_lite.py <html...> --src <章节.md>
```

纯 Python、秒级，四项判定：结构完整 / 标题覆盖 ≥90% / 正文容器居中 / 本地图片存在；
另给两项 INFO：图表密度、必备模块命中数。

## 文件

| 文件 | 用途 |
|---|---|
| `design-spec.md` | 给 subagent 的任务书（填 5 个占位符即可发出） |
| `themes.md` | 4 套主题预设，按章轮换 |
| `scripts/verify_lite.py` | 唯一校验脚本 |
