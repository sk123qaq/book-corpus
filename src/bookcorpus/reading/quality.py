"""阅读版 HTML 的版式与图文检查。

纯函数：不读文件、不联网、不写盘。同一份判据被 ``service.audit_pack``
的 G5/G7、``verify_lite.py`` 与测试复用。阈值与
``docs/design/阅读版HTML规范.md`` 保持一致。
"""

import re
from html.parser import HTMLParser

LIMITS = {
    "body_px": (16.0, 22.0),
    "caption_px": (12.0, 18.0),
    "footnote_px": (11.0, 16.0),
    "cjk_measure": (14.0, 42.0),
    "caption_measure": (14.0, 40.0),
    "line_height": (1.5, 2.0),
    "contrast": 4.5,
    "chars_per_figure": 1200,
    "heading_gap_chars": 1600,
}

HAN_RE = re.compile(r"[\u4e00-\u9fff]")
ALT_FILENAME_RE = re.compile(r"^[\w\-. ]+\.(png|jpe?g|gif|webp|svg)$", re.I)
CONTRAST_PAIRS = (
    ("ink", "paper"),
    ("ink-2", "paper"),
    ("ink-3", "paper"),
    ("ink-3", "paper-2"),
    ("ink-2", "paper-2"),
    ("ochre", "paper-2"),
    ("ink", "note-bg"),
    ("ink-2", "note-bg"),
    ("ink-3", "note-bg"),
    ("pine", "note-bg"),
    ("pine", "paper-2"),
    ("paper", "ink"),
)
CAPTION_PREFIXES = ("图", "表", "如图", "下图", "上图", "Figure", "Table")
EXTERNAL_PATTERNS = (
    (re.compile(r"<script[^>]+src\s*=", re.I), "<script src>"),
    (re.compile(r"<link[^>]+rel\s*=\s*[\"']?stylesheet", re.I), "<link rel=stylesheet>"),
    (re.compile(r"@import", re.I), "@import"),
    (re.compile(r"(?<![-\w])src\s*=\s*[\"']https?://", re.I), "远程 src"),
)


def han_count(text):
    return len(HAN_RE.findall(text or ""))


def _style_blocks(html_text):
    return " ".join(re.findall(r"(?is)<style[^>]*>(.*?)</style>", html_text or ""))


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _top_level_rules(css):
    """只收顶层规则；@media/@supports 整块跳过。

    正则匹配 ``选择器{...}`` 会在单行多规则的媒体查询里错配括号，所以按
    花括号深度扫描。
    """
    css = _strip_comments(css or "")
    rules = []
    pos = 0
    size = len(css)
    while pos < size:
        open_at = css.find("{", pos)
        if open_at < 0:
            break
        selector = css[pos:open_at].strip()
        depth = 1
        cursor = open_at + 1
        while cursor < size and depth:
            char = css[cursor]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        if selector and not selector.startswith("@"):
            rules.append((selector, css[open_at + 1 : cursor - 1]))
        pos = cursor
    return rules


def _declarations(rules, selector):
    """同名选择器的声明合并，后者覆盖前者（CSS 级联顺序）。"""
    found = False
    out = {}
    for sel, body in rules:
        if sel != selector:
            continue
        found = True
        for part in body.split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            out[key.strip().lower()] = value.strip()
    return out if found else None


def _variables(rules, selector):
    decls = _declarations(rules, selector) or {}
    return {key: value for key, value in decls.items() if key.startswith("--")}


