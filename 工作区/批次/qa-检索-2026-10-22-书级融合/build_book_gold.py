#!/usr/bin/env python3
"""生成并校验 book_gold.tsv 的 30 道 pilot。

  build_book_gold.py          写 工作区/批次/qa-检索-2026-10-22-书级融合/book_gold.tsv
  build_book_gold.py --check  只校验，不写盘

普通层复用 60 题 gold 的 10 条，换书级口径。长书层与单章层是新写的，
每条都在目标章里逐字核对 evidence 才落盘。

列：qid question target_book target_chapters stratum type note path line evidence terms
target_book 与 target_chapters 都用 | 连接多个可接受项。terms 是用户会打的检索词，
逗号分隔，评测的实际查询就是它。
"""
import csv
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
GOLD = os.path.join(ROOT, "工作区", "批次", "qa-检索-2026-10-19", "gold.tsv")
OUT = os.path.join(HERE, "book_gold.tsv")
IDX = os.path.join(ROOT, "目录", "检索", "index.db")
LIB = os.path.join(ROOT, "目录", "library.db")

COLS = ["qid", "question", "target_book", "target_chapters", "stratum", "type",
        "note", "path", "line", "evidence", "terms"]

ORDINARY = ["q010", "q058", "q028", "q014", "q017", "q044", "q033", "q024",
            "q048", "q051"]

DUP_BOOKS = {"人文社科/人类学与考古/乡土中国": ["人文社科/人类学与考古/乡土中国（2）"]}
DUP_CHAPTERS = {"人文社科/人类学与考古/乡土中国":
                ["库/人文社科/人类学与考古/乡土中国（2）/md/11_礼治秩序.md"]}

LONG = [
    ("凡人修仙传", "md/2067_第十卷魔界之战第两千六十九章拦截.md",
     "凡人修仙传里，朱果儿是什么修为？", "原词",
     "你一个金丹期的小丫头", "长书层，网文", "朱果儿,修为,金丹期"),
    ("完美世界", "md/1017_第1030章驾临天人族祖地.md",
     "完美世界里，石昊在天人族祖城外面打算怎么对付出来的人？", "近义表达",
     "出来一个收拾一个", "长书层，网文", "石昊,天人族,收拾"),
    ("遮天", "md/0792_第787章叫板太古王族.md",
     "遮天里老疯子叫板太古王族时，一个人对上了一船什么人？", "原词",
     "老疯子一人独战一船祖王", "长书层，网文", "老疯子,祖王,太古王族"),
    ("诡秘之主", "md/1143_第一百九十一章历史中.md",
     "诡秘之主里克莱恩是怎么把那团魔药吞下去的？", "近义表达",
     "猛地往内吸气", "长书层，网文", "克莱恩,魔药,吞"),
    ("剑来", "md/1003_第996章云上琅琅杏花香.md",
     "剑来里米裕受魏檗所托去做什么？", "原词",
     "为长春宫出门历练的一行人秘密护道", "长书层，网文", "米裕,魏檗,护道"),
    ("鲁迅文学诗歌奖第一人：啸天说诗", "md/0169_石塘濑听猿.md",
     "《石塘濑听猿》这首诗写的是观赏之趣还是听觉之美？", "原词",
     "不是写观赏之趣，而是写听觉之美", "长书层，诗歌鉴赏", "石塘濑听猿,听觉,观赏"),
    ("金克木集", "md/695_空城对话.md",
     "空城计那场戏里，诸葛亮和司马懿在城楼上谈了什么？", "近义表达",
     "诸葛和司马在两军阵前面对面的谈话", "长书层，文集", "诸葛亮,司马懿,城楼"),
    ("史铁生作品全", "md/0972_八.md",
     "史铁生说文学的本分是什么？", "近义表达",
     "探问心魂的迷茫和意义才更是它的本分", "长书层，文集", "史铁生,文学,本分"),
    ("米兰·昆德拉作品系列", "md/0877_第二幕.md",
     "《雅克和他的主人》里刀鞘和小刀的故事在讲什么？", "原词",
     "上帝创造你，就是要让你插进好几个不同的刀鞘", "长书层，文集", "雅克,刀鞘,小刀"),
    ("海明威诞辰120周年图文珍藏版文集", "md/0026_正文.md",
     "《老人与海》里老人为什么让钓索勒在有老茧的部位？", "原词",
     "设法让钓索勒在有老茧的部位", "长书层，文集", "老人与海,钓索,老茧"),
]

