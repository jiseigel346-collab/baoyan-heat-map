# -*- coding: utf-8 -*-
"""从 OCR 缓存重建 2026 江苏招生计划：院校 -> 专业组 -> 专业。
输出 /tmp/zhuankan_parsed.json （专业级明细 + 专业组级 + 校验）。
"""
import json, glob, re, os, sys

CACHE = "/tmp/ocr_cache"
DATA_TOP = 292
LEN_SET = set("二三四五二三四五") | {"两年","二年","三年","四年","五年","2","3","4","5"}
LEN_CHARS = set("二三四五")

def classify(hdr):
    km = "历史" if "历史等科目类" in hdr else ("物理" if "物理等科目类" in hdr else "")
    if "艺术" in hdr: cat = "艺术类"
    elif "体育" in hdr: cat = "体育类"
    elif "提前" in hdr: cat = "提前录取本科"
    elif "专科" in hdr or "高职" in hdr: cat = "专科"
    elif "本科院校" in hdr or "本科" in hdr: cat = "本科批"
    else: cat = ""
    return km, cat

def field_of(nx):
    if nx < 1094:
        if nx < 213: return ("L","code")
        if nx < 882: return ("L","name")
        if nx < 947: return ("L","plan")
        if nx < 1002: return ("L","len")
        return ("L","fee")
    else:
        if nx < 1200: return ("R","code")
        if nx < 1862: return ("R","name")
        if nx < 1925: return ("R","plan")
        if nx < 1978: return ("R","len")
        return ("R","fee")

def parse_page(d):
    w = d["w"]; f = w/2188.0
    hdr = " ".join(t[4] for t in d["items"] if t[1] < 160*f)
    km, cat = classify(hdr)
    # 仅按 x 分 code / name / numeric(计划+学制+学费合并) 三大区
    B = {(s,k):[] for s in "LR" for k in ("code","name","num")}
    for x0,y0,x1,y1,t,c in d["items"]:
        if y0 < DATA_TOP*f: continue
        nx = x0/f
        if nx < 1094:
            if nx < 213: s,k="L","code"
            elif nx < 858: s,k="L","name"
            else: s,k="L","num"
        else:
            if nx < 1200: s,k="R","code"
            elif nx < 1838: s,k="R","name"
            else: s,k="R","num"
        B[(s,k)].append((y0,x0,t,c))
    seq = []
    for side in "LR":
        codes=[]
        for y,x,t,c in sorted(B[(side,"code")]):
            dg=re.sub(r"\D","",t)
            if dg: codes.append((y,dg,c))
        names=sorted(B[(side,"name")])
        nums =sorted(B[(side,"num")])
        for i,(y,code,cc) in enumerate(codes):
            ylo=y-16*f
            yhi=codes[i+1][0]-16*f if i+1<len(codes) else 1e9
            nm=" ".join(t for (yy,xx,t,c) in names if ylo<=yy<yhi).strip()
            zone=sorted([(xx/f,t) for (yy,xx,t,c) in nums if ylo<=yy<yhi])
            ln=""; fee=""; plan=""
            small=[]   # (nx, value<1000)
            big=[]     # value>=1000 (学费)
            for nx,t in zone:
                if any(ch in LEN_CHARS for ch in t) and not ln:
                    ln="".join(ch for ch in t if ch in LEN_CHARS)[:1]
                dg=re.sub(r"\D","",t)
                if not dg: continue
                v=int(dg)
                if v>=1000: big.append(v)
                else: small.append((nx,v))
            if big: fee=str(max(big))
            if small:
                small.sort()           # 最靠左的小数=计划数
                plan=str(small[0][1])
            seq.append({"code":code,"name":nm,"plan":plan,"len":ln,"fee":fee,"cc":cc,"flag":""})
    return km, cat, seq

def num(s):
    s=re.sub(r"\D","",str(s))
    return int(s) if s else None

def is_major(rec):
    ln = rec["len"]; fee = rec["fee"]
    has_len = any(ch in LEN_CHARS for ch in ln) or ln.strip() in {"2","3","4","5"}
    has_fee = num(fee) is not None and num(fee) >= 1000
    return has_len or has_fee

