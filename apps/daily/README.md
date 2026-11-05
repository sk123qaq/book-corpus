# 书库日课

每日一章的只读阅读页。数据来自数据工作区的 `日课/*.json`，这一页只读，不写书、不写账。

## 启动

```bash
python3 apps/daily/server.py --port 8772 --root /home/colaqaq/书库
# 或者
BOOK_CORPUS_DATA=/home/colaqaq/书库 python3 apps/daily/server.py
```

打开 <http://127.0.0.1:8772>。端口默认 8772，`--root` 默认取 `BOOK_CORPUS_DATA`，没有就用 `~/书库`。

## 数据契约

一期一份 JSON，文件名必须等于 `date`，且是合法的 `YYYY-MM-DD`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `version` | 整数 | 从 1 起 |
| `date` | 字符串 | `YYYY-MM-DD`，文件名与它一致 |
| `kind` | 字符串 | `expository` 或 `fiction` |
| `kindLabel` | 字符串 | 页面上的类别，例如「论述」「小说」 |
| `subject` | 字符串 | 主题，例如「行为经济学」 |
| `book` | 对象 | `uid`、`title`、`author` |
| `chapter` | 对象 | `index`、`title`、`path`，可选 `label`；`path` 相对书库根目录 |
| `headline` | 字符串 | 本期标题 |
| `body` | 字符串数组 | 3 到 5 段正文，300 到 500 字 |
| `quote` | 对象 | `text`、`line` |
| `why` | 字符串 | 为什么选这一章 |
| `research` | 对象 | `chapters`、`web`、`queries`，可选 `note` |

页面按日期倒序读取，按日期升序补 `issue` 序号，所以最新一期序号最大，显示为「第 004 期」。坏 JSON、坏日期、文件名与 `date` 不符、字段不合约的文件会跳过，错误进 `/api/feed` 的 `errors`，不影响其它期。目录不存在时返回空列表和一条错误，不抛异常。只读，不缓存到磁盘。

## 接口

- `GET /api/feed`：`{today, dir, issues, errors}`，按日期倒序，`Cache-Control: no-store`
- `GET /api/issue/<date>`：单期；没有就 404 JSON
- 静态只放行 `/`、`/index.html`、`/app.js`、`/style.css`

服务只绑 `127.0.0.1`。Host 头只接受 `127.0.0.1` 与 `localhost`，可带端口，其余 403。所有响应带 `X-Content-Type-Options: nosniff`。

## 生成

```bash
book-corpus daily --root /home/colaqaq/书库 --dry-run   # 只看选书与提示词
book-corpus daily --root /home/colaqaq/书库             # 写当天一期
book-corpus daily install-timer --root /home/colaqaq/书库 --at 07:00
```

生成器按日期做随机种子，同一天重跑会选同一章；已有当天文件时默认跳过，`--force` 才重写。`--book B0003 --chapter 2` 可以指定书与章。写盘前会校验字数、引文是否逐字出现在本章、行号是否对得上；两次不过就报错，不留半成品。

## 只读边界

- 不写 `books/`、不写 `tables/`、不写日课 JSON。
- 不落盘缓存。
- 页面没有外部字体、CDN、图标库，除同源 `/api` 外不发网络请求。

## 验收

```bash
python3 -m pytest tests/acceptance/daily -q
python3 -m ruff check apps/daily tests/acceptance/daily
```

人工视觉与交互清单见 `tests/manual/daily/README.md`。