SHORT = [
    ("中县干部", "md/中县干部.md",
     "中县干部里，干部考核为什么考不出真东西？", "原词",
     "因为县城太小，关系太复杂", "单章层，社科", "干部考核,考不出,县城"),
    ("中国神话史.袁珂1986", "md/中国神话史.袁珂1986.md",
     "句道兴写本《搜神记》残卷里最鲜明突出的是关于谁的神话？", "原词",
     "最鲜明突出的，是关于田章的一段神话", "单章层，神话史", "句道兴,搜神记,田章"),
    ("剑与电-角色扮演游戏设计艺术", "md/剑与电-角色扮演游戏设计艺术.md",
     "设计一个游戏世界，最该先说清玩家能干什么吗？", "无原词",
     "玩家用什么方式和你的世界交互", "单章层，游戏设计", "游戏世界,玩家,交互"),
    ("走遍中国 山西", "md/走遍中国 山西.md",
     "大同代王府的九龙壁有多大？", "原词",
     "其长45.50米", "单章层，导游", "大同,九龙壁,代王府"),
    ("解忧杂货店", "md/05_第五章来自天上的祈祷.md",
     "《解忧杂货店》里敦也他们觉得哪封信根本算不上烦恼？", "近义表达",
     "哪里算得上烦恼", "短书层，5 章", "解忧杂货店,鱼店,音乐"),
    ("到灯塔去", "md/01_窗.md",
     "《到灯塔去》里拉姆齐先生看一眼窗边的妻子和儿子，为什么就安心了？", "无原词",
     "他受到了鼓励，得到了满足", "短书层，3 章", "拉姆齐,窗边,妻子"),
    ("荒原狼", "md/03_哈里·哈勒的笔记.md",
     "《荒原狼》里哈里拧开收音机听到了什么？", "原词",
     "在慕尼黑演奏的亨德尔的《F大调大协奏曲》", "短书层，5 章", "荒原狼,哈里,收音机"),
    ("雷雨", "md/05_第四募.md",
     "《雷雨》里周萍说他和蘩漪的关系让他越来越怎么样？", "近义表达",
     "我越过越怕，越恨，越厌恶", "短书层，5 章", "周萍,蘩漪,厌恶"),
    ("余华/现实一种", "md/01_现实一种.md",
     "《现实一种》里山岗的妻子收拾好黑包，是让山岗去做什么？", "近义表达",
     "你该逃走了", "短书层，3 章", "山岗,黑包,逃走"),
    ("要有光  一位学者对中国青少年心理的调查, 一位母亲对孩子心灵的探寻",
     "md/04_在言谈之中.md",
     "《要有光》里王振判断花臂有什么障碍？", "原词",
     "花臂有严重的述情障碍", "短书层，4 章", "王振,花臂,述情障碍"),
]


ORD2 = [
    ("人文社科/人类学与考古/乡土中国", "md/10_礼治秩序.md",
     "礼治社会靠什么维持秩序，为什么不需要法律？", "原词",
     "礼和法不相同的地方是维持规范的力量", "普通层 v2，书级问法", "礼治,秩序,法律"),
    ("人文社科/心理与认知/乌合之众", "md/05_第二章群体的情感与道德.md",
     "为什么人在群体里容易冲动、容易被传染？", "原词",
     "群体的冲动性，流动性和易怒性", "普通层 v2，书级问法", "群体,冲动,传染"),
    ("文学/严肃文学小说/1984", "md/07_第7节.md",
     "为什么说无产者和牲口一样是自由的？", "原词",
     "无产者和牲口都是自由的", "普通层 v2，书级问法", "无产者,自由,牲口"),
    ("文学/严肃文学小说/动物农场", "md/21_◆◆◆ ◆◆◆.md",
     "动物农场里所有动物平等，为什么有些动物更平等？", "原词",
     "但是有些动物比别的动物更加平等", "普通层 v2，书级问法", "平等,动物,口号"),
    ("人文社科/历史/人类简史", "md/025_由想象所建构的秩序.md",
     "为什么几百个陌生人能靠共同想象合作？", "近义表达",
     "几百个陌生人就能够互相合作", "普通层 v2，书级问法", "陌生人,合作,想象"),
    ("人文社科/心理与认知/思考，快与慢", "md/047_第11章锚定效应在生活中随处可见.md",
     "随机数字为什么会影响人的估价？", "近义表达",
     "你应该记住它的名字：锚定效应", "普通层 v2，书级问法", "锚定,数字,估价"),
    ("人文社科/思想与哲学/苏菲的世界", "md/19_斯宾诺莎.md",
     "斯宾诺莎为什么说人没有自由意志？", "原词",
     "但他永远不可能获得‘自由意志’", "普通层 v2，书级问法", "斯宾诺莎,自由意志,必然"),
    ("人文社科/思想与哲学/刘擎西方现代思想讲义",
     "md/029_08_韦伯IV “现代的铁笼”是怎么锈就的.md",
     "韦伯说的现代铁笼是什么意思？", "原词",
     "被看作是机器的零件", "普通层 v2，书级问法", "韦伯,铁笼,官僚"),
    ("文学/严肃文学小说/加缪全集", "md/03_局外人（1942年）.md",
     "默尔索在母亲葬礼上没有哭，为什么后来被判死刑？", "近义表达",
     "好像我身上有什么东西爆裂开来", "普通层 v2，书级问法", "默尔索,葬礼,审判"),
    ("人文社科/心理与认知/认知觉醒",
     "md/11_第一节元认知：成长慢，是因为你不会“飞”.md",
     "元认知能力为什么让人成长更快？", "原词",
     "元认知能力就是我们习以为常、见怪不怪的反思", "普通层 v2，书级问法",
     "元认知,成长,反思"),
]


