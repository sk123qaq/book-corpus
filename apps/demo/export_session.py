#!/usr/bin/env python3
"""会话导出：Markdown 与 docx（手工 OOXML，只用标准库）。

先按轮把会话整理成一份文档模型，再交给两个渲染器。md 与 docx 的内容因此
同源，字段顺序固定为问题、范围、回答、本轮依据。

  from export_session import response
  body, ctype, headers = response(sess, sid, "docx")
"""

import io
import re
import time
import zipfile
from urllib.parse import quote

import session

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff\ufffe\uffff]")
_NAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')


def _text(value):
    return str(value if value is not None else "").strip()


def _first_question(sess):
    for turn in (sess or {}).get("turns") or []:
        question = _text(turn.get("question"))
        if question:
            return " ".join(question.split())
    return ""


def _scope(turn):
    """本轮范围：只认 sources，和页面「范围」行同口径；没有就不写。"""
    names, seen = [], set()

    def add(value):
        name = _text(value)
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    ans = turn.get("answer") if isinstance(turn.get("answer"), dict) else {}
    for src in turn.get("sources") or ans.get("sources") or []:
        if isinstance(src, dict):
            add(src.get("title"))
    return names


def _answer(turn):
    ans = turn.get("answer") if isinstance(turn.get("answer"), dict) else {}
    body = _text(ans.get("answer"))
    if body:
        return body
    trace = turn.get("trace") if isinstance(turn.get("trace"), dict) else {}
    result = trace.get("result") if isinstance(trace.get("result"), dict) else {}
    return _text(result.get("text"))


def _cites(turn):
    ans = turn.get("answer") if isinstance(turn.get("answer"), dict) else {}
    rows = []
    for cite in ans.get("citations") or []:
        if not isinstance(cite, dict):
            continue
        try:
            line = int(cite.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        rows.append(
            {
                "title": session.citation_title(turn, cite),
                "chapter": _text(cite.get("chapter")),
                "path": _text(cite.get("path")),
                "line": line,
                "quote": " ".join(_text(cite.get("quote")).split()),
            }
        )
    return rows


def model(sess, stamp=None):
    """会话 → 文档模型。stamp 为 None 时取当前时间。"""
    turns = []
    for turn in (sess or {}).get("turns") or []:
        if not isinstance(turn, dict):
            continue
        turns.append(
            {
                "n": turn.get("n") or len(turns) + 1,
                "question": _text(turn.get("question")),
                "scope": _scope(turn),
                "answer": _answer(turn),
                "citations": _cites(turn),
            }
        )
    return {
        "title": _first_question(sess)[:40] or "会话",
        "stamp": stamp or time.strftime("%Y-%m-%d %H:%M"),
        "turns": turns,
    }


# ---- Markdown ------------------------------------------------------


def _cite_line(index, cite):
    parts = ["《%s》" % cite["title"]]
    if cite["chapter"]:
        parts.append(cite["chapter"])
    parts.append("%s:%d" % (cite["path"], cite["line"]))
    return "%d. %s" % (index, " · ".join(parts))


def markdown(doc):
    out = ["# " + doc["title"], "", "导出时间：%s　轮数：%d" % (doc["stamp"], len(doc["turns"])), ""]
    for turn in doc["turns"]:
        out += ["## 第 %d 轮" % turn["n"], ""]
        out += ["**问题**：" + turn["question"], ""]
        if turn["scope"]:
            out += ["**范围**：" + "".join("《%s》" % n for n in turn["scope"]), ""]
        if turn["answer"]:
            out += ["**回答**", "", turn["answer"], ""]
        if turn["citations"]:
            out += ["**本轮依据**", ""]
            for i, cite in enumerate(turn["citations"], 1):
                out.append(_cite_line(i, cite))
                if cite["quote"]:
                    out.append("   > " + cite["quote"])
            out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---- docx ----------------------------------------------------------


def _xml(value):
    text = _XML_BAD.sub("", str(value if value is not None else ""))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _run(text, bold=False, italic=False):
    props = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
    rpr = "<w:rPr>%s</w:rPr>" % props if props else ""
    return '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _xml(text))


def _para(runs="", style="", indent=0):
    ppr = ""
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    if indent:
        ppr += '<w:ind w:left="%d"/>' % indent
    head = "<w:pPr>%s</w:pPr>" % ppr if ppr else ""
    return "<w:p>%s%s</w:p>" % (head, runs)


def _answer_paras(body):
    rows = []
    for line in body.split("\n"):
        rows.append(_para(_run(line.strip())))
    return "".join(rows)


def _document(doc):
    body = [
        _para(_run(doc["title"]), style="Heading1"),
        _para(_run("导出时间：%s　轮数：%d" % (doc["stamp"], len(doc["turns"])))),
    ]
    for turn in doc["turns"]:
        body.append(_para(_run("第 %d 轮" % turn["n"]), style="Heading2"))
        body.append(_para(_run("问题", bold=True) + _run("：" + turn["question"])))
        if turn["scope"]:
            body.append(_para(_run("范围", bold=True) + _run("：" + "".join("《%s》" % n for n in turn["scope"]))))
        if turn["answer"]:
            body.append(_para(_run("回答", bold=True)))
            body.append(_answer_paras(turn["answer"]))
        if turn["citations"]:
            body.append(_para(_run("本轮依据", bold=True)))
            for i, cite in enumerate(turn["citations"], 1):
                body.append(_para(_run(_cite_line(i, cite))))
                if cite["quote"]:
                    body.append(_para(_run("引用：" + cite["quote"], italic=True), indent=720))
    body.append(
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" '
        'w:left="1440"/></w:sectPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % "".join(body)
    )


_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/'
    'vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/'
    'vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    "</Types>"
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/></Relationships>'
)

_DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
    'relationships"><Relationship Id="rId1" Type="http://schemas.'
    'openxmlformats.org/officeDocument/2006/relationships/styles" '
    'Target="styles.xml"/></Relationships>'
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
    '2006/main">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault>'
    '<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="300" '
    'w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:qFormat/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading1">'
    '<w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:before="240" w:after="240"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>'
    '<w:style w:type="paragraph" w:styleId="Heading2">'
    '<w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
    '<w:next w:val="Normal"/><w:qFormat/>'
    '<w:pPr><w:spacing w:before="200" w:after="120"/></w:pPr>'
    '<w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr></w:style>'
    "</w:styles>"
)


def docx(doc):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("word/_rels/document.xml.rels", _DOC_RELS)
        zf.writestr("word/document.xml", _document(doc))
        zf.writestr("word/styles.xml", _STYLES)
    return buf.getvalue()


# ---- 文件名与响应 --------------------------------------------------


def _stem(doc, limit):
    name = _NAME_BAD.sub("", " ".join(doc["title"].split())).strip(" .")
    return name[:limit] or "会话"


def _disposition(sid, doc, ext):
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(sid or "")) or "session"
    ascii_name = "session-%s.%s" % (sid, ext)
    stamp = doc["stamp"].replace("-", "").replace(" ", "-").replace(":", "")
    utf8_name = "会话-%s-%s.%s" % (stamp, _stem(doc, 12), ext)
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quote(utf8_name, safe=""))


def response(sess, sid, fmt, stamp=None):
    """返回 (body_bytes, content_type, headers)；fmt 只接受 md 与 docx。"""
    doc = model(sess, stamp)
    if fmt == "docx":
        return docx(doc), DOCX_CT, {"Content-Disposition": _disposition(sid, doc, "docx")}
    return (
        markdown(doc).encode("utf-8"),
        "text/markdown; charset=utf-8",
        {"Content-Disposition": _disposition(sid, doc, "md")},
    )