def _rgb(value):
    text = (value or "").strip()
    if not text.startswith("#"):
        return None
    hex_part = text[1:]
    if len(hex_part) == 3:
        hex_part = "".join(ch * 2 for ch in hex_part)
    if len(hex_part) != 6:
        return None
    try:
        return tuple(int(hex_part[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _luminance(value):
    rgb = _rgb(value)
    if rgb is None:
        return None

    def channel(raw):
        scaled = raw / 255.0
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(part) for part in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground, background):
    """WCAG 相对亮度对比度。任一颜色解析不了时返回 ``None``。"""
    front = _luminance(foreground)
    back = _luminance(background)
    if front is None or back is None:
        return None
    high, low = max(front, back), min(front, back)
    return (high + 0.05) / (low + 0.05)


def _px(value):
    match = re.fullmatch(r"\s*(-?[\d.]+)\s*px\s*", value or "")
    return float(match.group(1)) if match else None


def _em(value):
    match = re.fullmatch(r"\s*(-?[\d.]+)\s*em\s*", value or "")
    return float(match.group(1)) if match else None


def _line_height(value, font_px):
    if value is None:
        return None
    text = value.strip()
    if text.endswith("px") and font_px:
        px = _px(text)
        return px / font_px if px is not None else None
    try:
        return float(text)
    except ValueError:
        return None


def _column_px(value):
    """grid-template-columns 第一列的像素宽。"""
    text = value or ""
    flex = re.search(r"minmax\(\s*[\d.]+[a-z%]*\s*,\s*([\d.]+)px\s*\)", text, re.I)
    if flex:
        return float(flex.group(1))
    first = re.match(r"\s*([\d.]+)px", text)
    return float(first.group(1)) if first else None


class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.figures = []
        self.paragraphs = []
        self.events = []
        self._figure = None
        self._figure_depth = 0
        self._caption = False
        self._paragraph = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if tag == "figure":
            self._figure_depth += 1
            if self._figure is None and "fig" in classes:
                self._figure = {
                    "caption_src": attributes.get("data-caption-src"),
                    "img": None,
                    "caption": "",
                }
        elif tag == "img":
            if self._figure is not None and self._figure["img"] is None:
                self._figure["img"] = attributes
            elif self._figure is None:
                self.figures.append({"caption_src": None, "img": attributes, "caption": ""})
        elif tag == "figcaption" and self._figure is not None:
            self._caption = True
        elif tag in ("p", "blockquote") and "src" in classes:
            self._paragraph = []
        elif tag in ("h2", "h3", "h4", "h5", "h6") and "sec" in classes:
            self.events.append(["heading"])

    def handle_endtag(self, tag):
        if tag == "figure" and self._figure_depth:
            self._figure_depth -= 1
            if self._figure is not None:
                self.figures.append(self._figure)
                self._figure = None
                self._caption = False
        elif tag == "figcaption":
            self._caption = False
        elif tag in ("p", "blockquote") and self._paragraph is not None:
            text = "".join(self._paragraph)
            self.paragraphs.append(text)
            self.events.append(["paragraph", han_count(text)])
            self._paragraph = None

    def handle_data(self, data):
        if self._caption and self._figure is not None:
            self._figure["caption"] += data
        if self._paragraph is not None:
            self._paragraph.append(data)


def _theme_vars(rules):
    base = _variables(rules, ":root")
    night = dict(base)
    night.update(_variables(rules, "body.night"))
    return (("warm", base), ("cold", night))


def _contrast_findings(rules):
    out = []
    limit = LIMITS["contrast"]
    for theme, palette in _theme_vars(rules):
        for foreground, background in CONTRAST_PAIRS:
            ratio = contrast_ratio(palette.get("--" + foreground), palette.get("--" + background))
            if ratio is None:
                continue
            if ratio < limit:
                out.append(
                    {
                        "level": "fail",
                        "key": "contrast",
                        "detail": "%s 对 %s %.2f:1 < %.1f:1（%s）" % (foreground, background, ratio, limit, theme),
                    }
                )
    return out


def _font_findings(rules, selector, limit_key, label):
    decls = _declarations(rules, selector)
    if not decls:
        return []
    size = _px(decls.get("font-size"))
    if size is None:
        return []
    low, high = LIMITS[limit_key]
    if low - 0.01 <= size <= high + 0.01:
        return []
    return [
        {
            "level": "fail",
            "key": "font-size",
            "detail": "%s 字号 %.1fpx，应在 %.0f–%.0fpx" % (label, size, low, high),
        }
    ]


def _measure_findings(rules, has_figures=False):
    out = []
    low, high = LIMITS["cjk_measure"]
    source = _declarations(rules, ".src") or {}
    body_px = _px(source.get("font-size"))
    columns = _declarations(rules, ".flow-row") or {}
    column = _column_px(columns.get("grid-template-columns"))
    if body_px and column:
        chars = column / body_px
        if chars < low - 0.01 or chars > high + 0.01:
            out.append(
                {
                    "level": "fail",
                    "key": "measure",
                    "detail": "正文 %.0fpx 列宽装 %.1f 字，应在 %.0f–%.0f 字" % (column, chars, low, high),
                }
            )
    caption = _declarations(rules, ".fig figcaption") or {}
    if not caption and has_figures:
        out.append(
            {
                "level": "warn",
                "key": "measure",
                "detail": "未找到 .fig figcaption 规则，图注行长未检查",
            }
        )
        return out
    caption_px = _px(caption.get("font-size"))
    low, high = LIMITS["caption_measure"]
    if caption_px:
        width = _em(caption.get("max-width"))
        caption_width = width * caption_px if width is not None else column
        if caption_width:
            chars = caption_width / caption_px
            if chars < low - 0.01 or chars > high + 0.01:
                out.append(
                    {
                        "level": "fail",
                        "key": "measure",
                        "detail": "图注 %.0fpx 宽装 %.1f 字，应在 %.0f–%.0f 字" % (caption_width, chars, low, high),
                    }
                )
    return out


def _line_height_findings(rules):
    source = _declarations(rules, ".src") or {}
    body_px = _px(source.get("font-size"))
    value = _line_height(source.get("line-height"), body_px)
    if value is None:
        return []
    low, high = LIMITS["line_height"]
    if low - 0.01 <= value <= high + 0.01:
        return []
    return [
        {
            "level": "fail",
            "key": "line-height",
            "detail": "正文行高 %.2f，应在 %.1f–%.1f" % (value, low, high),
        }
    ]


def _paragraph_marker_findings(rules):
    source = _declarations(rules, ".src")
    if not source:
        return []
    indent = _em(source.get("text-indent"))
    bottom = source.get("margin-bottom")
    if bottom is None:
        margin = (source.get("margin") or "").split()
        if len(margin) == 1:
            bottom = margin[0]
        elif len(margin) == 2:
            bottom = margin[1]
        elif len(margin) >= 3:
            bottom = margin[2]
    margin_em = _em(bottom)
    if indent is not None and indent >= 1.0 and margin_em is not None and margin_em >= 0.6:
        return [
            {
                "level": "fail",
                "key": "paragraph-marker",
                "detail": "正文同时有首行缩进 %.1fem 与段距 %.1fem，择一即可" % (indent, margin_em),
            }
        ]
    return []


def _external_findings(html_text):
    text = html_text or ""
    hits = [label for pattern, label in EXTERNAL_PATTERNS if pattern.search(text)]
    if not hits:
        return []
    return [
        {
            "level": "fail",
            "key": "external-resource",
            "detail": "外部资源 " + "、".join(hits),
        }
    ]


def _figure_findings(figures):
    out = []
    for index, figure in enumerate(figures, 1):
        image = figure.get("img") or {}
        alt = (image.get("alt") or "").strip()
        caption = " ".join(figure.get("caption", "").split())
        source = figure.get("caption_src")
        if source not in ("model", "alt"):
            out.append(
                {
                    "level": "warn",
                    "key": "figure-caption",
                    "detail": "第 %d 张图未标 data-caption-src" % index,
                }
            )
        elif source == "model" and alt and caption == alt:
            out.append(
                {
                    "level": "fail",
                    "key": "figure-caption",
                    "detail": "第 %d 张图的图注仍是 alt 原文" % index,
                }
            )
        elif source == "alt":
            out.append(
                {
                    "level": "warn",
                    "key": "figure-caption",
                    "detail": "第 %d 张图缺模型图注，回退 alt" % index,
                }
            )
        if not (image.get("width") and image.get("height")):
            out.append(
                {
                    "level": "fail",
                    "key": "image-size",
                    "detail": "第 %d 张图缺 width/height" % index,
                }
            )
        if alt and ALT_FILENAME_RE.match(alt):
            out.append(
                {
                    "level": "fail",
                    "key": "alt",
                    "detail": "第 %d 张图的 alt 是文件名 %s" % (index, alt),
                }
            )
        if caption and (caption.startswith(CAPTION_PREFIXES) or len(caption.replace(" ", "")) < 8):
            out.append(
                {
                    "level": "warn",
                    "key": "caption-text",
                    "detail": "第 %d 张图的图注像标签 %s" % (index, caption[:24]),
                }
            )
    return out


def _rhythm_findings(scan):
    out = []
    total = sum(han_count(text) for text in scan.paragraphs)
    figures = len(scan.figures)
    if figures:
        per_figure = total / figures
        if per_figure > LIMITS["chars_per_figure"]:
            out.append(
                {
                    "level": "warn",
                    "key": "figure-density",
                    "detail": "每 %.0f 字才有一张图，建议低于 %d" % (per_figure, LIMITS["chars_per_figure"]),
                }
            )
    gap_limit = LIMITS["heading_gap_chars"]
    run = 0
    for event in scan.events:
        if event[0] == "heading":
            if run > gap_limit:
                out.append(
                    {
                        "level": "warn",
                        "key": "heading-gap",
                        "detail": "标题之间 %d 字，超过 %d" % (run, gap_limit),
                    }
                )
            run = 0
        else:
            run += event[1]
    return out


def _stats_finding(scan):
    total = sum(han_count(text) for text in scan.paragraphs)
    return [
        {
            "level": "info",
            "key": "stats",
            "detail": "段落 %d · 图 %d · 汉字 %d · 约 %d 分钟"
            % (len(scan.paragraphs), len(scan.figures), total, max(1, round(total / 500))),
        }
    ]


def check_html(html_text):
    """检查一份阅读版 HTML，返回 ``[{level, key, detail}]``。

    没有 ``<style>`` 时无法核对版式，直接记 fail；缺选择器或颜色解析不了
    时跳过对应项，不抛异常。
    """
    text = html_text or ""
    findings = []
    rules = _top_level_rules(_style_blocks(text))
    scan = _Scan()
    scan.feed(text)
    scan.close()
    if not rules:
        findings.append({"level": "fail", "key": "font-size", "detail": "缺 <style>，无法检查版式"})
    else:
        findings.extend(_contrast_findings(rules))
        findings.extend(_font_findings(rules, ".src", "body_px", "正文"))
        findings.extend(_font_findings(rules, ".fig figcaption", "caption_px", "图注"))
        findings.extend(_font_findings(rules, ".fn", "footnote_px", "脚注"))
        findings.extend(_measure_findings(rules, bool(scan.figures)))
        findings.extend(_line_height_findings(rules))
        findings.extend(_paragraph_marker_findings(rules))
    findings.extend(_external_findings(text))
    findings.extend(_figure_findings(scan.figures))
    findings.extend(_rhythm_findings(scan))
    findings.extend(_stats_finding(scan))
    return findings
