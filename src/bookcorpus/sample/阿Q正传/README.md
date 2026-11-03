# 样书：《阿Q正传》

这是 `book-corpus sample` 用的样书，用来在空工作区里演示一遍「入库 → 拆章 → 建库 → 建索引 → 检索」。

## 版权

鲁迅（1881–1936）的作品在中国已过著作权保护期，属公有领域。底本取自 Project Gutenberg 电子书 #27166《吶喊》（*Call to Arms*），该站点提供的文本本身也属公有领域。

- 底本：https://www.gutenberg.org/ebooks/27166
- 抓取日期：2026-11-03
- 文本：`source/阿Q正传.txt`，由底本繁体转简体（OpenCC `tw2s`），保留原标点与分段

## 目录

`md/` 是按原文九章拆好的 Markdown，`NN_第X章<标题>.md` 命名，front matter 里的 `source` 指回 `source/阿Q正传.txt`。`book-corpus sample` 把这棵 `md/` 原样复制进工作区，不重新拆章。

拆章只做了确定性的整理：繁转简、去脚注残留、按句末标点补段落分隔。原文的分段在 Gutenberg 纯文本里已被行距抹平，个别人物对话可能并到了同一段。

## 换书

这本书只示范格式。自己建库时，按 `books/_收件箱/README.md` 的流程放源文件，拆章产物照 `md/` 这套命名与 front matter 写，就能被 `build_para_index.py` 收进索引。
