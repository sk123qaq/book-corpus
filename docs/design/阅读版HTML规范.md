# 阅读版 HTML 规范

本文件是阅读版章节 HTML 的生成要求与验收清单，唯一机器标准是
`src/bookcorpus/reading/quality.py` 的 `check_html`。文档里的阈值与 CSS 数值必须与代码一致，
改动任何一边都要同步另一边。

## 适用范围

管的是 `render_chapter` / `render_export` 产出的单章 HTML，以及 `chapter-html-versions`
技能里手写或拼接的同类页面。不含操作台界面、`build/阅读版索引.html` 这类索引页，
也不含打印专用排版（打印只在 `@media print` 里限定字号与行高）。

## 三层质量

| 层 | 问的问题 | 判定方式 |
|---|---|---|
| legibility 可辨认 | 字够不够大、对比够不够、有没有外链拖慢或断网失败 | 硬指标，fail |
| readability 可读 | 一行多少字、行距、段与段怎么分、图注能不能读 | 硬指标，fail |
| comprehension 可理解 | 图注是否解释图、alt 是否可用、段落是否过长、图文节奏是否失衡 | 多为 warn |

硬指标失败必须修。内容类问题记 warn，不阻断生成，理由是模型补的图注与真实信息量
只能人工判断，机器只做下限检查。

## 生成要求

### 结构自包含

页面必须能离线双击打开。不允许 `<script src>`、`<link rel=stylesheet>`、`@import`，
也不允许 `src` 或 `href` 指向 `http://`、`https://`。图片走相对路径或 data URI。
页面必须自带 `<style>`，没有样式表时无法检查版式，直接判 fail。

### 字号与行长

正文 17.5px，行高 1.9，标题列宽 `minmax(0, 680px)`。680 除以 17.5 约 38.9 字，
落在 14 到 42 字的中文舒适区内。

图注 13px、行高 1.6、`max-width: 40em`。40em 在 13px 下是 520px，正好 40 字，
比正文栏窄，避免图注出现比正文更长的行。

脚注 12px。正文与图注、脚注的下限分别对应 16px、12px、11px，上限 22px、18px、16px。
10px 级的页码、目录元信息属于界面文字，不受这几条约束。

### 行距与分段

正文行高 1.9，落在字高 50% 到 100% 的加距区间。窄屏 1.85，打印 1.7，大字号模式 1.8，
都在 1.5 到 2.0 之间。

分段只用一个标记。本规范选首行缩进 2em，正文段落 `margin: 0`。缩进与段距同时出现
会让读者以为每个段落都在开新节。

编辑层页面是唯一例外：有节拍和图版时，段落额外加 1.05em 下距，和 2em 缩进同时存在。
这样图版前后的段落边界更清楚，节拍标题仍然承担「节」的层级。`check_html` 的分段检查
只读基础 `.src` 规则，编辑层例外由 `G9` 和人工验收负责，详见 `阅读版编辑层.md`。

### 颜色与对比度

两套主题各有一组固定配色对，全部按 WCAG 相对亮度公式算，要求不低于 4.5:1。
校验的两套值如下，括号内是实测比值。

| 主题 | ink/paper | ink-3/paper | ink-3/paper-2 | ink-2/paper-2 | ochre/paper-2 | ink/note-bg |
|---|---|---|---|---|---|---|
| 日间 | 15.59 | 5.04 | 4.57 | 6.04 | 4.97 | 15.79 |
| 夜间 | 13.97 | 5.49 | 4.87 | 6.46 | 5.39 | 13.50 |

另查 ink-2/paper、pine/note-bg、ink-2/note-bg、ink-3/note-bg、pine/paper-2、paper/ink 六对。
日间 `--ink-3` 定为 `#69665C`，夜间定为 `#4F5C63`。旧值 `#8A877C` 在 `#F3F0E7` 上只有
3.16:1，是不够的。`--ochre` 日间 `#7C5A2E`，夜间 `#6B4E28`。

### 图与图注

标注 JSON 的块条目可以带可选 `caption`。有 caption 时 figure 标 `data-caption-src="model"`，
图注用模型写的那句。没有就回退块的 alt 文本，标 `data-caption-src="alt"` 并记 warn。

模型写图注的要求写在 `model.py` 的系统提示里，一句话说清这张图说明什么、为什么值得看，
不超过 40 字，不用「如图」「下图」开头，不复述 alt。

`img` 尽量带 `width` 与 `height`。生成侧从源图文件头读 PNG、GIF、JPEG、WebP、SVG
的像素尺寸，读不出就省略属性，不阻断渲染。带尺寸能在图片加载前占位，避免正文跳动。

### alt

alt 用来说明图的内容，不是文件名。`^[\w\-. ]+\.(png|jpe?g|gif|webp|svg)$` 一类的
纯文件名直接判 fail。装饰性图片用空 alt，但本书库里的 `figure.fig` 都属于内容图。

### 节奏

