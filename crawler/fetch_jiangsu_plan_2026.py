# -*- coding: utf-8 -*-
"""抓取“2026年全国高校在江苏的本科招生计划（逐校×专业组×专业）”。

数据源：掌上高考（中国教育在线）公开静态数据接口
  - 全国院校列表： https://api.eol.cn/gkcx/api/?...uri=apidata/api/gk/school/lists
  - 逐校招生计划： https://static-data.gaokao.cn/www/2.0/schoolspecialplan/{school_id}/2026/32.json
    （32 = 江苏省）

说明：该数据为第三方聚合，权威口径仍以江苏省教育考试院《2026招生计划专刊》/志愿
系统为准；本脚本仅做结构化整理，便于核对参考。

输出：data/jiangsu_plan_2026.json（缓存，供 build_jiangsu_excel.py 生成 Excel）

用法：
  python crawler/fetch_jiangsu_plan_2026.py            # 全量
  python crawler/fetch_jiangsu_plan_2026.py --limit 20 # 仅抓前 20 所（验证用）
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "jiangsu_plan_2026.json"
YEAR = "2026"
PROV = "32"  # 江苏

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
LIST_API = "https://api.eol.cn/gkcx/api/"
PLAN_URL = "https://static-data.gaokao.cn/www/2.0/schoolspecialplan/{sid}/%s/%s.json" % (YEAR, PROV)

session = requests.Session()
session.headers.update({"User-Agent": UA, "Referer": "https://www.gaokao.cn/"})


def get_school_list(max_n=None):
    """分页拉取全国院校列表，返回 [{school_id,name,province_name,code_enroll,level_name}]"""
    out, page, size = [], 1, 100
    while True:
        params = {
            "access_token": "", "admissions": "", "central": "", "department": "",
            "dual_class": "", "f211": "", "f985": "", "is_doublehigh": "",
            "is_dual_class": "", "keyword": "", "nature": "", "page": page,
            "province_id": "", "ranktype": "", "request_type": 1, "school_type": "",
            "signsafe": "", "size": size, "sort": "view_total", "type": "",
            "uri": "apidata/api/gk/school/lists",
        }
        data = {}
        for attempt in range(5):
            try:
                r = session.get(LIST_API, params=params, timeout=20)
                j = r.json()
                d = j.get("data")
                if isinstance(d, dict) and d.get("item"):
                    data = d
                    break
            except Exception:
                pass
            time.sleep(1.5 * (attempt + 1))
        items = data.get("item") or []
        if not items:
            break
        for it in items:
            out.append({
                "school_id": it.get("school_id"),
                "name": it.get("name"),
                "province_name": it.get("province_name"),
                "code_enroll": it.get("code_enroll"),
                "level_name": it.get("level_name"),
            })
        total = data.get("numFound") or 0
        sys.stderr.write(f"\r院校列表 {len(out)}/{total}")
        sys.stderr.flush()
        if len(out) >= total:
            break
        if max_n and len(out) >= max_n:
            break
        page += 1
        time.sleep(0.5)
    sys.stderr.write("\n")
    return out


def first_subject(sg_info: str) -> str:
    s = sg_info or ""
    if "首选物理" in s:
        return "物理类"
    if "首选历史" in s:
        return "历史类"
    return ""


def fetch_plan(school):
    sid = school["school_id"]
    url = PLAN_URL.format(sid=sid)
    for attempt in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 404:
                return []
            j = r.json()
            break
        except Exception:
            if attempt == 2:
                return []
            time.sleep(1 + attempt)
    data = j.get("data")
    if not isinstance(data, dict) or not data:
        return []
    # 接口里每个 "科类_批次_组号" 是一个组；其中以 "_0" 结尾的键是该 科类+批次
    # 的聚合（包含全部专业，且各专业自带其专业组信息），数字组号会与聚合重复。
    # 因此优先只取 "_0" 聚合键；若没有则回退到全部键并按记录去重。
    agg_keys = [k for k in data if str(k).endswith("_0")]
    use_keys = agg_keys if agg_keys else list(data.keys())
    rows = []
    seen = set()
    for gkey in use_keys:
        grp = data.get(gkey)
        if not isinstance(grp, dict):
            continue
        for it in grp.get("item", []) or []:
            dk = (it.get("special_group"), it.get("special_id"),
                  it.get("spname"), it.get("num"), it.get("level1_name"))
            if dk in seen:
                continue
            seen.add(dk)
            level1 = it.get("level1_name") or ""
            rows.append({
                "school_name": school["name"],
                "school_province": school["province_name"],
                "school_code": school["code_enroll"],
                "batch": level1,                       # 批次/层次，如 本科(普通)
                "subject_type": first_subject(it.get("sg_info")),  # 历史类/物理类
                "group_name": it.get("sg_name") or "",  # 专业组号
                "select_req": it.get("sg_info") or "",  # 选科要求
                "special_name": it.get("spname") or it.get("sp_name") or "",
                "num": it.get("num"),                   # 计划数
                "length": it.get("length") or "",       # 学制
                "tuition": it.get("tuition") or "",     # 学费
                "remark": (it.get("info") or it.get("remark") or "").strip(),
                "zslx": it.get("zslx") or "",
            })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=12)
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    schools = get_school_list(max_n=args.limit or None)
    if args.limit:
        schools = schools[:args.limit]
    sys.stderr.write(f"准备抓取 {len(schools)} 所院校的 {YEAR} 江苏招生计划...\n")

    all_rows = []
    done = 0
    with_plan = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_plan, s): s for s in schools}
        for fut in as_completed(futs):
            rows = fut.result()
            if rows:
                with_plan += 1
                all_rows.extend(rows)
            done += 1
            if done % 50 == 0 or done == len(schools):
                sys.stderr.write(f"\r进度 {done}/{len(schools)}  有计划院校 {with_plan}  累计行 {len(all_rows)}")
                sys.stderr.flush()
    sys.stderr.write("\n")

    obj = {
        "year": int(YEAR),
        "province": "江苏",
        "source": "掌上高考(中国教育在线) static-data.gaokao.cn schoolspecialplan",
        "authoritative_note": "权威口径以江苏省教育考试院《2026招生计划专刊》及志愿系统为准；本数据为第三方聚合，仅供参考核对。",
        "school_count_total": len(schools),
        "school_count_with_plan": with_plan,
        "row_count": len(all_rows),
        "rows": all_rows,
    }
    OUT.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    print(f"完成：院校 {len(schools)} 所，其中有江苏{YEAR}计划 {with_plan} 所，明细 {len(all_rows)} 行 -> {OUT}")


if __name__ == "__main__":
    main()
