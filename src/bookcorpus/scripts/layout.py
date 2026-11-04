"""工作区树形。树只在这里命名一次，别的模块不许再拼 库/ 目录/ 视图/。

  books/<uid>/            一本书一个目录，装 book.json、source/、md/、html/
  books/_收件箱、_冷宫      不属任何书，下划线开头
  collections/<Cuid>/     合集原书，原始文件放 source/
  tables/                 人写的表与三张概念表
  build/                  一切派生产物，整目录删掉能重建

根靠 books.toml 认。manifest 里的 schema 就是这个树形的版本号。
"""

import os

SCHEMA = 2
MANIFEST = "books.toml"
INBOX = "_收件箱"
COLD = "_冷宫"
RESERVED = (INBOX, COLD)
LEDGER_COLS = ["uid", "一级", "二级", "作者层", "保留", "正式书名", "备注"]

INIT_DIRS = (
    os.path.join("books", INBOX),
    os.path.join("books", COLD),
    "collections",
    "tables",
    os.path.join("tables", "系列"),
    os.path.join("build", "检索"),
    os.path.join("build", "views"),
    os.path.join("工作区", "批次"),
)


def find_root(start=None):
    """工作区根。BOOKS_ROOT 优先，其次从 start 向上找 books.toml，找不到返回 None。"""
    env = os.environ.get("BOOKS_ROOT")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(cur, MANIFEST)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


class Layout:
    """一个工作区的全部路径。只认 root，别的都是它的投影。"""

    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))

    def __repr__(self):
        return "Layout(%r)" % self.root

    @property
    def manifest(self):
        return os.path.join(self.root, MANIFEST)

    @property
    def books(self):
        return os.path.join(self.root, "books")

    @property
    def collections(self):
        return os.path.join(self.root, "collections")

    @property
    def tables(self):
        return os.path.join(self.root, "tables")

    @property
    def build(self):
        return os.path.join(self.root, "build")

    @property
    def work(self):
        return os.path.join(self.root, "工作区")

    @property
    def ledger(self):
        """人账本体是库；分类表.csv 只是 init 的种子。"""
        return os.path.join(self.tables, "ledger.db")

    @property
    def ledger_seed(self):
        return os.path.join(self.tables, "分类表.csv")

    @property
    def inventory(self):
        return os.path.join(self.tables, "书目录清单.csv")

    @property
    def concepts(self):
        return os.path.join(self.tables, "概念表.tsv")

    @property
    def concepts_broad(self):
        return os.path.join(self.tables, "概念表-broad.tsv")

    @property
    def concepts_action(self):
        return os.path.join(self.tables, "概念表-动作词.tsv")

    @property
    def series(self):
        return os.path.join(self.tables, "系列")

    @property
    def library_db(self):
        return os.path.join(self.build, "library.db")

    @property
    def index_dir(self):
        return os.path.join(self.build, "检索")

    @property
    def index_db(self):
        return os.path.join(self.index_dir, "index.db")

    @property
    def catalog(self):
        return os.path.join(self.build, "书目总表.csv")

    @property
    def catalog_xlsx(self):
        return os.path.join(self.build, "书目总表.xlsx")

    @property
    def reading_index(self):
        return os.path.join(self.build, "阅读版索引.html")

    @property
    def views(self):
        return os.path.join(self.build, "views")

    @property
    def inbox(self):
        return os.path.join(self.books, INBOX)

    @property
    def cold(self):
        return os.path.join(self.books, COLD)

    def book(self, uid):
        return os.path.join(self.books, uid)

    def collection(self, cuid):
        return os.path.join(self.collections, cuid)

    def md(self, uid):
        return os.path.join(self.book(uid), "md")

    def html(self, uid):
        return os.path.join(self.book(uid), "html")
