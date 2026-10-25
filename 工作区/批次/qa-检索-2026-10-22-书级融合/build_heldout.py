#!/usr/bin/env python3
"""生成并校验 B2 新普通层 20 题（6-499 章，盲写，不参与调参）。"""
import csv, hashlib, os, sqlite3, sys
HERE=os.path.dirname(os.path.abspath(__file__)); ROOT=os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT,"工作区","脚本"))
import book_score
ROWS=[
("H01","十字军东征的主要作用被比喻成什么？","人文社科/历史/曼德拉/理想国译丛/圣经与利剑：英国和巴勒斯坦——从青铜时代到贝尔福宣言","库/人文社科/历史/曼德拉/理想国译丛/圣经与利剑：英国和巴勒斯坦——从青铜时代到贝尔福宣言/md/07_第4章十字军东征.md","十字军东征的主要作用是作为“基督教世界的下水道，排走所有的不和谐”","十字军东征,下水道,不和谐"),
("H02","皮萨罗在卡哈马卡为什么能俘虏阿塔瓦尔帕？","人文社科/人类学与考古/枪炮、病菌与钢铁：人类社会的命运","库/人文社科/人类学与考古/枪炮、病菌与钢铁：人类社会的命运/md/06_第三章卡哈马卡的冲突.md","皮萨罗就俘虏了阿塔瓦尔帕","皮萨罗,阿塔瓦尔帕,卡哈马卡"),
("H03","1950年代批判资产阶级文艺时，为什么把色情和性当作低级本能？","人文社科/社会与经济/新中国性话语研究","库/人文社科/社会与经济/新中国性话语研究/md/03_第二节对性的批判和扫荡.md","批判的重心在于它迎合人的低级本能，色情和性就是典型的低级本能","资产阶级文艺,色情,低级本能"),
("H04","袁珂认为神话的起源比杨堃的推断更早吗？","人文社科/宗教与神话/中国神话通论","库/人文社科/宗教与神话/中国神话通论/md/002_二神话的起源.md","我的设想则在原始社会前期，即蒙昧期的中级阶段","神话,起源,杨堃"),
("H05","欧几里得几何为什么不能用来测量地球表面的三角形？","科学技术/物理与天文/史蒂芬·霍金/宇宙系列/黑洞战争","库/科学技术/物理与天文/史蒂芬·霍金/宇宙系列/黑洞战争/md/07_第3章非欧几何.md","如果欧几里得真的不辞辛苦地去测量地球表面上的三角形，他会发现欧几里得几何学是不能用的","欧几里得,球面,三角形"),
("H06","金字塔原理的纵向结构要求每层观点回答什么？","商业与管理/金字塔原理/2","库/商业与管理/金字塔原理/2/md/07_练习2 建立纵向的关系.md","纵向的结构一定要提出“疑问—回答”式对话","金字塔,纵向,疑问"),
("H07","企业衰落第三阶段的核心表现是什么？","商业与管理/吉姆·柯林斯/再造卓越","库/商业与管理/吉姆·柯林斯/再造卓越/md/08_03 企业衰落第三阶段漠视危机.md","企业衰落第三阶段漠视危机","衰落,第三阶段,漠视危机"),
("H08","保罗在第六章里为什么觉得前线像兽笼？","文学/严肃文学小说/西线无战事","库/文学/严肃文学小说/西线无战事/md/07_VI.md","前线是只兽笼。笼中人只能紧张地等待将要发生的事","前线,兽笼,偶然"),
("H09","保润一家为什么去井亭医院缴赔款？","文学/严肃文学小说/苏童/黄雀记","库/文学/严肃文学小说/苏童/黄雀记/md/06_井亭医院.md","我是去井亭医院缴赔款的，不缴不行了","井亭医院,赔款,树"),
("H10","第十章里探长向斯塔克韦瑟追问什么动作？","文学/类型小说/阿加莎·克里斯蒂/意外来客","库/文学/类型小说/阿加莎·克里斯蒂/意外来客/md/11_第十章.md","你有把手放在这张桌子上，然后把窗户打开吗？","探长,斯塔克韦瑟,窗户"),
("H11","名侦探的守则第二章开头，牛神邸发生了什么事件？","文学/类型小说/东野圭吾/名侦探的守则","库/文学/类型小说/东野圭吾/名侦探的守则/md/03_第二章意外的凶手：谁杀了他.md","牛神邸发生了杀人事件","牛神邸,杀人,事件"),
("H12","余华为什么说儿子第一次因为心理原因而哭泣？","文学/散文随笔/余华/没有一条道路是重复的","库/文学/散文随笔/余华/没有一条道路是重复的/md/03_恐惧与成长.md","这一次他终于因为心理的原因而哭泣了","恐惧,成长,儿子"),
("H13","大雁阿卡为什么不肯放慢速度等白鹅？","文学/儿童与青少年文学/尼尔斯骑鹅旅行记","库/文学/儿童与青少年文学/尼尔斯骑鹅旅行记/md/006_傍晚.md","跟他讲，跟不上队伍可以回家去","阿卡,白鹅,大雁"),
("H14","汤川用什么道具向草薙演示物理实验？","文学/类型小说/东野圭吾/嫌疑人X的献身","库/文学/类型小说/东野圭吾/嫌疑人X的献身/md/05_第五章.md","这可是高中物理的实验道具","汤川,物理,实验"),
("H15","莉拉和莱农为什么爬上堂·阿奇勒家的楼梯？","文学/严肃文学小说/我的天才女友","库/文学/严肃文学小说/我的天才女友/md/06_童年｜堂·阿奇勒的故事｜.md","那次，我和莉拉决定爬上那段阴暗的楼梯","莉拉,堂·阿奇勒,楼梯"),
("H16","冬泳开头，主人公在咖啡厅等谁？","文学/严肃文学小说/冬泳","库/文学/严肃文学小说/冬泳/md/05_冬泳.md","我跟隋菲约在咖啡厅见面","隋菲,咖啡厅,等待"),
("H17","蛙第五章开头，家里为什么议论姑姑的婚事？","文学/严肃文学小说/蛙","库/文学/严肃文学小说/蛙/md/08_第五章.md","姑姑早就到了谈婚论嫁的年龄","姑姑,婚事,提亲"),
("H18","王小波为什么说插队时最大的痛苦是没有书看？","文学/散文随笔/王小波/沉默的大多数","库/文学/散文随笔/王小波/沉默的大多数/md/03_思维的乐趣.md","最大的痛苦是没有书看","插队,书,思想"),
("H19","哈桑在第七章开头给阿米尔讲了一个什么梦？","文学/严肃文学小说/追风筝的人","库/文学/严肃文学小说/追风筝的人/md/07_第七章.md","我们在喀尔卡湖，你，我，爸爸","哈桑,梦,湖"),
("H20","顺治十三年太后圣寿节上，福临为什么显得失望？","文学/严肃文学小说/少年天子","库/文学/严肃文学小说/少年天子/md/06_第三章.md","脸上一团失望","太后,圣寿节,福临"),
]
def main():
    con=sqlite3.connect("file:%s?mode=ro"%book_score.DB,uri=True)
    indexed={r[0] for r in con.execute("SELECT DISTINCT path FROM para")}
    out=[]
    for qid,q,book,path,evidence,terms in ROWS:
        full=os.path.join(ROOT,path)
        if not os.path.exists(full): raise SystemExit("缺章 %s"%path)
        lines=open(full,encoding="utf-8",errors="ignore").read().split("\n")
        line=next((i for i,x in enumerate(lines,1) if evidence in x),0)
        if not line: raise SystemExit("找不到证据 %s %s"%(qid,evidence[:30]))
        if path not in indexed: raise SystemExit("章不在索引 %s"%path)
        meta=book_score.load_meta().get(book)
        if not meta or not (6<=meta.chapters<=499): raise SystemExit("章数不对 %s %s"%(book,meta.chapters if meta else None))
        out.append({"qid":qid,"question":q,"target_book":book,"target_chapters":path,
                    "stratum":"ordinary","type":"heldout","note":"B2 新普通层，盲写，不参与调参",
                    "path":path,"line":line,"evidence":evidence,"terms":terms})
    path=os.path.join(HERE,"book_gold-heldout.tsv")
    with open(path+".tmp","w",encoding="utf-8",newline="") as fh:
        w=csv.DictWriter(fh,fieldnames=list(out[0]),delimiter="\t",lineterminator="\n")
        w.writeheader(); w.writerows(out)
    os.replace(path+".tmp",path)
    sha=hashlib.sha256(open(path,"rb").read()).hexdigest()
    with open(os.path.join(HERE,"book_gold-heldout.freeze.txt"),"w",encoding="utf-8") as fh:
        fh.write("book_gold-heldout.tsv\nrows 20\nstratum ordinary\nchapters 6-499\nsha256 %s\ndate 2026-10-23\n"%sha)
    print("wrote",path,"rows",len(out),"sha256",sha)
    for r in out: print(r["qid"],r["line"],r["target_book"])
    con.close()
if __name__=="__main__": main()
