#!/usr/bin/env python3
"""CJK bigram 索引与查询的同一份切分。索引侧与查询侧都从这里拿。

索引侧：中文连续串切成重叠二元组，单字串保留单字；ASCII 词原样小写。
查询侧：1 字走前缀，2 字走单 token，3 字以上走相邻二元组 phrase。
"""
import re

RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9]+")
CJK = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")


def tokens(text):
    out = []
    for run in RUN.findall(text):
        if CJK.match(run):
            if len(run) == 1:
                out.append(run)
            else:
                out.extend(run[i:i + 2] for i in range(len(run) - 1))
        else:
            out.append(run.lower())
    return out


def index_text(text):
    return " ".join(tokens(text))


def _quote(tok):
    # ASCII 词也必须加引号，否则 and/or/not/near 会被 FTS5 当运算符
    return '"%s"' % tok.replace('"', '""')


def query_expr(q):
    """把用户输入的词变成一个 FTS5 MATCH 表达式；多段之间是 AND。"""
    parts = []
    for run in RUN.findall(q):
        if CJK.match(run):
            if len(run) == 1:
                parts.append("%s*" % run)
            elif len(run) == 2:
                parts.append(_quote(run))
            else:
                parts.append('"%s"' % " ".join(run[i:i + 2] for i in range(len(run) - 1)))
        else:
            parts.append(_quote(run.lower()))
    if not parts:
        return None
    return " AND ".join(parts)


def selftest():
    cases = {
        "自由": ['自由'],
        "内卷": ['内卷'],
        "清华": ['清华'],
        "自由自在": ['自由', '由自', '自在'],
        "1984 自由": ['1984', '自由'],
        "㐀㐁㐂": ['㐀㐁', '㐁㐂'],
    }
    for text, want in cases.items():
        got = tokens(text)
        assert got == want, (text, got, want)
    assert query_expr("自由") == '"自由"'
    assert query_expr("礼") == "礼*"
    assert query_expr("现代的铁笼") == '"现代 代的 的铁 铁笼"'
    assert query_expr("COVID 19") == '"covid" AND "19"'
    assert query_expr("㐀㐁㐂") == '"㐀㐁 㐁㐂"'
    print("lib_fts_cjk 自检通过")


if __name__ == "__main__":
    selftest()
