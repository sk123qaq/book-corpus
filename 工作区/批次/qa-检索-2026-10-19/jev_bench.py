#!/usr/bin/env python3
"""Jev 侧对照：同一批章节，用 Noul 判「是否实质讨论」，看单章 token 与判得准不准。"""
import json, os, re, sys, time, urllib.request
ROOT = os.path.expanduser("~/文档/books")
ENV = "/home/shike/.agents/skills/typesafe-ai/.env"
def key():
    for line in open(ENV):
        if line.startswith("TYPESAFE_API_KEY="): return line.split("=",1)[1].strip()
TARGETS = {
 "liuqing029": "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/029_08_韦伯IV “现代的铁笼”是怎么锈就的.md",
 "liuqing072": "库/人文社科/思想与哲学/刘擎西方现代思想讲义/md/072_思考的含义：独立判断.md",
 "wuhe05": "库/人文社科/心理与认知/乌合之众/md/05_第二章群体的情感与道德.md",
 "farm21": "库/文学/严肃文学小说/动物农场/md/21_◆◆◆ ◆◆◆.md",
 "n1984_08": "库/文学/严肃文学小说/1984/md/08_第8节.md",
 "xiangtu10": "库/人文社科/人类学与考古/乡土中国/md/10_礼治秩序.md",
 "churchill107": "库/文学/散文随笔/丘吉尔世界大战丛书（第一次世界大战回忆录、第二次世界大战回忆录 共计17册） (温斯顿•丘吉尔 (Winston Chu/md/107_第十六章爱尔兰自由邦的兴起.md",
 "camus03": "库/文学/严肃文学小说/加缪全集/md/03_局外人（1942年）.md",
 "ming03": "库/人文社科/历史/当年明月/明朝那些事儿/第壹部朱元璋：从和尚到皇帝/md/03_第三章踏上征途.md",
}
def clean(rel, limit):
    t=open(os.path.join(ROOT,rel),encoding="utf-8",errors="ignore").read()
    if t.startswith("---"):
        i=t.find("\n---",3)
        if i>0: t=t[i+4:]
    return re.sub(r"\s+"," ",t).strip()[:limit]
def post(body):
    req=urllib.request.Request("https://api.typesafe.ai/v1/systemone",
        data=json.dumps(body,ensure_ascii=False).encode(),
        headers={"Authorization":"Bearer "+key(),"Content-Type":"application/json"})
    t0=time.time()
    with urllib.request.urlopen(req,timeout=180) as r: d=json.load(r)
    return d, time.time()-t0
FREE = {"type":"noul",
        "instructions":{"text":"`chapters[{i}].text`","q":"上面这段文字是否在【实质讨论】人的自主与自由如何被外部秩序压制（制度、规范、监视、群体、权力）？仅是出现「自由」二字（如「自由邦」这种专有名词）不算。"},
        "criteria":{"true":"在讨论自主/自由被压制的机制","false":"只是叙述事件或专有名词用法"}}
def run(limit, n, tag):
    ids=list(TARGETS)[:n]
    state={"chapters":[{"id":c,"text":clean(TARGETS[c],limit)} for c in ids]}
    qs={}
    for i,c in enumerate(ids):
        q=json.loads(json.dumps(FREE))
        q["instructions"]={"text":f"`chapters[{i}].text`","q":FREE["instructions"]["q"]}
        qs["free_"+c]=q
    d,dt=post({"state":state,"model":"jev-latest","questions":qs})
    u=d["usage"]
    return {"tag":tag,"limit":limit,"n":n,"in":u["input_tokens"],"out":u["output_tokens"],
            "sec":round(dt,2),"per_ch":round((u["input_tokens"]+u["output_tokens"])/n),
            "model":d.get("model"),"ans":{k:v.get("noul") for k,v in d["answers"].items()},
            "unc":{k:round(v.get("confidence",0),2) for k,v in d["answers"].items()}}
if __name__=="__main__":
    out=[]
    for tag,limit,n in [("A-1200字/9章",1200,9),("B-2400字/9章",2400,9),("C-1200字/20章",1200,20)]:
        try:
            r=run(limit,n,tag); out.append(r)
            print(f"{tag}: model={r['model']} in={r['in']} out={r['out']} 每章≈{r['per_ch']}tok {r['sec']}s")
            if n==9:
                for k,v in r["ans"].items(): print(f"    {k:<18}{v:.2f}")
        except Exception as e:
            print(tag,"失败",e)
    json.dump(out,open("jev_results.json","w"),ensure_ascii=False,indent=1)
