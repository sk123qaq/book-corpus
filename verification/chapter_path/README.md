# 章节路径检查（chapter path）

守一条用户可见的承诺：**徽章说“深度路径”，点进去就必须能打开**。

- **P1 AdvertisedDepthIsReadable**：`path == "depth"` ⟹ `GET /reader/<uid>/<n>/depth` 返回 200。
- **P2 AuditPathMatchesService**：徽章路径（`none` 读作 `missing`）等于 `depth.audit` 报告的 `path`。

三个读者共用 `Store.chapter_route`（`src/bookcorpus/reading/store.py`）：徽章的 `_chapter_path`、
`_serve_depth` 的闸门、`_audit_chapter` 的 `path`。规则是“ready 是唯一承诺页面可开的状
态；旧包 manifest 只作兜底；其余报 `none` 并给出原因”，所以 model / stale 状态不会
再承诺一个 404 的页面。

## 跑

```bash
uv run --frozen python verification/chapter_path/check_paths.py   # 真实代码 + 真实 HTTP
```

枚举 96 个磁盘状态，只对模型可达的 16 个起真实 server，退出码 0 才算过。

TLA+ 穷举（P1、P2 各一个 config）：

```bash
java -cp "$TLA2TOOLS" tlc2.TLC -config verification/chapter_path/ChapterPath.cfg \
  -metadir /tmp/tlc-cp-p1 -cleanup -noGenerateSpecTE -noGenerateSpecTEBin \
  verification/chapter_path/ChapterPath.tla
java -cp "$TLA2TOOLS" tlc2.TLC -config verification/chapter_path/ChapterPathAudit.cfg \
  -metadir /tmp/tlc-cp-p2 -cleanup -noGenerateSpecTE -noGenerateSpecTEBin \
  verification/chapter_path/ChapterPath.tla
```

出图用 `/statechart`，view 是 `verification/chapter_path/view_path.js`。

## 现状

2026-11-10，`feat/reading-depth-9` 上收紧后：harness `96 states / 16 reachable / 0 violation`，
TLC 两个 config 均 16 distinct states、退出 0。收紧前（`9e823f9`）是 18 violations（10 P1、8 P2）。

## 改代码时

先改产品代码，再让模型跟上：`ChapterPath.tla` 的 `DepthState` / `ServicePath` / `Readable` /
`AuditPath`，以及 `check_paths.py` 的 `reachable()`。不要只改不变式让它通过，也不要为了让
P1 通过而把 model / stale 直接降成 legacy——纯深度包可能没有 legacy 的 `chapter.html`，
会制造新的 404。

模型只抽象单章磁盘字节：不含任务队列、并发写、`POST /api/jobs` 的分支与内容门禁。
