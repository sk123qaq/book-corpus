#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Split an EPUB into one Markdown file per chapter, images included.

Only needs Python 3 + beautifulsoup4 (lxml optional, used when present).

    python3 ebook_to_md.py BOOK.epub [-o OUTDIR] [options]

Chapter boundaries and chapter titles come from the book's own table of
contents (EPUB3 nav doc, else NCX).  Images are copied into <OUTDIR>/images/
and referenced relatively so the Markdown renders standalone.

By default only body chapters are written: covers, copyright/CIP pages,
tables of contents, prefaces (推荐序/前言/自序…), part dividers, indexes and
other front/back matter are skipped, and README.md lists what was skipped and
why.  Pass --all-pages to keep everything, or --drop REGEX to remove more.
"""

from __future__ import annotations

import argparse
import html
import os
import posixpath
import re
import sys
import unicodedata
import zipfile
from collections import Counter, OrderedDict
from urllib.parse import unquote
from xml.etree import ElementTree as ET

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("beautifulsoup4 is required:  pip install beautifulsoup4")

try:
    from bs4 import XMLParsedAsHTMLWarning
    import warnings

    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except Exception:  # pragma: no cover
    pass

# --------------------------------------------------------------------------
# text helpers
# --------------------------------------------------------------------------

CJK = (
    "\u3000-\u303f"  # CJK punctuation
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"  # CJK ideographs
    "\uf900-\ufaff"
    "\uff00-\uffef"  # fullwidth forms
    "\U00020000-\U0002ffff"
)
_CJK_RE = re.compile(f"[{CJK}]")

#: characters that end a sentence -- a heading almost never ends with one
TERMINAL_PUNCT = "。．.，,、；;：:！!？?…—～~）)】」』”\"'"

WS_RE = re.compile(r"\s+")


def normalize_text(s: str) -> str:
    """Collapse whitespace, dropping spaces that only separate CJK glyphs."""
    if not s:
        return ""
    s = s.replace("\u00a0", " ").replace("\u3000", " ")
    s = WS_RE.sub(" ", s)
    # a space between two CJK characters is an artefact of pretty-printed HTML
    out = []
    for i, ch in enumerate(s):
        if ch == " ":
            prev = out[-1] if out else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if prev and nxt and _CJK_RE.match(prev) and _CJK_RE.match(nxt):
                continue
        out.append(ch)
    return "".join(out).strip()


def sanitize_filename(name: str, fallback: str = "untitled", maxlen: int = 50) -> str:
    """Make a title safe for a file name on Linux/macOS/Windows."""
    name = unicodedata.normalize("NFC", name)
    name = normalize_text(name)
    name = re.sub(r'[/\\:*?"<>|\r\n\t]', "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name)
    name = re.sub(r"\s*_\s*", "_", name)
    name = name.strip(" ._-")
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(" ._-")
    return name or fallback


# --------------------------------------------------------------------------
# CSS: only what heading recovery needs (font-size / font-weight)
# --------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_DECL_RE = re.compile(r"([-\w]+)\s*:\s*([^;]+)")
_LENGTH_RE = re.compile(r"^(-?\d*\.?\d+)\s*([a-z%]*)")

#: CSS declarations we keep -- enough to spot headings and paragraph shape
PROP_KEYS = (
    "font-size",
    "font-weight",
    "text-align",
    "text-indent",
    "margin",
    "margin-top",
)

_KEYWORD_SIZE = {
    "xx-small": 9.0,
    "x-small": 10.0,
    "small": 13.0,
    "medium": 16.0,
    "large": 18.0,
    "x-large": 24.0,
    "xx-large": 32.0,
}


def length_px(value: str, base: float = 16.0):
    """Computed length in px for the layout lengths we care about, or None."""
    if not value:
        return None
    v = value.strip().lower().removesuffix("!important").strip()
    m = _LENGTH_RE.match(v)
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2)
    if unit in ("", "px"):
        return num
    if unit in ("em", "rem"):
        return num * base
    if unit == "%":
        return num * base / 100.0
    if unit == "pt":
        return num * 4.0 / 3.0
    if unit == "pc":
        return num * 16.0
    if unit == "in":
        return num * 96.0
    if unit == "cm":
        return num * 37.8
    if unit == "mm":
        return num * 3.78
    return None


def scale_font_size(value: str, parent: float):
    """Return the computed size for a font-size declaration, or None."""
    v = value.strip().lower().rstrip("!important").strip()
    try:
        if v.endswith("rem") or v.endswith("em"):
            return parent * float(v[:-2].strip() or 1)
        if v.endswith("%"):
            return parent * float(v[:-1].strip()) / 100.0
        if v.endswith("px"):
            return float(v[:-2].strip())
        if v.endswith("pt"):
            return float(v[:-2].strip()) * 4.0 / 3.0
        if v.endswith("pc"):
            return float(v[:-2].strip()) * 16.0
        if v.endswith("cm"):
            return float(v[:-2].strip()) * 37.8
        if v.endswith("mm"):
            return float(v[:-2].strip()) * 3.78
        if v.endswith("in"):
            return float(v[:-2].strip()) * 96.0
        if v == "smaller":
            return parent / 1.2
        if v == "larger":
            return parent * 1.2
        if v in _KEYWORD_SIZE:
            return _KEYWORD_SIZE[v]
        if v.endswith("vw"):
            return parent * float(v[:-2].strip()) / 100.0
    except ValueError:
        return None
    return None


class StyleSheet:
    """class/元素的 font-size + font-weight 速查表。"""

    def __init__(self, text: str = ""):
        self.class_props: dict[str, dict] = {}
        self.elem_props: dict[str, dict] = {}
        self.id_props: dict[str, dict] = {}
        if text:
            self.add(text)

    def add(self, css: str) -> None:
        css = _COMMENT_RE.sub(" ", css)
        for selector, body in _RULE_RE.findall(css):
            props = {}
            for prop, val in _DECL_RE.findall(body):
                prop = prop.strip().lower()
                if prop in PROP_KEYS:
                    props[prop] = val.strip()
            if not props:
                continue
            for sel in selector.split(","):
                sel = sel.strip()
                if not sel or any(c in sel for c in " >+[:"):
                    sel = sel.split()[-1] if sel else ""
                if sel.startswith("."):
                    self.class_props.setdefault(sel[1:], {}).update(props)
                elif sel.startswith("#"):
                    self.id_props.setdefault(sel[1:], {}).update(props)
                elif re.fullmatch(r"[a-zA-Z][\w-]*", sel or ""):
                    self.elem_props.setdefault(sel.lower(), {}).update(props)

    def props_for(self, tag: str, classes, elem_id, inline_style) -> dict:
        props: dict = {}
        props.update(self.elem_props.get(tag, {}))
        for cls in classes or []:
            props.update(self.class_props.get(cls, {}))
        if elem_id:
            props.update(self.id_props.get(elem_id, {}))
        if inline_style:
            for prop, val in _DECL_RE.findall(inline_style):
                prop = prop.strip().lower()
                if prop in PROP_KEYS:
                    props[prop] = val.strip()
        return props

    def merge(self, other: "StyleSheet") -> None:
        for target, src in (
            (self.class_props, other.class_props),
            (self.elem_props, other.elem_props),
            (self.id_props, other.id_props),
        ):
            for key, props in src.items():
                target.setdefault(key, {}).update(props)


def is_bold(value: str) -> bool:
    v = (value or "").strip().lower()
    if v in ("bold", "bolder"):
        return True
    if v.endswith("!important"):
        v = v[: -len("!important")].strip()
    try:
        return int(v) >= 600
    except ValueError:
        return False


# --------------------------------------------------------------------------
# EPUB container
# --------------------------------------------------------------------------


class EpubError(Exception):
    pass


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _tag(elem) -> str:
    return _strip_ns(elem.tag).lower()


def _xml_root(data: bytes):
    """Parse XML bytes, repairing defects common in converted EPUBs.

    Calibre/Mobi-to-EPUB pipelines sometimes leave comments that contain "--"
    or that are never closed, plus stray "&" and control characters;
    ElementTree rejects all of those outright.  The repair only runs when the
    strict parse fails, so well-formed files are unaffected.
    """
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        pass
    text = data.decode("utf-8", "replace")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = text.replace("<!--", "").replace("-->", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"&(?!(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z][\w.\-]*);)", "&amp;", text)
    return ET.fromstring(text.encode("utf-8"))


class TocEntry:
    __slots__ = ("label", "href", "path", "fragment", "level")

    def __init__(self, label, href, path, fragment, level):
        self.label = label
        self.href = href
        self.path = path
        self.fragment = fragment
        self.level = level

    def __repr__(self):
        return f"TocEntry({self.label!r}, {self.path!r}, #{self.fragment}, L{self.level})"


class Epub:
    def __init__(self, path: str):
        self.path = path
        self.zip = zipfile.ZipFile(path)
        names = set(self.zip.namelist())
        opf_path = self._find_opf()
        self.opf_path = opf_path
        self.opf_dir = posixpath.dirname(opf_path)
        self.metadata: dict[str, str] = {}
        self.manifest: dict[str, dict] = {}
        self.spine: list[str] = []  # manifest ids, in reading order
        self._css_cache: dict[str, str] = {}
        self._global_css: StyleSheet | None = None
        self._soup_cache: dict[str, BeautifulSoup] = {}
        if opf_path not in names:
            raise EpubError(f"{path}: OPF not found in archive ({opf_path})")
        self._parse_opf()

    # -- container ---------------------------------------------------------
    def _find_opf(self) -> str:
        try:
            data = self.zip.read("META-INF/container.xml")
        except KeyError:
            raise EpubError(f"{self.path}: not an EPUB (META-INF/container.xml missing)")
        root = _xml_root(data)
        for elem in root.iter():
            if _tag(elem) == "rootfile" and elem.get("full-path"):
                return elem.get("full-path")
        raise EpubError(f"{self.path}: container.xml has no rootfile")

    def _parse_opf(self) -> None:
        root = _xml_root(self.zip.read(self.opf_path))
        for elem in root.iter():
            t = _tag(elem)
            if t == "title" and "title" not in self.metadata:
                self.metadata["title"] = (elem.text or "").strip()
            elif t == "creator" and "creator" not in self.metadata:
                self.metadata["creator"] = (elem.text or "").strip()
            elif t == "language" and "language" not in self.metadata:
                self.metadata["language"] = (elem.text or "").strip()
            elif t == "publisher" and "publisher" not in self.metadata:
                self.metadata["publisher"] = (elem.text or "").strip()
            elif t == "date" and "date" not in self.metadata:
                self.metadata["date"] = (elem.text or "").strip()
            elif t == "item" and elem.get("id"):
                self.manifest[elem.get("id")] = {
                    "href": elem.get("href") or "",
                    "type": elem.get("media-type") or "",
                    "props": elem.get("properties") or "",
                }
        self.spine = [
            e.get("idref")
            for e in root.iter()
            if _tag(e) == "itemref" and e.get("idref")
        ]
        self.toc_id = None
        for e in root.iter():
            if _tag(e) == "spine" and e.get("toc"):
                self.toc_id = e.get("toc")

    # -- archive access ----------------------------------------------------
    def exists(self, rel: str) -> bool:
        return rel in set(self.zip.namelist())

    def read_text(self, rel: str) -> str:
        raw = self.zip.read(rel)
        for enc in ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def resolve(self, base_file: str, href: str) -> str:
        """Resolve `href` relative to the directory of `base_file`."""
        href = unquote(html.unescape(href.split("#")[0]))
        href = href.replace("\\", "/")
        if not href:
            return base_file
        return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), href))

    def soup(self, rel: str) -> BeautifulSoup:
        if rel not in self._soup_cache:
            parser = "lxml"
            try:
                import lxml  # noqa: F401
            except ImportError:
                parser = "html.parser"
            self._soup_cache[rel] = BeautifulSoup(self.read_text(rel), parser)
        return self._soup_cache[rel]

    # -- documents ---------------------------------------------------------
    def content_docs(self) -> list[str]:
        """Spine documents that are HTML/XHTML, in reading order."""
        docs = []
        for idref in self.spine:
            item = self.manifest.get(idref)
            if not item:
                continue
            href = item["href"]
            if "html" not in item["type"] and not re.search(r"\.x?html?$", href, re.I):
                continue
            rel = self.resolve(self.opf_path, href)
            if self.exists(rel):
                docs.append(rel)
        return docs

    # -- table of contents -------------------------------------------------
    def toc(self) -> list[TocEntry]:
        entries = self._toc_from_nav()
        if not entries:
            entries = self._toc_from_ncx()
        return entries

    def _nav_path(self) -> str | None:
        for item in self.manifest.values():
            if "nav" in item["props"].split():
                return self.resolve(self.opf_path, item["href"])
        return None

    def _toc_from_nav(self) -> list[TocEntry]:
        nav = self._nav_path()
        if not nav or not self.exists(nav):
            return []
        soup = self.soup(nav)
        navtag = None
        for cand in soup.find_all("nav"):
            if "toc" in (cand.get("epub:type") or cand.get("type") or "").split():
                navtag = cand
                break
        navtag = navtag or soup.find("nav")
        if navtag is None:
            return []
        entries: list[TocEntry] = []

        def walk(ol, level):
            for li in ol.find_all("li", recursive=False):
                a = li.find("a") or li.find("span")
                if a is not None:
                    target = a.get("href")
                    if target:
                        path, _, frag = target.partition("#")
                        entries.append(
                            TocEntry(
                                normalize_text(a.get_text(" ", strip=True)),
                                target,
                                self.resolve(nav, path),
                                frag or None,
                                level,
                            )
                        )
                for sub in li.find_all("ol", recursive=False):
                    walk(sub, level + 1)

        for ol in navtag.find_all("ol"):
            walk(ol, 1)
            break
        return [e for e in entries if e.path]

    def _toc_from_ncx(self) -> list[TocEntry]:
        ncx = None
        if self.toc_id and self.toc_id in self.manifest:
            ncx = self.resolve(self.opf_path, self.manifest[self.toc_id]["href"])
        if not ncx:
            for item in self.manifest.values():
                if item["type"] == "application/x-dtbncx+xml" or item["href"].endswith(".ncx"):
                    ncx = self.resolve(self.opf_path, item["href"])
                    break
        if not ncx or not self.exists(ncx):
            return []
        root = _xml_root(self.zip.read(ncx))
        entries: list[TocEntry] = []

        def walk(node, level):
            for nav in node:
                if _tag(nav) != "navpoint":
                    continue
                label = ""
                for sub in nav.iter():
                    if _tag(sub) == "text":
                        label = (sub.text or "").strip()
                        break
                href = ""
                for sub in nav.iter():
                    if _tag(sub) == "content" and sub.get("src"):
                        href = sub.get("src")
                        break
                if href:
                    path, _, frag = href.partition("#")
                    entries.append(
                        TocEntry(
                            normalize_text(label),
                            href,
                            self.resolve(ncx, path),
                            frag or None,
                            level,
                        )
                    )
                walk(nav, level + 1)

        navmap = next((e for e in root.iter() if _tag(e) == "navmap"), None)
        walk(navmap if navmap is not None else root, 1)
        return entries

    # -- stylesheets -------------------------------------------------------
    def _load_css(self, rel: str) -> str:
        if rel not in self._css_cache:
            self._css_cache[rel] = self.read_text(rel) if self.exists(rel) else ""
        return self._css_cache[rel]

    def style_for(self, doc: str, global_sheet: "StyleSheet") -> "StyleSheet":
        sheet = StyleSheet()
        soup = self.soup(doc)
        for link in soup.find_all("link"):
            href = link.get("href")
            rel_attr = " ".join(link.get("rel") or []).lower()
            if href and ("stylesheet" in rel_attr or href.endswith(".css")):
                sheet.add(self._load_css(self.resolve(doc, href)))
        for style in soup.find_all("style"):
            sheet.add(style.get_text() or "")
        sheet.merge(global_sheet)
        return sheet

    def global_styles(self) -> "StyleSheet":
        if self._global_css is None:
            sheet = StyleSheet()
            for item in self.manifest.values():
                if item["type"] == "text/css" or item["href"].endswith(".css"):
                    sheet.add(self._load_css(self.resolve(self.opf_path, item["href"])))
            self._global_css = sheet
        return self._global_css


# --------------------------------------------------------------------------
# HTML -> Markdown
# --------------------------------------------------------------------------

SKIP_TAGS = {"script", "style", "head", "title", "meta", "link", "svg", "noscript"}
SKIP_CLASS_RE = re.compile(r"pagebreak|mbp_pagebreak|calibre\d*\b(?=.*pagebreak)", re.I)
BULLET_RE = re.compile(r"^\s*([●•·▪◦‣∙*]|[-–—](?=\s))\s*")
BLOCK_TAGS = [
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "blockquote", "li",
    "table", "pre", "hr", "img", "section", "article", "figure", "figcaption",
]
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


class Block:
    """A converted Markdown block, before level normalisation."""

    __slots__ = (
        "kind",
        "text",
        "level",
        "size",
        "bold",
        "source",
        "align",
        "indent",
        "mtop",
        "group",
        "has_link",
        "recovered",
    )

    def __init__(self, kind, text, level=0, size=0.0, bold=False, source=None):
        self.kind = kind  # heading | para | list | quote | table | image | hr | title
        self.text = text
        self.level = level
        self.size = size
        self.bold = bold
        self.source = source
        self.align = ""  # CSS text-align of the block
        self.indent = None  # CSS text-indent in px (None: not declared)
        self.mtop = None  # CSS margin-top in px
        self.group = ""  # style signature shared by similarly styled blocks
        self.has_link = False
        self.recovered = False  # level set by heading recovery, not by the source

    def __repr__(self):
        return f"<{self.kind} L{self.level} {self.size:.1f} {self.text[:30]!r}>"


class Converter:
    def __init__(self, epub: Epub, image_sink, keep_images=True):
        self.epub = epub
        self.image_sink = image_sink  # callable(doc_rel, src) -> markdown path or None
        self.keep_images = keep_images
        self.warnings: list[str] = []
        self.link_resolver = None  # callable(doc_rel, href) -> markdown target or None
        self.baseline = 16.0

    # -- inline ------------------------------------------------------------
    def _inline(self, node, sheet: StyleSheet, size: float, bold: bool, doc: str) -> str:
        if isinstance(node, str):
            return normalize_text(str(node))
        name = node.name
        if name is None:
            return ""
        name = name.lower()
        if name in SKIP_TAGS:
            return ""
        classes = node.get("class") or []
        if name == "div" and SKIP_CLASS_RE.search(" ".join(classes)):
            return ""
        props = sheet.props_for(name, classes, node.get("id"), node.get("style"))
        if "font-size" in props:
            scaled = scale_font_size(props["font-size"], size)
            if scaled:
                size = scaled
        if "font-weight" in props and is_bold(props["font-weight"]):
            bold = True
        if name in ("b", "strong"):
            bold = True

        if name == "br":
            return " "
        if name == "img":
            return self._image(node, doc)
        if name == "hr":
            return "\n\n---\n\n"

        inner = "".join(self._inline(c, sheet, size, bold, doc) for c in node.children)
        inner = normalize_text(inner)

        if not inner:
            return ""
        if name == "a":
            href = node.get("href") or ""
            if href.startswith(("http://", "https://", "mailto:")):
                return f"[{inner}]({href})"
            target = self.link_resolver(doc, href) if self.link_resolver else None
            return f"[{inner}]({target})" if target else inner
        if name in ("em", "i", "cite", "var"):
            return f"*{inner}*"
        if name in ("b", "strong"):
            return f"**{inner}**"
        if name in ("code", "tt", "kbd", "samp"):
            return f"`{inner}`"
        if name in ("sup",):
            return f"^{inner}^"
        if name in ("sub",):
            return f"~{inner}~"
        return inner

    def _image(self, node, doc: str) -> str:
        if not self.keep_images:
            return ""
        src = node.get("src") or node.get("data-src") or ""
        if not src:
            for key in ("xlink:href", "{http://www.w3.org/1999/xlink}href"):
                if node.get(key):
                    src = node.get(key)
                    break
        if not src or src.startswith(("http://", "https://", "data:")):
            return ""
        path = self.image_sink(doc, src)
        if not path:
            return ""
        alt = normalize_text(node.get("alt") or "")
        return f"\n\n![{self._clean_alt(node, doc, alt)}]({path})\n\n"

    def _clean_alt(self, node, doc: str, alt: str) -> str:
        """Drop alt text that only repeats visible text.

        Some books put every footnote's prose in the alt of the noteref icon
        (芯片战争: 681 icons, 96k chars), so keeping it writes each note twice
        and audits at 133%.  A short caption is still useful; a long alt that
        already appears in the page's own text is not.
        """
        if not alt:
            return ""
        link = node.find_parent("a")
        if link is not None:
            role = (
                f"{link.get('epub:type') or link.get('{http://www.idpf.org/2007/ops}type') or ''} "
                f"{link.get('role') or ''}"
            ).lower()
            if "noteref" in role:
                return ""
        classes = " ".join(node.get("class") or []).lower()
        if "footnote" in classes or "noteref" in classes:
            return ""
        if len(alt) > 200:
            return ""
        if len(alt) >= 40 and alt in self._doc_text(doc):
            return ""
        return alt

    def _doc_text(self, doc: str) -> str:
        cache = getattr(self, "_doc_text_cache", None)
        if cache is None:
            cache = self._doc_text_cache = {}
        if doc not in cache:
            soup = self.epub.soup(doc)
            body = soup.find("body") if soup else None
            cache[doc] = normalize_text(body.get_text(" ", strip=True)) if body else ""
        return cache[doc]

    # -- block walking -----------------------------------------------------
    def _text_stats(self, elem, sheet: StyleSheet, size: float, bold: bool):
        """Character-weighted dominant font size / boldness of a block."""
        weights: Counter = Counter()
        bold_at: dict[float, bool] = {}

        def walk(node, size, bold):
            if isinstance(node, str):
                text = normalize_text(str(node))
                if text:
                    weights[round(size, 2)] += len(text)
                    bold_at[round(size, 2)] = bold_at.get(round(size, 2), False) or bold
                return
            if node.name is None or node.name.lower() in SKIP_TAGS:
                return
            name = node.name.lower()
            props = sheet.props_for(
                name, node.get("class") or [], node.get("id"), node.get("style")
            )
            if "font-size" in props:
                scaled = scale_font_size(props["font-size"], size)
                if scaled:
                    size = scaled
            if ("font-weight" in props and is_bold(props["font-weight"])) or name in (
                "b",
                "strong",
            ):
                bold = True
            for child in node.children:
                walk(child, size, bold)

        for child in elem.children:
            walk(child, size, bold)
        if not weights:
            return size, bold, 0
        dom = max(weights.items(), key=lambda kv: (kv[1], kv[0]))[0]
        return dom, bold_at.get(dom, False), sum(weights.values())

    def _is_heading_candidate(self, text: str, size: float, bold: bool) -> bool:
        if not text or len(text) > 60:
            return False
        if text[-1] in TERMINAL_PUNCT:
            return False
        if re.match(r"^(图|表|Figure|Table|Fig\.?)\s*[\d一二三四五六七八九十]", text):
            return False
        if size >= self.baseline * 1.15:
            return True
        return False

    def convert(self, doc: str, sheet: StyleSheet, root=None, body_size: float = 16.0):
        """HTML document -> list[Block]."""
        soup = self.epub.soup(doc)
        body = root if root is not None else soup.find("body")
        if body is None:
            return []
        blocks: list[Block] = []
        self._walk_blocks(body, sheet, body_size, False, doc, blocks)
        return blocks

    def _walk_blocks(self, elem, sheet, size, bold, doc, out: list, depth=0):
        if depth > 60:
            return
        for node in elem.children:
            if isinstance(node, str):
                text = normalize_text(str(node))
                if text:
                    out.append(Block("para", text, size=size, bold=bold))
                continue
            name = (node.name or "").lower()
            if not name or name in SKIP_TAGS:
                continue
            classes = node.get("class") or []
            cls = " ".join(classes)
            if "pagebreak" in cls.lower() or "mbp_pagebreak" in cls.lower():
                continue
            props = sheet.props_for(name, classes, node.get("id"), node.get("style"))
            nsize = size
            if "font-size" in props:
                scaled = scale_font_size(props["font-size"], size)
                if scaled:
                    nsize = scaled
            nbold = bold or (
                ("font-weight" in props and is_bold(props["font-weight"]))
                or name in ("b", "strong")
            )

            if name in HEADING_TAGS:
                text = self._block_text(node, sheet, nsize, nbold, doc)
                if text:
                    out.append(
                        Block(
                            "heading",
                            text,
                            level=int(name[1]),
                            size=nsize,
                            bold=True,
                            source=node,
                        )
                    )
                continue
            if name == "table":
                out.append(Block("table", self._table(node, sheet, nsize, nbold, doc), source=node))
                continue
            if name == "img":
                md = self._image(node, doc)
                if md.strip():
                    out.append(Block("image", md.strip(), source=node))
                continue
            if name == "hr":
                out.append(Block("hr", "---", source=node))
                continue
            if name in ("p", "figcaption", "pre", "blockquote", "li"):
                text = self._block_text(node, sheet, nsize, nbold, doc)
                if text:
                    dom, dbold, _ = self._text_stats(node, sheet, size, bold)
                    if name == "blockquote" or name == "li":
                        kind = "list" if BULLET_RE.match(text) else "quote"
                        if name == "li":
                            kind = "list"
                    elif name == "pre":
                        kind = "code"
                    else:
                        kind = "para"
                    block = Block(kind, text, size=dom, bold=dbold, source=node)
                    self._set_layout(block, name, classes, props, nsize)
                    out.append(block)
                else:
                    # a paragraph holding nothing but images
                    for sub in node.find_all("img"):
                        md = self._image(sub, doc)
                        if md.strip():
                            out.append(Block("image", md.strip(), source=sub))
                continue
            # generic containers: recurse
            self._walk_blocks(node, sheet, nsize, nbold, doc, out, depth + 1)

    @staticmethod
    def _set_layout(block, tag, classes, props, size) -> None:
        """Record how the block is laid out, for style-based heading recovery."""
        block.align = (props.get("text-align") or "").strip().lower()
        block.indent = length_px(props.get("text-indent"), size)
        margin = props.get("margin-top")
        if not margin and props.get("margin"):
            margin = props["margin"].split()[0]
        block.mtop = length_px(margin, size)
        if classes:
            block.group = " ".join(classes)
        else:
            block.group = f"<{tag} {block.align}>"
        block.has_link = block.source is not None and block.source.find("a") is not None

    def _block_text(self, node, sheet, size, bold, doc) -> str:
        parts = []
        for child in node.children:
            parts.append(self._inline(child, sheet, size, bold, doc))
        text = "".join(parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _table(self, node, sheet, size, bold, doc) -> str:
        rows: list[list[str]] = []
        header: list[str] | None = None
        for tr in node.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False) or tr.find_all(["td", "th"])
            values = []
            for cell in cells:
                txt = self._block_text(cell, sheet, size, bold, doc)
                span = int(cell.get("colspan") or 1)
                values.append(txt.replace("|", "\\|").replace("\n", " "))
                values.extend([""] * (span - 1))
            if not values:
                continue
            if header is None and tr.find("th") is not None:
                header = values
                continue
            rows.append(values)
        if header is None and rows:
            header, rows = rows[0], rows[1:]
        if header is None:
            return ""
        width = max([len(header)] + [len(r) for r in rows]) if rows else len(header)
        header = header + [""] * (width - len(header))
        lines = ["| " + " | ".join(header) + " |", "|" + " --- |" * width]
        for row in rows:
            row = row + [""] * (width - len(row))
            if not any(c.strip() for c in row):
                continue
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

class Unit:
    def __init__(self, index, title, doc, fragment=None, toc_level=1, untoced=False,
                 next_fragment=None):
        self.index = index  # position in the book's reading order
        self.number = index  # position among the files that are written
        self.title = title or f"章节 {index}"
        self.doc = doc
        self.fragment = fragment
        self.next_fragment = next_fragment
        self.toc_level = toc_level
        self.untoced = untoced
        self.derived = not untoced  # False: title is a placeholder, not from the book
        self.blocks: list[Block] = []
        self.filename = ""
        self.images: list[str] = []
        self.chars = 0
        self.empty = False  # nothing but the (dropped) title -> no file written
        self.excluded = False  # front/back matter: no file in body-only mode
        self.reason = ""  # why the unit produced no file
        self.written = True  # a file is written for this unit
        self.has_prose = False
        self.merge_children: list = []  # numbered sub-sections folded into this one
        self.continuations: list = []  # TOC-less spine docs owned by this unit


NEW_CHAPTER_HEAD_RE = re.compile(
    r"^\s*(?:第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节回讲]"
    r"|chapter\s+(?:\d+|[ivxlcdm]+)\b)",
    re.I,
)


def _title_key(text: str) -> str:
    return re.sub(
        r"[\s\u3000:：.、,，;；!！?？\-—–_（）()\[\]【】]+", "", (text or "").lower()
    )


def _looks_like_new_chapter(text: str, previous_title: str) -> bool:
    """A TOC-less doc that starts a new 第N章 is not a continuation."""
    if not text or not NEW_CHAPTER_HEAD_RE.match(text[:80]):
        return False
    head = _title_key(text[: max(80, len(previous_title) + 40)])
    prev = _title_key(previous_title)
    # a continuation often repeats the chapter title before its subtitle
    return not (head.startswith(prev) or prev.startswith(head))


def _doc_text(epub: Epub, doc: str) -> str:
    soup = epub.soup(doc)
    body = soup.find("body") if soup else None
    return normalize_text(body.get_text(" ", strip=True)) if body else ""


HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _first_block(epub: Epub, doc: str) -> tuple[str, str]:
    """First block-level text of a document and its tag, without converting."""
    soup = epub.soup(doc)
    body = soup.find("body") if soup else None
    if body is None:
        return "", ""
    for node in body.descendants:
        tag = getattr(node, "name", None)
        if not tag or tag.lower() not in (HEADING_TAGS | {"p", "div", "li", "blockquote", "td", "pre"}):
            continue
        if node.find(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "td"]):
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        if text:
            return text, tag.lower()
    return "", ""


def _heading_like(text: str, tag: str) -> bool:
    if tag in HEADING_TAGS:
        return True
    return bool(text) and len(text) <= 30 and text[-1] not in TERMINAL_PUNCT


def build_units(epub: Epub, include_untoced: bool) -> list[Unit]:
    """Turn the TOC into units, keeping each unit's whole reading span.

    A TOC anchor owns every spine document up to the next TOC anchor, not just
    the document the anchor sits in.  Calibre's split files put the chapter
    title on one page and the prose on the next two or three; treating each
    spine file as a unit used to turn one chapter into five tiny files
    (B1941: 29 files, 71.6%).  TOC-less docs that are empty, repeat the unit
    title, or carry real prose are therefore attached to the preceding unit.
    Small TOC-less pages stay separate so the title filter / --drop can still
    see junk (piracy stamps, part dividers).
    """
    docs = epub.content_docs()
    doc_order = {d: i for i, d in enumerate(docs)}
    toc = epub.toc()

    by_doc: "OrderedDict[str, list[TocEntry]]" = OrderedDict()
    for entry in toc:
        if entry.path in doc_order:
            by_doc.setdefault(entry.path, []).append(entry)

    units: list[Unit] = []
    seq = 0
    last_toc: "Unit | None" = None
    for doc in docs:
        entries = by_doc.get(doc)
        if entries:
            for i, entry in enumerate(entries):
                seq += 1
                nxt = None
                if i + 1 < len(entries):
                    peer = entries[i + 1]
                    # A run of entries sharing one anchor (calibre page-break
                    # hrefs, several 章 labels pointing at one page start):
                    # only the last of the run owns a span; the earlier ones
                    # own none -- "" tells the slicer so.
                    nxt = (
                        ""
                        if (peer.fragment or None) == (entry.fragment or None)
                        else peer.fragment
                    )
                u = Unit(seq, entry.label, doc, entry.fragment, entry.level,
                         next_fragment=nxt)
                units.append(u)
                last_toc = u
            continue

        if last_toc is not None:
            text = _doc_text(epub, doc)
            first, first_tag = _first_block(epub, doc)
            head_key = _title_key(text[: max(80, len(last_toc.title) + 40)])
            title_key = _title_key(last_toc.title)
            same = bool(head_key) and (
                head_key == title_key
                or head_key.startswith(title_key)
                or title_key.startswith(head_key)
            )
            own_empty = len(_doc_text(epub, last_toc.doc)) < 80
            if not is_part_title(text[:60]) and not _looks_like_new_chapter(text, last_toc.title):
                # A short standalone heading starts a new TOC-less section
                # (B1929 编者注); a title page repeats the parent title and is
                # a continuation.  Prose pages are continuations when the
                # TOC unit is body or its own page is empty.  A substantial
                # front-matter page (B1919 版权信息) does not absorb the
                # illustrated page that follows it.
                if (same or not _heading_like(first, first_tag)) and (
                    not text or len(text) >= 200 or same
                ) and (same or not text or own_empty or is_body_title(last_toc.title)):
                    seq += 1
                    cont = Unit(seq, None, doc, None, last_toc.toc_level, untoced=True)
                    cont.continuation = True
                    last_toc.continuations.append(cont)
                    continue

        if include_untoced:
            seq += 1
            units.append(Unit(seq, None, doc, None, 1, untoced=True))
    return units


def slice_root(epub: Epub, doc: str, fragment: str | None, next_fragment: str | None):
    """Return the body element trimmed to [fragment, next_fragment).

    next_fragment == "" marks a run of TOC entries sharing one anchor: this
    entry owns no span (an empty element is returned).
    A missing fragment with a real next_fragment -- a 篇/章 entry whose page
    also carries its sub-sections -- is bounded by that next anchor; taking
    the whole document there used to duplicate every sub-section into the
    parent file.
    """
    soup = epub.soup(doc)
    body = soup.find("body")
    if body is None:
        return None
    if next_fragment == "":
        return soup.new_tag("div")
    if not fragment and not next_fragment:
        return body
    # find the top-level element carrying the fragment id
    def locate(frag):
        if not frag:
            return None
        found = soup.find(id=frag)
        if found is None:
            found = soup.find(attrs={"name": frag})
        return found

    BLOCK_TAGS = {
        "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
        "li", "td", "blockquote", "table", "figure", "ol", "ul", "dl", "pre",
    }

    def btag(el) -> str:
        """bs4 element name (lowercased, namespace prefix stripped)."""
        name = getattr(el, "name", None) or ""
        return str(name).rsplit(":", 1)[-1].lower()

    def climb(el):
        """TOC fragments often land on an inline anchor -- a footnote marker
        (#ch1 inside a heading) or a `<a name=...>` mid-paragraph.  Slicing
        from the inline element itself yields no prose, so walk up to the
        nearest block-level ancestor and slice from there.  An anchor with
        no block ancestor at all -- an empty `<span id=...>` written
        directly under `<body>` (PDF-derived epubs: 美的历程, 三毛典藏全集) --
        is returned as itself: it is a body child, so the sibling slicing
        takes [span_k, span_k+1) correctly; returning None there used to fall
        back to the whole body and duplicate the document into every file."""
        node = el
        last = None
        while node is not None:
            tag = btag(node)
            if tag in BLOCK_TAGS:
                return node
            if tag in ("body", "[document]"):
                return last
            last = node
            node = node.parent
        return last

    start = climb(locate(fragment))
    end = climb(locate(next_fragment))
    if start is None:
        if end is None:
            return body
        # No start anchor but a real next anchor: take everything before
        # that anchor (flattened when it sits below another wrapper).
        wrapper = soup.new_tag("div")
        levels = []
        node = end
        while node.parent is not None and btag(node.parent) != "body":
            levels.append((node, node.parent))
            node = node.parent
        if node.parent is None or btag(node.parent) != "body":
            return body
        for child in list(body.children):
            if child is node:
                break
            wrapper.append(child.extract())
        for inner, parent in reversed(levels):
            for child in list(parent.children):
                if child is inner:
                    break
                wrapper.append(child.extract())
        return wrapper
    wrapper = soup.new_tag("div")
    if end is None and not next_fragment:
        # Last entry of the page: it owns everything from its start to the end
        # of the document.  Slicing only to the end of start's parent loses the
        # rest when the anchor sits in a small inner wrapper (calibre splits
        # pages that way): walk up -- take the start node, then every following
        # sibling at each level, skipping the intermediate containers.
        cur = start
        include_cur = True
        while cur is not None and cur.parent is not None:
            parent = cur.parent
            moving = False
            for child in list(parent.children):
                if child is cur:
                    moving = True
                    if include_cur:
                        wrapper.append(child.extract())
                    continue
                if moving:
                    wrapper.append(child.extract())
            if btag(parent) == "body" or parent.parent is None:
                break
            cur = parent
            include_cur = False
        return wrapper
    siblings = list(start.parent.children) if start.parent else []
    try:
        start_idx = siblings.index(start)
    except ValueError:
        return body
    end_idx = len(siblings)
    if end is not None and end.parent is start.parent:
        end_idx = siblings.index(end)
    for node in siblings[start_idx:end_idx]:
        wrapper.append(node.extract())
    return wrapper


LINK_OPEN = "\x01link:"
LINK_CLOSE = "\x02"
LINK_RE = re.compile(f"{LINK_OPEN}(.*?){LINK_CLOSE}")
MD_LINK_RE = re.compile(
    r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(" + LINK_OPEN + r"(.*?)" + LINK_CLOSE + r"\)"
)

PART_RE = re.compile(
    r"^(?:第\s*[0-9０-９一二三四五六七八九十百]+\s*[部篇卷]"
    r"|[上中下]\s*[部篇册]"
    r"|.{1,24}[上中下]\s*[部篇册]\s*$)"
)
#: "第1章", "01 通用战略", "12.3 细分", "附录1", "Appendix A" -- unambiguously body
CHAPTER_RE = re.compile(
    r"^\s*(第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节回讲]"
    r"|\d{1,3}\s*[.、:：]?\s+\S"
    r"|附录|Appendix\b)",
    re.I,
)
#: leading "1980年版", "(1)", "一、" before a matter word
MATTER_PREFIX_RE = re.compile(r"^\s*[（(\[]?\d{1,4}[)\]）]?\s*年?版?\s*[.、:：]?\s*")
# Front matter is named before the body; these are prefix-matched because a
# label usually carries its subtitle ("推荐序 赋能：...").
CJK_FRONT_WORDS = (
    "封面", "封底", "扉页", "书名页", "版权", "图书在版编目", "CIP", "目录", "目次",
    "推荐序", "代序", "序言", "译者序", "作者序", "自序", "他序", "前言", "导读",
    "写在前面", "出版说明", "出版信息", "献词", "凡例", "编委会",
    "作者简介", "译者简介", "内容简介", "题记", "彩插",
)
# Back matter that is page locators or thank-yous, not reading text.
CJK_BACK_WORDS = (
    "索引", "致谢", "鸣谢",
)
# Scholarly apparatus is body by default: a reader follows the book's [N] notes
# and bibliography to check a citation.  Dropping these used to cost 20-30% of a
# monograph (B1929 注释 26.5k, B1938 参考文献 38.4k, B1922 参考文献 55.5k).
# A book that really should omit them opts out with --drop '^注释$'.
# A short TOC-less page full of these is a colophon/publisher page, not a
# chapter continuation (B1929's 果麦 page sat after the chronology).
COLOPHON_RE = re.compile(
    r"产品经理|责任编辑|封面设计|责任印制|策划人|官方网站|官方微博|"
    r"出版信息|出品人|版权所有|ISBN|公众号|微信号|QQ群|"
    r"本书由.{0,12}整理|更多电子书|扫码关注"
)
LATIN_MATTER_WORDS = (
    "cover", "title page", "copyright", "colophon", "contents", "table of contents",
    "foreword", "preface", "dedication", "acknowledg", "acknowledgments",
    "acknowledgements", "acknowledgment", "acknowledgement", "about the author",
    "about the translator", "about the editors", "index", "further reading",
    "also by", "praise for", "frontmatter", "backmatter",
)


def is_part_title(title: str) -> bool:
    """True for part dividers like "第二部分 行业内的竞争范围"."""
    return bool(PART_RE.match((title or "").strip()))


NUMERIC_TITLE_RE = re.compile(r"^\s*(?:\d{1,3}|[一二三四五六七八九十百零〇]{1,3})\s*$")
#: a parent that numbered sections may be folded into: "第一章", "第12节"
CHAPTER_HEAD_RE = re.compile(r"^\s*第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节回]")


def merge_numeric_sections(units: list["Unit"]) -> list["Unit"]:
    """Fold bare-number sub-sections into the chapter unit that owns them.

    Some books split every chapter into numbered sections whose TOC labels are
    nothing but "01"/"二"; on their own they would become files named "01".
    A section is folded into the nearest preceding unit when that unit is
    chapter-titled ("第N章") and sits at a shallower level, so part dividers
    (上部/下部) and numbered chapters at the same level stay put.
    """
    merged: set = set()
    open_unit: "Unit | None" = None
    for u in units:
        if u.untoced:
            open_unit = None
            continue
        if NUMERIC_TITLE_RE.match(u.title):
            if (
                open_unit is not None
                and open_unit.toc_level < u.toc_level
                and CHAPTER_HEAD_RE.match(open_unit.title)
            ):
                open_unit.merge_children.append(u)
                open_unit.merge_children.extend(u.continuations)
                merged.add(id(u))
                continue
            open_unit = None
            continue
        open_unit = u
    if not merged:
        return units
    return [u for u in units if id(u) not in merged]


def is_body_title(title: str) -> bool:
    """False when a title names front/back matter rather than a body chapter.

    Numbered chapters always count as body; unknown titles default to body, so a
    mismatch drops nothing silently -- it shows up in the run report instead.
    """
    t = (title or "").strip()
    if not t:
        return False
    if CHAPTER_RE.match(t):
        return True
    core = MATTER_PREFIX_RE.sub("", t).lower()
    if core.startswith(CJK_FRONT_WORDS) or core.startswith(CJK_BACK_WORDS):
        return False
    for word in LATIN_MATTER_WORDS:
        if re.match(re.escape(word) + r"\b", core):
            return False
    return True


def strip_own_number(title: str, number: int) -> str:
    """Drop a leading chapter number from a file name when it repeats the index.

    "01 竞争优势：核心概念" as file 1 becomes "竞争优势：核心概念"; a number that
    does not match (or "附录1") is kept, because it carries real information.
    """
    m = re.match(r"^\s*(?:第\s*)?(\d{1,3})\s*[章节.、:：]?\s+", title or "")
    if m and int(m.group(1)) == number:
        rest = (title or "")[m.end():].strip()
        return rest or title
    return title


def link_dest(name: str) -> str:
    """标出文件名里会截断 markdown 目标的字符（括号、空格）。"""
    return f"<{name}>" if any(c in name for c in "() ") else name


def resolve_links(text: str, doc_unit: dict, own_file: str | None = None) -> str:
    """Replace link placeholders with the file name of the target chapter."""
    def repl(m):
        label, target = m.group(1), m.group(2)
        unit = doc_unit.get(target)
        if unit is None or not unit.written or unit.filename == own_file:
            return label  # no file of its own, or a self-link: keep the words only
        return f"[{label}]({link_dest(unit.filename)})"

    text = MD_LINK_RE.sub(repl, text)
    return LINK_RE.sub("", text)


def clean_markup(text: str) -> str:
    """Strip renderer markup from block text used as a title.

    Block text still carries inline markup: footnote references come out as
    "[^[1]^](\\x01link:...)"; a title guessed from such a line would leak the
    placeholder into the file name (阿加莎's dedication pages did exactly
    that).  Unwrap links, drop footnote markers, unwrap emphasis.
    """
    foot = r"\[\^?\[?[0-9]{1,3}\]?\^?\]"                     # [^1^] [^[1]^] [[1]] [1]
    text = re.sub(foot + r"\s*\([^)]*\)", "", text)          # footnote ref with link
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)     # normal link -> text
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)         # image
    text = re.sub(foot, "", text)                            # bare footnote marker
    text = re.sub(r"\x01link:[^\x02]*\x02", "", text)        # raw placeholder
    text = re.sub(r"\^+\s*([^\^\s]{0,8}?)\s*\^+", r"\1", text)  # ^[1]^ -> [1]
    text = text.replace("**", "").replace("*", "").replace("`", "")
    return text


def guess_title_from_blocks(blocks, maxlen: int = 24) -> str:
    """Derive a page title from its own text, for pages the TOC does not list."""
    text = first_block_text(blocks)
    if not text:
        return ""
    text = clean_markup(text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # unwrap links
    text = re.sub(r"(?:https?://|www\.)\S+", " ", text)  # drop URLs
    text = re.split(r"[。．！!？?；;…]|\.(?=\s|$)", text)[0].strip()
    if len(text) > maxlen:
        text = re.split(r"[：:，,、\s]", text[:maxlen])[0] or text[:maxlen]
    text = re.sub(r"[（(\[【]\s*[A-Za-z0-9 .,'’]*$", "", text)  # no dangling "(C. Rol"
    text = text.strip(" 　·•-—:：,，、")
    return text if 2 <= len(text) <= maxlen else ""


def first_block_text(blocks) -> str:
    for b in blocks:
        if b.kind in ("heading", "para", "title") and b.text:
            return b.text
    return ""


CAPTION_RE = re.compile(
    r"^\s*(图|表|注[:：]|附注|Figure|Table|Fig\.?|Source|资料来源|来源[:：])"
)


def is_standalone_line(text: str, maxlen: int = 50) -> bool:
    """A short, punctuation-free, unlinked line: heading material, not prose."""
    t = text.strip()
    if not t or len(t) > maxlen:
        return False
    if t[-1] in TERMINAL_PUNCT:
        return False
    if BULLET_RE.match(t) or CAPTION_RE.match(t):
        return False
    if t.startswith(("[", "!", "|", ">", "*", "_", "#")):
        return False
    return True


def recover_headings_by_style(units: list["Unit"]) -> None:
    """Recover headings from layout alone.

    Some books (especially converted Chinese ebooks) mark headings with a CSS
    class that differs only in alignment/indentation -- no <h*> tags and no
    font-size difference.  Such a class is recognisable: it is used for short,
    punctuation-free lines that never carry links and are never right-aligned,
    and it appears on pages that also contain real prose.
    """
    stats: dict[str, dict] = {}
    order = 0
    for unit in units:
        unit.has_prose = any(
            b.kind in ("para", "quote") and len(b.text) >= 80 for b in unit.blocks
        )
        if unit.excluded:
            continue
        for b in unit.blocks:
            if b.kind not in ("para", "quote") or not b.group:
                continue
            s = stats.setdefault(
                b.group, {"n": 0, "short": 0, "links": 0, "right": 0,
                          "mtop": 0.0, "mtop_n": 0, "first": order}
            )
            order += 1
            s["n"] += 1
            if b.align == "right":
                s["right"] += 1
            if b.has_link:
                s["links"] += 1
            if is_standalone_line(b.text):
                s["short"] += 1
                if b.mtop:
                    s["mtop"] += b.mtop
                    s["mtop_n"] += 1

    heading_groups = {
        g: s
        for g, s in stats.items()
        if s["n"] >= 2
        and s["short"] >= 0.7 * s["n"]
        and not s["links"]
        and not s["right"]
    }
    if not heading_groups:
        return
    # a class that sits further from the text above it (bigger margin) and
    # appears earlier ranks higher in the heading hierarchy
    ranked = sorted(
        heading_groups.items(),
        key=lambda kv: (
            -(kv[1]["mtop"] / kv[1]["mtop_n"] if kv[1]["mtop_n"] else 0.0),
            kv[1]["first"],
        ),
    )
    levels = {g: min(6, 2 + i) for i, (g, _) in enumerate(ranked)}

    for unit in units:
        if unit.excluded or not unit.has_prose:
            continue
        if any(b.kind == "heading" for b in unit.blocks):
            continue
        for b in unit.blocks:
            if b.kind in ("para", "quote") and b.group in levels and is_standalone_line(b.text):
                b.kind = "heading"
                b.level = levels[b.group]
                b.recovered = True


def normalize_heading_levels(blocks: list[Block], title_text: str) -> None:
    """Map source heading levels onto ##/###/... with ## as the chapter's top."""
    heads = [b for b in blocks if b.kind == "heading" and not b.recovered]
    if not heads:
        return
    if any(b.source is not None and b.source.name in HEADING_TAGS for b in heads):
        native = sorted({b.level for b in heads if b.level})
        mapping = {lvl: min(6, 1 + i) for i, lvl in enumerate(native)}
        # 1 -> '#' is reserved for the chapter title, so shift one down
        mapping = {lvl: min(6, lvl_out + 1) for lvl, lvl_out in mapping.items()}
    else:
        sizes = sorted({round(b.size, 2) for b in heads}, reverse=True)
        mapping = {s: min(6, 2 + i) for i, s in enumerate(sizes)}
    key = (lambda b: b.level) if any(b.level for b in heads) else (
        lambda b: round(b.size, 2)
    )
    for b in heads:
        b.level = mapping.get(key(b), 2)


def drop_duplicate_title(blocks: list[Block], title_text: str) -> None:
    """Remove the in-body repetition of the chapter title (it becomes the H1)."""
    if not title_text:
        return
    norm = lambda s: re.sub(r"[\s\u3000·、,，.。:：\-–—_]+", "", s or "")
    target = norm(title_text)
    if not target:
        return
    for i, b in enumerate(blocks[:4]):
        if b.kind not in ("heading", "para"):
            continue
        if norm(b.text) == target:
            del blocks[i]
            return
        if b.kind == "heading" and target.startswith(norm(b.text)) and len(norm(b.text)) >= 4:
            del blocks[i]
            return


def render(blocks: list[Block]) -> str:
    out: list[str] = []
    prev_kind = None
    for b in blocks:
        if b.kind == "heading":
            out.append(f"{'#' * max(2, min(6, b.level))} {b.text}")
        elif b.kind == "para":
            out.append(b.text)
        elif b.kind == "image":
            out.append(b.text)
        elif b.kind == "hr":
            out.append("---")
        elif b.kind == "quote":
            for line in b.text.split("\n"):
                out.append(f"> {line}".rstrip())
        elif b.kind == "code":
            out.append("```\n" + b.text + "\n```")
        elif b.kind == "list":
            text = BULLET_RE.sub("", b.text, count=1)
            if prev_kind == "list":
                out[-1] = out[-1] + "\n" + f"- {text}"
            else:
                out.append(f"- {text}")
        elif b.kind == "table":
            out.append(b.text)
        prev_kind = b.kind
    text = "\n\n".join(x for x in out if x is not None)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def add_output_options(ap) -> None:
    """Output options shared by ebook_to_md.py and split_collection.py."""
    ap.add_argument(
        "--no-images", action="store_true", help="drop images instead of copying them"
    )
    ap.add_argument(
        "--no-frontmatter", action="store_true", help="omit the YAML front matter block"
    )
    ap.add_argument(
        "--all-pages",
        action="store_true",
        help="keep front/back matter too (default: body chapters only)",
    )
    ap.add_argument(
        "--drop",
        action="append",
        metavar="REGEX",
        help="also drop chapters whose title matches REGEX (repeatable)",
    )
    ap.add_argument(
        "--keep",
        action="append",
        metavar="REGEX",
        help=(
            "keep pages whose title matches REGEX -- overrides the front/back "
            "matter filter when a body section is named like one "
            "(e.g. 金字塔原理's 序言的结构); repeatable"
        ),
    )
    ap.add_argument(
        "--heading-recovery",
        choices=["auto", "off"],
        default="auto",
        help="recover headings in books that use plain <p> for them (default: auto)",
    )
    ap.add_argument(
        "--no-merge-sections",
        action="store_true",
        help="keep numbered sub-sections (01, 二, ...) as files of their own "
        "instead of folding them into the 第N章 parent they belong to",
    )
    ap.add_argument("--no-index", action="store_true", help="do not write README.md")


PART_DIVIDER_TEXT_LIMIT = 200


def _unit_text_len(u: "Unit") -> int:
    return sum(len(b.text) for b in u.blocks if getattr(b, "text", ""))


def _unit_is_filler(u: "Unit") -> bool:
    """True for divider pages that are a title and little else.

    A part-titled unit that carries real text must stay a chapter file --
    三体II's "上部 面壁者" is a whole part in one document, and dropping it
    as a divider would silently lose 100k+ characters.
    """
    return _unit_text_len(u) < PART_DIVIDER_TEXT_LIMIT


def _unit_looks_like_colophon(u: "Unit") -> bool:
    """A short TOC-less page that is a publisher/copyright colophon."""
    text = " ".join(b.text for b in u.blocks if getattr(b, "text", ""))
    if not text or len(text) > 800:
        return False
    hits = len(COLOPHON_RE.findall(text))
    return hits >= 2 or (hits >= 1 and len(text) < 200)


def emit_book(
    epub: Epub,
    units: list["Unit"],
    outdir: str,
    title: str,
    source_name: str,
    args,
    *,
    collection: str = "",
    marker: "Unit | None" = None,
) -> dict:
    """Write one book's units into `outdir` (NN_*.md + images/ + README.md).

    Shared by the plain single-book run and by split_collection.py, which
    calls it once per book of a 套装/全集 (each book gets its own folder).
    `units` must belong to one book only.

    `marker` is the unit carrying the book's own title/cover page when the
    book comes from a collection: it never becomes a file of its own, it is
    listed under 未收录 instead.  Returns a stats dict.
    """
    stats = {
        "chapters": 0, "chars": 0, "images": 0, "baseline": 16.0,
        "written": [], "skipped": [], "warnings": [], "lines": [],
    }
    if not units:
        return stats
    os.makedirs(outdir, exist_ok=True)
    imgdir = os.path.join(outdir, "images")

    # ---- image sink ------------------------------------------------------
    used_names: dict[str, str] = {}
    image_map: dict[tuple[str, str], str] = {}
    image_count = 0

    def image_sink(doc: str, src_attr: str):
        nonlocal image_count
        key = (doc, src_attr)
        if key in image_map:
            return image_map[key]
        target = epub.resolve(doc, src_attr)
        if not epub.exists(target):
            converter.warnings.append(f"missing image: {target} (in {doc})")
            image_map[key] = None
            return None
        base = sanitize_filename(posixpath.basename(target), "image", maxlen=60)
        if not re.search(r"\.(jpe?g|png|gif|webp|svg|bmp)$", base, re.I):
            base += ".jpg"
        name = base
        n = 2
        while name in used_names and used_names[name] != target:
            stem, ext = os.path.splitext(base)
            name = f"{stem}_{n}{ext}"
            n += 1
        used_names[name] = target
        image_map[key] = f"images/{name}"
        os.makedirs(imgdir, exist_ok=True)
        with open(os.path.join(imgdir, name), "wb") as fh:
            fh.write(epub.zip.read(target))
        image_count += 1
        return image_map[key]

    converter = Converter(epub, image_sink, keep_images=not args.no_images)

    # ---- internal link rewriting ----------------------------------------
    doc_unit: dict[str, Unit] = {}
    for u in units:
        doc_unit.setdefault(u.doc, u)
        for child in u.merge_children + u.continuations:
            doc_unit.setdefault(child.doc, u)

    def link_resolver(doc: str, href: str):
        """Emit a placeholder: file names are only known once titles are final."""
        if not href or href.startswith(("#", "http://", "https://", "mailto:")):
            return None
        target_doc = epub.resolve(doc, href)
        if target_doc not in doc_unit:
            return None
        return f"{LINK_OPEN}{target_doc}{LINK_CLOSE}"

    converter.link_resolver = link_resolver

    # ---- baseline font size ---------------------------------------------
    sample_sizes: Counter = Counter()
    for u in units[:60]:
        sheet = epub.style_for(u.doc, epub.global_styles())
        soup = epub.soup(u.doc)
        body = soup.find("body")
        if body is None:
            continue
        for p in body.find_all(["p", "div"], limit=400):
            if p.find(["p", "div"]) is not None:
                continue
            dom, _, weight = converter._text_stats(p, sheet, 16.0, False)
            if weight >= 20:
                sample_sizes[round(dom, 1)] += weight
    if sample_sizes:
        converter.baseline = max(sample_sizes.items(), key=lambda kv: kv[1])[0]

    # ---- convert (pass 1: blocks only, no rendering yet) ------------------
    for u in units:
        u.blocks = []
        sources = (
            [(u.doc, u)]
            + [(c.doc, c) for c in u.merge_children]
            + [(c.doc, c) for c in u.continuations]
        )
        for src_doc, src_unit in sources:
            sheet = epub.style_for(src_doc, epub.global_styles())
            root = slice_root(epub, src_doc, src_unit.fragment, src_unit.next_fragment)
            child_blocks = converter.convert(src_doc, sheet, root=root)
            if src_unit is not u:
                # a folded sub-section repeats its own (numeric) label in the
                # body; a continuation page often repeats the chapter title.
                # Drop that repetition, keep everything else.
                drop_duplicate_title(
                    child_blocks,
                    src_unit.title if src_unit in u.merge_children else u.title,
                )
            u.blocks.extend(child_blocks)

    # pages outside the TOC get their title from their own first line
    for u in units:
        if not u.untoced:
            continue
        guess = guess_title_from_blocks(u.blocks)
        if guess:
            u.title, u.derived = guess, True
        else:
            u.title, u.derived = f"前置页 {u.index}", False

    for u in units:
        drop_duplicate_title(u.blocks, u.title)

    # ---- keep body chapters only (default) --------------------------------
    if not args.all_pages:
        first_toced = next((i for i, u in enumerate(units) if not u.untoced), None)
        keep_res = [re.compile(p) for p in (args.keep or [])]
        def _forced_keep(u) -> bool:
            return any(rx.search(u.title) for rx in keep_res)
        for i, u in enumerate(units):
            if u is marker:
                u.excluded, u.reason = True, "书名页/目录页 (book title page)"
            elif _forced_keep(u):
                continue
            elif first_toced is not None and u.untoced and i < first_toced:
                u.excluded, u.reason = True, "front matter (not in TOC)"
            elif u.untoced and _unit_looks_like_colophon(u):
                u.excluded, u.reason = True, "版权/出版信息页"
            elif is_part_title(u.title) and _unit_is_filler(u):
                u.excluded, u.reason = True, "part divider"
            elif not is_body_title(u.title):
                u.excluded, u.reason = True, "front/back matter"
        for pattern in args.drop or []:
            rx = re.compile(pattern)
            short = pattern if len(pattern) <= 48 else pattern[:45] + "…"
            for u in units:
                if not u.excluded and rx.search(u.title):
                    u.excluded, u.reason = True, f"matched --drop {short!r}"

    # ---- heading recovery -------------------------------------------------
    if args.heading_recovery != "off":
        for u in units:
            if any(b.kind == "heading" for b in u.blocks):
                continue
            for b in u.blocks:
                if b.kind == "para" and converter._is_heading_candidate(b.text, b.size, b.bold):
                    b.kind = "heading"
        recover_headings_by_style(units)
    for u in units:
        normalize_heading_levels(u.blocks, u.title)

    # ---- file names (titles are final now, so links can resolve) ----------
    for u in units:
        u.empty = not any(b.text.strip() for b in u.blocks)
        if u.empty and not u.excluded:
            u.reason = "part divider" if is_part_title(u.title) else "no text of its own"
        u.written = not (u.empty or u.excluded)

    kept = sum(1 for u in units if u.written)
    width = max(2, len(str(kept)))
    seen_stems: dict[str, int] = {}
    number = 0
    for u in units:
        if u.written:
            number += 1
        u.number = number if u.written else u.index
        stem = f"{u.number:0{width}d}_{sanitize_filename(strip_own_number(u.title, u.number))}"
        if stem in seen_stems:
            seen_stems[stem] += 1
            stem = f"{stem}_{seen_stems[stem]}"
        else:
            seen_stems[stem] = 1
        u.filename = stem + ".md"

    # ---- render and write (pass 2) ----------------------------------------
    part = ""
    for u in units:
        if is_part_title(u.title):
            part = u.title
        if not u.written:
            continue
        body_md = resolve_links(render(u.blocks), doc_unit, u.filename)
        u.chars = len(re.sub(r"\s", "", body_md))

        meta = OrderedDict()
        meta["title"] = u.title
        if part and part != u.title:
            meta["part"] = part
        if collection:
            meta["collection"] = collection
        if epub.metadata.get("creator"):
            meta["author"] = epub.metadata["creator"]
        if epub.metadata.get("publisher"):
            meta["publisher"] = epub.metadata["publisher"]
        if epub.metadata.get("date"):
            meta["date"] = epub.metadata["date"]
        if epub.metadata.get("language"):
            meta["language"] = epub.metadata["language"]
        meta["source"] = source_name
        meta["chapter"] = u.number

        parts = [f"# {u.title}\n"]
        if not args.no_frontmatter:
            yaml = "\n".join(f'{k}: "{str(v).replace(chr(34), chr(39))}"' for k, v in meta.items())
            parts.insert(0, f"---\n{yaml}\n---\n")
        parts.append(body_md)
        with open(os.path.join(outdir, u.filename), "w", encoding="utf-8") as fh:
            fh.write("\n".join(parts).strip() + "\n")

    # ---- drop images nothing refers to (pages that were skipped) ----------
    referenced = set()
    for u in units:
        if not u.written:
            continue
        with open(os.path.join(outdir, u.filename), encoding="utf-8") as fh:
            referenced.update(re.findall(r"\]\((images/[^)]+)\)", fh.read()))
    pruned = 0
    if os.path.isdir(imgdir):
        for name in sorted(os.listdir(imgdir)):
            if f"images/{name}" not in referenced:
                os.remove(os.path.join(imgdir, name))
                pruned += 1
        if not os.listdir(imgdir):
            os.rmdir(imgdir)
    if pruned:
        image_count -= pruned

    # files from an earlier run that this run did not produce
    stale = sorted(
        f for f in os.listdir(outdir)
        if f.endswith(".md") and f != "README.md"
        and f not in {u.filename for u in units if u.written}
    )
    if stale:
        print(
            f"note: {len(stale)} .md file(s) in the output folder are left over from an "
            f"earlier run and were not rewritten: {', '.join(stale[:4])}"
            + (" …" if len(stale) > 4 else "")
        )

    # ---- index -----------------------------------------------------------
    written = [u for u in units if u.written]
    if not args.no_index:
        lines = [f"# {title}", ""]
        info = []
        if collection:
            info.append(f"- 合集：{collection}")
        if epub.metadata.get("creator"):
            info.append(f"- 作者：{epub.metadata['creator']}")
        if epub.metadata.get("publisher"):
            info.append(f"- 出版：{epub.metadata['publisher']}")
        if epub.metadata.get("date"):
            info.append(f"- 日期：{epub.metadata['date']}")
        info.append(f"- 章节：{len(written)}")
        info.append(f"- 图片：{image_count}")
        info.append(f"- 来源：`{source_name}`")
        lines += info + ["", "## 目录", ""]
        # 目录首行缩进 4 格以上、前面又没有父项时，整段会被 CommonMark 当缩进代码块，
        # 一条都不成链接（B1187 孤独与沉思）：首行按 0 缩进排，其余照层级相对缩进。
        shown = [u for u in units if u.written or is_part_title(u.title)]
        top = shown[0].toc_level if shown and shown[0].toc_level >= 3 else 1
        for u in units:
            pad = "  " * max(0, u.toc_level - top)
            if u.written:
                lines.append(f"{pad}- [{u.title}]({link_dest(u.filename)})")
            elif is_part_title(u.title):
                lines.append(f"{pad}- **{u.title}**")
        skipped = [u for u in units if not u.written and not is_part_title(u.title)]
        if skipped:
            lines += ["", "## 未收录（卷首/卷尾页）", ""]
            for u in skipped:
                lines.append(f"- {u.title} — {u.reason}")
        with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines).strip() + "\n")

    # ---- stats -----------------------------------------------------------
    stats["chapters"] = len(written)
    stats["chars"] = sum(u.chars for u in units)
    stats["images"] = image_count
    stats["baseline"] = converter.baseline
    stats["written"] = written
    stats["skipped"] = [u for u in units if not u.written]
    stats["warnings"] = list(converter.warnings)
    for u in units:
        if u.written:
            flag = "  " if u.chars >= 200 else "!!"
            stats["lines"].append(f"  {flag} {u.filename:<46s} {u.chars:>8,} chars")
        else:
            stats["lines"].append(f"  -- skipped: {u.title[:40]:<42s} ({u.reason})")
    return stats