def main():
    files = sorted(glob.glob(f"{CACHE}/page_*.json"))
    rows = []          # 专业级
    groups = {}        # key=(km,cat,school_code,grp_code) -> dict
    cur_km=cur_cat=None
    cur_school=cur_school_code=None
    cur_grp_code=cur_grp_name=cur_grp_select=None
    pages_data=0
    school_map={}
    for fpath in files:
        d=json.load(open(fpath)); pg=d["page"]+1
        km,cat,seq = parse_page(d)
        if not km and not cat:   # 非数据页
            continue
        # 段切换时重置院校/组状态
        if (km,cat)!=(cur_km,cur_cat):
            cur_km,cur_cat=km,cat
            cur_school=cur_school_code=cur_grp_code=cur_grp_name=cur_grp_select=None
        if km and cat: pages_data+=1
        for rec in seq:
            code=rec["code"]; name=rec["name"]
            if is_major(rec):
                rows.append({
                    "km":cur_km,"cat":cur_cat,
                    "school_code":cur_school_code,"school":cur_school,
                    "grp_code":cur_grp_code,"grp_select":cur_grp_select,"grp_name":cur_grp_name,
                    "sp_code":code,"sp_name":name,
                    "plan":num(rec["plan"]),"len":rec["len"].strip(),
                    "fee":num(rec["fee"]),"page":pg,"cc":rec["cc"],
                    "flag":rec.get("flag",""),
                })
            else:
                # 表头：院校 或 专业组
                if "专业组" in name or (len(code)>=5):
                    cur_grp_code=code[:6] if len(code)>=6 else code
                    cur_grp_name=name
                    if len(cur_grp_code)>=6 and not cur_school_code:
                        cur_school_code=cur_grp_code[:4]
                    m=re.search(r"专业组[（(]([^（）()]*)", name)
                    cur_grp_select=m.group(1) if m else ""
                    gk=(cur_km,cur_cat,cur_school_code,cur_grp_code)
                    groups[gk]={"km":cur_km,"cat":cur_cat,"school_code":cur_school_code,
                                "school":cur_school,"grp_code":cur_grp_code,
                                "grp_select":cur_grp_select,"grp_name":cur_grp_name,
                                "grp_plan":num(rec["plan"]),"page":pg}
                elif len(code)==4:
                    cur_school_code=code; cur_school=name
                    cur_grp_code=cur_grp_name=cur_grp_select=None
                    if name and len(name)>=2:
                        school_map[code]=name
                else:
                    # 其它表头（少见），并入组名
                    pass
    # 院校代号回填（来自专业组代号前4位）
    for r in rows:
        if not r["school_code"] and r["grp_code"] and len(r["grp_code"])>=4:
            r["school_code"]=r["grp_code"][:4]
    for g in groups.values():
        if not g["school_code"] and g["grp_code"] and len(g["grp_code"])>=4:
            g["school_code"]=g["grp_code"][:4]
    # 每个院校代号取“最完整的中文名”作为权威名（覆盖空/纯数字/过短）
    import re as _re
    def cn_len(s): return len(_re.findall(r"[\u4e00-\u9fa5]", s or ""))
    best={}
    for code,nm in school_map.items():
        if cn_len(nm)>cn_len(best.get(code,"")): best[code]=nm
    for r in rows:
        nm=r.get("school") or ""
        if cn_len(nm)>cn_len(best.get(r["school_code"],"")): best[r["school_code"]]=nm
    for r in rows:
        if best.get(r["school_code"]): r["school"]=best[r["school_code"]]
    for g in groups.values():
        if best.get(g["school_code"]): g["school"]=best[g["school_code"]]
    # 校验
    grp_sum={}
    for r in rows:
        gk=(r["km"],r["cat"],r["school_code"],r["grp_code"])
        grp_sum[gk]=grp_sum.get(gk,0)+(r["plan"] or 0)
    chk=0
    for gk,g in groups.items():
        s=grp_sum.get(gk,0)
        g["sp_plan_sum"]=s
        g["match"]= (g["grp_plan"]==s) if g["grp_plan"] is not None else None
        if g["match"] is False: chk+=1
    out={"rows":rows,"groups":list(groups.values()),
         "n_rows":len(rows),"n_groups":len(groups),"n_group_mismatch":chk,
         "data_pages":pages_data}
    json.dump(out, open("/tmp/zhuankan_parsed.json","w"), ensure_ascii=False)
    print(f"数据页:{pages_data} 专业行:{len(rows)} 专业组:{len(groups)} 组计划不匹配:{chk}")
    # 简单分布
    from collections import Counter
    print("类别分布:",Counter((r['km'],r['cat']) for r in rows))
    print("计划合计:",sum(r['plan'] or 0 for r in rows))

if __name__=="__main__":
    main()