EXTRA_LONG = [
    ("文学/网络文学/驭房有术", "md/0107_第95章压运石（第二更）.md",
     "驭房有术里，谁被怀疑为了上位而排挤正职？", "近义表达",
     "那肯定是康永华了", "长书层补充，网文", "康永华,副职,正职"),
    ("文学/类型小说/黄易作品大全集", "md/1348_第七章四路进军.md",
     "黄易作品里，平州城内的契丹兵力有多少？", "原词",
     "总兵力约在一万二千人间", "长书层补充，类型小说", "平州,契丹,兵力"),
    ("文学/严肃文学小说/阿来经典作品集：共36册", "md/0200_5.md",
     "阿来作品里，老人为什么反对年轻人做木头生意？", "近义表达",
     "发完木头财就拍屁股走人了", "长书层补充，文集", "木头生意,老人,树林"),
    ("文学/网络文学/十日终焉", "md/783_第783章内向和社恐.md",
     "《十日终焉》里的生肖是什么变成的？", "原词",
     "曾经都是有血有肉的人", "长书层补充，网文", "生肖,地鼠,人"),
    ("文学/散文随笔/施蛰存全集北山散文集", "md/503_《奉和晦日幸昆明池应制》讲解.md",
     "施蛰存说宋之问那首诗的结尾做到了什么？", "原词",
     "言尽意不尽", "长书层补充，散文", "宋之问,结尾,言尽意不尽"),
    ("文学/草婴译著全集", "md/345_哈吉穆拉特.md",
     "草婴译著里，布特勒为什么借犹太人的高利贷？", "原词",
     "借了犹太人的高利贷，还清了赌债", "长书层补充，文集", "布特勒,高利贷,赌债"),
    ("文学/严肃文学小说/莫言作品", "md/472_地震.md",
     "莫言《地震》里，老太婆看出儿子画的是什么？", "近义表达",
     "我瞅着你画了块西瓜地", "长书层补充，文集", "地震,西瓜地,画"),
    ("文学/严肃文学小说/奥尔加·托卡尔丘克作品集", "md/373_二.md",
     "奥尔加说第一人称叙事像在编织什么？", "近义表达",
     "编织一种与众不同的花纹", "长书层补充，文集", "第一人称,叙事,花纹"),
    ("文学/严肃文学小说/你在高原", "md/102_寂寥之春.md",
     "《你在高原》里李咪是谁带来的客人？", "原词",
     "那是李贵字带来的客人", "长书层补充，小说", "李咪,客人,聚会"),
    ("文学/诗歌/鲁迅文学诗歌奖第一人：啸天说诗", "md/0188_哀江南赋（节）并序.md",
     "《哀江南赋》里哪两句被王若虚批评为堆垛故实？", "原词",
     "堆垛故实以寓时事", "长书层补充，诗歌鉴赏", "哀江南赋,王若虚,堆垛故实"),
]

