#!/usr/bin/env python3
"""CJK bigram 索引与查询的同一份切分。索引侧与查询侧都从这里拿。

索引侧：中文连续串切成重叠二元组，单字串保留单字；ASCII 词原样小写。
查询侧：1 字走前缀，2 字走单 token，3 字以上走相邻二元组 phrase。
"""
import re
import unicodedata

RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z0-9]+")
CJK = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff]+$")

# 查询词组分隔符：空白与半角/全角逗号、顿号、分号
SPLIT = re.compile(r"[\s,，、;；]+")


def split_groups(text):
    """查询侧的组切分：NFKC 把全角字母数字折成半角，再按分隔符切组。

    只动查询侧，索引侧 index_text 不归一——语料里的全角字符本来就不进
    RUN 正则（索引和查询两侧都不认），归一只是把查询词修成索引里有的形态。
    """
    return [g for g in SPLIT.split(unicodedata.normalize("NFKC", text or "")) if g]


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
    assert split_groups("自由，平等、博爱") == ["自由", "平等", "博爱"]
    assert split_groups("１９８４ ａｂｃ") == ["1984", "abc"]
    assert split_groups("") == []
    print("lib_fts_cjk 自检通过")


if __name__ == "__main__":
    selftest()