单段超过 420 汉字记 warn。正文汉字总数除以图数超过 1200 记 warn，无图不报。
相邻两个 `h2.sec` 到 `h6.sec` 之间超过 1600 汉字记 warn。

### 统计

每次检查附一条 info，给出段落数、图数、汉字数，以及按每分钟 500 字估算的阅读分钟数。

## 验收清单

ID 只在本文件内用于引用。级别为 fail 的项计入退出码，warn 只提示。

| ID | 检查项 | 阈值 | 级别 | 依据 | 机器检查键 |
|---|---|---|---|---|---|
| L1 | 正文字号 | 16 到 22px | fail | USWDS 正文不小于 16px | `font-size` |
| L2 | 图注字号 | 12 到 18px | fail | 图表注可读下限 | `font-size` |
| L3 | 脚注字号 | 11 到 16px | fail | 同上，脚注更小但仍可读 | `font-size` |
| L4 | 对比度 | 十二对固定配色均不小于 4.5:1 | fail | WCAG 1.4.3 | `contrast` |
| L5 | 正文行长 | 14 到 42 汉字 | fail | WCAG 1.4.8 与 clreq | `measure` |
| L6 | 图注行长 | 不超过 40 字 | fail | clreq 中文行长上限 | `measure` |
| L7 | 正文行高 | 1.5 到 2.0 倍 | fail | clreq 行距 50% 到 100% | `line-height` |
| L8 | 分段标记 | 缩进与段距不能同时存在 | fail | clreq 段落规范 | `paragraph-marker` |
| L9 | 外部资源 | 0 处 | fail | 离线自包含 | `external-resource` |
| C1 | 图注来源 | 有模型图注，或显式回退 alt | fail / warn | 本规范决策 1 | `figure-caption` |
| C2 | 图片尺寸 | `img` 有 width 与 height | fail | 防加载跳动 | `image-size` |
| C3 | alt 文本 | 不是文件名 | fail | W3C alt 决策树 | `alt` |
| C4 | 图注文字 | 不以图/表/如图/下图/上图/Figure/Table 开头，且不少于 8 字 | warn | PLOS 与 Urban 图表规范 | `caption-text` |
| R1 | 段落长度 | 不超过 420 汉字 | warn | 长文可读性经验值 | `paragraph-length` |
| R2 | 图文密度 | 每图至少 1200 汉字 | warn | 图表节奏 | `figure-density` |
| R3 | 标题间隔 | 相邻 h2 到 h6 之间不超过 1600 汉字 | warn | 章节节奏 | `heading-gap` |
| S1 | 统计 | 段落、图、字数、分钟 | info | 无 | `stats` |

## 三处执行入口

1. `src/bookcorpus/reading/quality.py` 的 `check_html(html_text)` 是唯一标准。纯函数，
   不读文件、不联网、不写盘，返回按固定顺序排列的 `{level, key, detail}` 列表。
2. `src/bookcorpus/reading/service.py` 的 `audit_pack` 在开头调用一次，把结果折成两个 gate。
   G5「版式」收 `contrast`、`font-size`、`measure`、`line-height`、`paragraph-marker`、
   `external-resource`、`paragraph-length`、`heading-gap`，G7「图文」收 `figure-caption`、
   `alt`、`image-size`、`caption-text`、`figure-density`。
3. `.agents/skills/chapter-html-versions/scripts/verify_lite.py` 把仓库 `src` 加进
   `sys.path` 后导入同一模块，输出 `FAIL [key] detail` 与 `WARN [key] detail`，
   fail 计入退出码。导入失败时打一行 NOTE 继续跑原有检查。

## 已知取舍

- 屏幕优先，印刷次要。打印样式只做到不掉字、不溢出，不追求版面美学。
- 缩进与段距二选一，选缩进。中文排版传统如此，也能让段距把「节」与「段」区分开。
- 图注短于正文栏。40em 在 13px 下是 520px，比 680px 的正文栏窄，视觉上图注退一层。
- 界面文字不受正文阈值约束。页码、目录元信息、按钮上的 10px 到 11px 字是信息密度需要，
  不属于连续阅读单位。
- 图注质量只能人工兜底。机器只查回退标记、长度、开头词与重复 alt。

## 来源

- W3C clreq 中文排版需求 https://www.w3.org/TR/clreq/
- WCAG 2.2 1.4.8 Visual Presentation https://www.w3.org/WAI/WCAG22/Understanding/visual-presentation.html
- WCAG 2.2 1.4.3 Contrast (Minimum) https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
- WCAG 2.2 1.4.12 Text Spacing https://www.w3.org/WAI/WCAG22/Understanding/text-spacing.html
- W3C Images Tutorial alt 决策树 https://www.w3.org/WAI/tutorials/images/decision-tree/
- USWDS Typography https://designsystem.digital.gov/components/typography/
- PLOS Ten Simple Rules for Better Figures https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1003833
- Urban Institute Data Visualization Style Guide https://urbaninstitute.github.io/graphics-styleguide/