EXTRA_SHORT = [
    ("文学/严肃文学小说/冬天里的春天", "md/05_第五章.md",
     "《冬天里的春天》里，他们今天被安排到哪个单位接受批判？", "原词",
     "今天是锻压中心", "短书层补充，5 章", "锻压中心,批判,检查"),
    ("文学/儿童与青少年文学/魔女宅急便 1-5",
     "md/02_第二卷、魔女宅急便２琪琪的新魔法.md",
     "《魔女宅急便》里琪琪出门前在门口捡到了什么？", "原词",
     "是一个用细绳十字交叉捆起来的包裹", "短书层补充，5 章", "琪琪,包裹,信"),
    ("文学/列夫·托尔斯泰文集", "md/02_第一部.md",
     "托尔斯泰文集里，皮埃尔跟着安娜·米哈伊洛夫娜走进的那扇门通向哪里？", "原词",
     "这扇门正对着后门的过厅", "短书层补充，4 章", "皮埃尔,安娜,过厅"),
    ("文学/严肃文学小说/黑铁时代", "md/03_第二章.md",
     "《黑铁时代》里“我”为什么上了救护车？", "近义表达",
     "伤口又重新打开了", "短书层补充，5 章", "救护车,伤口,血"),
    ("文学/类型小说/东野圭吾/伽利略的苦恼", "md/05_第五章扰乱.md",
     "《伽利略的苦恼》里恶魔之手让哪些活动被迫中止？", "原词",
     "音乐会、演出等文娱活动被迫中止", "短书层补充，5 章", "恶魔之手,恐吓,音乐会"),
    ("文学/类型小说/东野圭吾/毕业", "md/01_第一章.md",
     "《毕业》里沙都子在看台上看到波香被谁压制住？", "近义表达",
     "对手的突然快攻让波香措手不及", "短书层补充，5 章", "波香,剑道,比赛"),
    ("文学/严肃文学小说/长路", "md/02_长路.md",
     "《长路》里孩子发烧后父亲做了什么？", "近义表达",
     "他抱孩子回到火边", "短书层补充，3 章", "孩子,发烧,火"),
    ("文学/散文随笔/喜剧的本质", "md/02_第二章情境的喜剧性和语言的喜剧性.md",
     "《喜剧的本质》里，亚历山大·贝恩用什么词定义喜剧的一般概念？", "原词",
     "贬低（dégradation）", "短书层补充，4 章", "贝恩,贬低,喜剧"),
    ("文学/严肃文学小说/余华/鲜血梅花", "md/02_古典爱情.md",
     "《鲜血梅花》里柳生在楼下看到一根绳子从窗口怎样？", "近义表达",
     "一根绳子缓缓而下", "短书层补充，5 章", "柳生,绳子,窗口"),
    ("文学/严肃文学小说/余华/我胆小如鼠", "md/02_夏季台风.md",
     "《我胆小如鼠》里吴全从街上带回了什么消息？", "近义表达",
     "地震马上就要发生了", "短书层补充，3 章", "吴全,地震,广播"),
]