def run(args) -> int:
    src = os.path.abspath(args.input)
    if not os.path.isfile(src):
        sys.exit(f"input not found: {src}")
    if not zipfile.is_zipfile(src):
        sys.exit(
            f"{src} is not an EPUB (not a zip archive).\n"
            "MOBI/AZW3/PDF must be converted to EPUB first -- see SKILL.md."
        )

    epub = Epub(src)
    title = args.title or epub.metadata.get("title") or os.path.splitext(os.path.basename(src))[0]
    outdir = os.path.abspath(
        args.outdir or os.path.join(os.path.dirname(src), sanitize_filename(title))
    )

    units = build_units(epub, not args.no_untoced)
    if not units:
        if args.no_untoced:
            sys.exit(
                "no chapters found: the book has no TOC (nav doc or NCX).\n"
                "Drop --no-untoced to fall back to splitting on spine documents."
            )
        sys.exit("no chapters found: no readable HTML/XHTML document in the spine.")
    if not epub.toc():
        print(
            f"warning: this book has no TOC (nav doc / NCX) -- falling back to "
            f"{len(units)} spine documents as chapter files.",
            file=sys.stderr,
        )

    merged = 0
    if not args.no_merge_sections:
        before = len(units)
        units = merge_numeric_sections(units)
        merged = before - len(units)

    stats = emit_book(epub, units, outdir, title, os.path.basename(src), args)

    # ---- report ----------------------------------------------------------
    print(f"source      : {src}")
    print(f"title       : {title}")
    print(f"output      : {outdir}")
    print(f"chapters    : {stats['chapters']}")
    print(f"characters  : {stats['chars']:,} (CJK+latin, whitespace excluded)")
    print(f"images      : {stats['images']}")
    print(f"baseline fs : {stats['baseline']}px")
    if merged:
        print(f"merged      : {merged} numbered sub-section(s) folded into their chapter")
    print("")
    for line in stats["lines"]:
        print(line)
    if stats["warnings"]:
        print("\nwarnings:")
        for w in stats["warnings"]:
            print(f"  - {w}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Split an EPUB into one Markdown file per chapter (images included)."
    )
    ap.add_argument("input", help="path to the .epub file")
    ap.add_argument("-o", "--outdir", help="output directory (default: ./<book title>/)")
    ap.add_argument("--title", help="override the book title used for naming")
    ap.add_argument(
        "--no-untoced",
        action="store_true",
        help="only emit pages the table of contents lists (default: also emit the rest)",
    )
    add_output_options(ap)
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