EXTRA_ORD = [
    ("人文社科/盐铁论", "md/20_相刺第二十.md",
     "《盐铁论》里文学说公卿的职责是什么？", "近义表达",
     "公卿者，四海之表仪", "普通层补充，政论", "公卿,表仪,职责"),
    ("人文社科/政治与法律/圆圈正义", "md/30_侠客心法治路.md",
     "罗翔说亚里士多德认为法治包含哪两重意义？", "原词",
     "已成立的法律获得普遍的服从", "普通层补充，法学", "法治,亚里士多德,良法"),
    ("人文社科/历史/万历十五年", "md/08_第三章世间已无张居正.md",
     "《万历十五年》里给事中参劾申时行，说他是什么样的人？", "近义表达",
     "两面派和卖友误君的小人", "普通层补充，历史", "申时行,参劾,给事中"),
    ("人文社科/人类学与考古/枪炮、病菌与钢铁", "md/09_第7章杏仁的前世今生.md",
     "《枪炮、病菌与钢铁》里，作物为什么会失去抑制发芽的机制？", "近义表达",
     "种子少了厚实的外层或没有抑制发芽的机制", "普通层补充，人类学", "作物,发芽,突变"),
    ("人文社科/历史/邓小平时代", "md/12_第8章为自由设限：1978–1979.md",
     "《邓小平时代》里，哪类干部一般赞成较多的言论自由？", "近义表达",
     "主管科学、高教、青年事务和统战工作的干部", "普通层补充，历史", "言论自由,干部,限制"),
    ("人文社科/政治与法律/中国历代政治得失", "md/10_三、汉代经济制度.md",
     "《中国历代政治得失》里，董仲舒主张用什么办法限制土地兼并？", "原词",
     "董仲舒曾主张限田政策", "普通层补充，制度史", "董仲舒,限田,土地"),
    ("人文社科/思想与哲学/曼德拉/理想国译丛/历史的终结与最后的人",
     "md/15_第12章不存在没有民主人的民主.md",
     "福山说现代化理论在什么前提下才有效？", "近义表达",
     "只当人受经济增长和工业理性的命令支配之时", "普通层补充，政治哲学",
     "现代化理论,经济,民主"),
    ("人文社科/心理与认知/爱的艺术", "md/13_艾里希·弗洛姆生命中的爱.md",
     "《爱的艺术》作者弗洛姆除了做治疗师，还积极参与什么？", "近义表达",
     "迫切需要干预政治", "普通层补充，心理学", "弗洛姆,政治,冷战"),
    ("人文社科/历史/显微镜下的大明", "md/14_第五章帝国一角的繁荣性崩塌.md",
     "《显微镜下的大明》里，后湖黄册库二十九年收了多少罚银？", "原词",
     "十六万八千五百四十六两", "普通层补充，历史", "黄册库,罚银,后湖"),
    ("人文社科/社会与经济/曼德拉/理想国译丛/大断裂：人类本性与社会秩序的重建",
     "md/20_第16章过去、现在与未来的重建.md",
     "《大断裂》里，美国靠什么把移民儿童整合进社会？", "近义表达",
     "由民主政治理想和盎格鲁—撒克逊文化传统所形成的“美国化”", "普通层补充，社会学",
     "移民,公民认同,美国化"),
]


def find_book(con, like):
    exact = con.execute("SELECT DISTINCT book FROM para WHERE book = ?",
                        (like,)).fetchall()
    if len(exact) == 1:
        return exact[0][0]
    rows = con.execute("SELECT DISTINCT book FROM para WHERE book LIKE ?",
                       ("%" + like,)).fetchall()
    if len(rows) != 1:
        raise SystemExit("书定位不唯一：%s %r" % (like, rows))
    return rows[0][0]


def anchor(chapter_path, evidence):
    full = os.path.join(ROOT, chapter_path)
    with open(full, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if evidence in line:
                return i
    raise SystemExit("evidence 不在章里：%s %r" % (chapter_path, evidence))


def ordinary_rows(con):
    uid2book = dict(sqlite3.connect("file:%s?mode=ro" % LIB, uri=True).execute(
        "SELECT uid, path FROM book"))
    gold = {r["qid"]: r for r in csv.DictReader(open(GOLD, encoding="utf-8-sig"),
                                                delimiter="\t")}
    out = []
    for qid in ORDINARY:
        g = gold[qid]
        book = uid2book[g["uid"]]
        books = [book] + DUP_BOOKS.get(book, [])
        chapters = [g["path"]] + DUP_CHAPTERS.get(qid, [])
        out.append({"qid": qid, "question": g["question"],
                    "target_book": "|".join(books),
                    "target_chapters": "|".join(chapters),
                    "stratum": "ordinary", "type": g["category"],
                    "note": "复用 gold %s，%s" % (qid, g["concept"]),
                    "path": g["path"], "line": g["line"], "evidence": g["evidence"],
                    "terms": g["terms"]})
    return out


def new_rows(con, specs, stratum, prefix, start=1):
    out = []
    for i, (like, rel, question, qtype, evidence, note, terms) in enumerate(specs, start):
        book = find_book(con, like)
        chapter = "库/%s/%s" % (book, rel)
        line = anchor(chapter, evidence)
        out.append({"qid": prefix + "%02d" % i, "question": question,
                    "target_book": "|".join([book] + DUP_BOOKS.get(book, [])),
                    "target_chapters": "|".join([chapter] + DUP_CHAPTERS.get(book, [])),
                    "stratum": stratum, "type": qtype,
                    "note": note, "path": chapter, "line": line, "evidence": evidence,
                    "terms": terms})
    return out


EXPECTED = {"long": 10, "short": 10, "ordinary": 10}


def check(rows, con, libdb=LIB):
    lib = sqlite3.connect("file:%s?mode=ro" % libdb, uri=True)
    chapters = dict(lib.execute(
        "SELECT b.path, d.chapters FROM book b JOIN derived d ON d.uid=b.uid "
        "AND d.kind='md' WHERE d.chapters > 0"))
    indexed_books = {r[0] for r in con.execute("SELECT DISTINCT book FROM para")}
    indexed_paths = {r[0] for r in con.execute("SELECT DISTINCT path FROM para")}
    errs = []
    qids = [r["qid"] for r in rows]
    if len(qids) != len(set(qids)):
        errs.append("qid 不重复失败")
    per = {}
    for r in rows:
        per[r["stratum"]] = per.get(r["stratum"], 0) + 1
    n_each = len(rows) // 3
    for s in EXPECTED:
        if per.get(s) != n_each:
            errs.append("层 %s 应 %d 行，实得 %s" % (s, n_each, per.get(s)))
    if len(rows) % 3:
        errs.append("总行数不能被 3 整除：%d" % len(rows))
    for r in rows:
        books = r["target_book"].split("|")
        chaps = r["target_chapters"].split("|")
        for b in books:
            if b not in indexed_books:
                errs.append("%s 目标书不在索引：%s" % (r["qid"], b))
            n = chapters.get(b, 0)
            if r["stratum"] == "long" and n < 500:
                errs.append("%s long 层但只有 %d 章" % (r["qid"], n))
            if r["stratum"] == "short" and n > 5:
                errs.append("%s short 层但有 %d 章" % (r["qid"], n))
            if r["stratum"] == "ordinary" and not (6 <= n <= 499):
                errs.append("%s ordinary 层但有 %d 章" % (r["qid"], n))
        for c in chaps:
            if c not in indexed_paths:
                errs.append("%s 目标章不在索引：%s" % (r["qid"], c))
        if not any(c.startswith("库/%s/" % b) for c in chaps for b in books):
            errs.append("%s 目标章不属于目标书" % r["qid"])
        if r["path"] not in chaps:
            errs.append("%s 证据锚点不在可接受章里" % r["qid"])
        full = os.path.join(ROOT, r["path"])
        line = ""
        if os.path.exists(full):
            lines = open(full, encoding="utf-8").read().split("\n")
            n = int(r["line"])
            line = lines[n - 1] if 0 < n <= len(lines) else ""
        if r["evidence"] not in line:
            errs.append("%s evidence 不在锚点行：%s:%s" % (r["qid"], r["path"], r["line"]))
        if len([t for t in r["terms"].split(",") if t.strip()]) < 1:
            errs.append("%s terms 为空" % r["qid"])
    if len(rows) not in (30, 60):
        errs.append("总行数应为 30 或 60，实得 %d" % len(rows))
    share = sum(1 for b in indexed_books if chapters.get(b, 0) >= 500)
    print("行数 %d，分层 %s" % (len(rows), per))
    print("全库基准 long_share = %d/%d = %.4f" % (share, len(indexed_books),
                                                   share / max(1, len(indexed_books))))
    if errs:
        for e in errs:
            print("FAIL", e)
        return 1
    print("PASS %d 行，%s，qid 不重复，目标书在索引，目标章在索引，证据在锚点行"
          % (len(rows), per))
    return 0


def main():
    con = sqlite3.connect("file:%s?mode=ro" % IDX, uri=True)
    if "--v1" in sys.argv:
        GOLD_OUT = OUT.replace("book_gold.tsv", "book_gold-v1.tsv")
        rows = ordinary_rows(con) + new_rows(con, LONG, "long", "L") + \
            new_rows(con, SHORT, "short", "S")
    else:
        GOLD_OUT = OUT
        rows = new_rows(con, ORD2, "ordinary", "O") + \
            new_rows(con, LONG, "long", "L") + new_rows(con, SHORT, "short", "S")
        if "--size" in sys.argv and "60" in sys.argv:
            rows += new_rows(con, EXTRA_ORD, "ordinary", "O", start=11) + \
                new_rows(con, EXTRA_LONG, "long", "L", start=11) + \
                new_rows(con, EXTRA_SHORT, "short", "S", start=11)
    rows.sort(key=lambda r: ({"ordinary": 0, "long": 1, "short": 2}[r["stratum"]], r["qid"]))
    rc = check(rows, con)
    if "--check" in sys.argv:
        return rc
    if rc:
        return rc
    with open(GOLD_OUT + ".tmp", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    os.replace(GOLD_OUT + ".tmp", GOLD_OUT)
    print("写入 %s" % os.path.relpath(GOLD_OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
