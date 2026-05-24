# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SUMMER_FILE = DATA / "summer_camp_notices.json"
REPORT_FILE = DATA / "discovery_report.json"
SCHOOLS_FILE = DATA / "schools.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

SEED_NOTICES = [
{"school":"北京大学","title":"北京大学研究生招生网“夏令营”栏目：2026年优秀大学生夏令营通知持续更新","date":"2026-05","url":"https://admission.pku.edu.cn/xly/index.htm","source_type":"官网栏目","category":"夏令营","province":"北京","level":"985","status":"更新中"},
{"school":"北京大学燕京学堂","title":"北京大学燕京学堂2026年全国优秀大学生夏令营","date":"2026-05-22","url":"https://yenching.pku.edu.cn/info/1040/5974.htm","source_type":"官网","category":"夏令营","province":"北京","level":"985","status":"已发布"},
{"school":"中国科学院国家空间科学中心","title":"中国科学院国家空间科学中心2026年全国优秀大学生夏令营招募通知","date":"2026-05-21","url":"https://www.nssc.ac.cn/yjsb/zsxx/zsdt/202605/t20260521_8207648.html","source_type":"官网","category":"夏令营","province":"北京","level":"科研院所","status":"报名中"},
{"school":"中国科学院理化技术研究所","title":"欢迎参加理化所2026年优秀大学生夏令营","date":"2026-05-08","url":"https://ipc.cas.cn/yjsjy2019/zsxx/202605/t20260508_8197625.html","source_type":"官网","category":"夏令营","province":"北京","level":"科研院所","status":"报名中"},
{"school":"中国科学院大学公共政策与管理学院","title":"中国科学院2026年公共政策与管理全国优秀大学生夏令营报名通知","date":"2026-05-07","url":"https://sppm.ucas.ac.cn/index.php/zh-CN/zsgl/zsxx/3367-2014","source_type":"官网","category":"夏令营","province":"北京","level":"双一流","status":"报名中"},
{"school":"中国科学院生物与化学交叉研究中心","title":"中国科学院生物与化学交叉研究中心2026年暑期夏令营","date":"2026-04-09","url":"https://www.ircbc.cn/edu/xly/202604/t20260409_8183495.html","source_type":"官网","category":"夏令营","province":"上海","level":"科研院所","status":"已发布"},
{"school":"北京大学深圳研究生院","title":"北京大学深圳研究生院2026年国际暑期探索营","date":"2026-04-28","url":"https://www.pkusz.edu.cn/info/1058/6656.htm","source_type":"官网","category":"夏令营","province":"广东","level":"985","status":"已发布"},
{"school":"香港中文大学（深圳）","title":"数据科学学院硕士研究生项目2026年优秀大学生迷你营及夏令营","date":"2026-02-04","url":"https://www.baoyantongzhi.com/notice/detail/24602","source_type":"聚合来源","category":"夏令营","province":"广东","level":"合作办学","status":"已发布"},
{"school":"浙江大学","title":"浙江大学基础医学院2026年优秀大学生暑期夏令营通知","date":"2026","url":"https://www.baoyantongzhi.com/notice/detail/54656","source_type":"聚合来源","category":"夏令营","province":"浙江","level":"985","status":"已发布"},
{"school":"保研通知网","title":"2026保研通知查询：全国高校夏令营、预推免、正式推免通知","date":"2026","url":"https://www.baoyantongzhi.com/notice","source_type":"聚合来源","category":"聚合入口","province":"全国","level":"聚合源","status":"更新中"}
]

def now_iso():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")

def read_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def get_html(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text

def extract_date(text):
    m = re.search(r"(2026)[-/年.](\d{1,2})(?:[-/月.](\d{1,2}))?", text)
    if not m: return ""
    y, mo, d = m.group(1), int(m.group(2)), m.group(3)
    return f"{y}-{mo:02d}-{int(d):02d}" if d else f"{y}-{mo:02d}"

def guess_school(title):
    known = ["北京大学燕京学堂","北京大学深圳研究生院","北京大学","中国科学院国家空间科学中心","中国科学院理化技术研究所","中国科学院理化所","中国科学院大学公共政策与管理学院","中国科学院大学","中国科学院生物与化学交叉研究中心","香港中文大学（深圳）","浙江大学","南京大学","中国科学技术大学"]
    for k in known:
        if k in title: return k
    m = re.match(r"(.{2,30}?大学|.{2,30}?学院|.{2,30}?研究所|.{2,30}?中心)", title)
    return m.group(1) if m else "全国高校"

def normalize(n):
    title = str(n.get("title","")).strip()
    url = str(n.get("url","")).strip()
    text = title + " " + url
    if not title or not url or "2026" not in text: return None
    if not any(k in text for k in ["夏令营","优秀大学生","暑期","预推免","推免","迷你营"]): return None
    if any(old in text for old in ["2023","2024"]): return None
    n.setdefault("school", guess_school(title))
    n.setdefault("date", extract_date(text) or "2026")
    n.setdefault("source_type", "公开来源")
    n.setdefault("category", "夏令营")
    n.setdefault("province", "")
    n.setdefault("level", "")
    n.setdefault("status", "已发布")
    return n

def parse_links(url, source_type):
    out = []
    try:
        html = get_html(url)
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return out
    for a in soup.find_all("a"):
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        full = urljoin(url, href)
        n = normalize({"title": title, "url": full, "source_type": source_type, "school": guess_school(title), "date": extract_date(title+" "+full) or "2026"})
        if n: out.append(n)
    return out

def merge(existing, new):
    pool = []
    for n in existing + new + SEED_NOTICES:
        nn = normalize(dict(n))
        if nn: pool.append(nn)
    seen, merged = set(), []
    for n in pool:
        key = n["url"].split("#")[0]
        if key in seen: continue
        seen.add(key); merged.append(n)
    merged.sort(key=lambda x: (1 if str(x.get("source_type","")).startswith("官网") else 0, str(x.get("date",""))), reverse=True)
    return merged[:200]

def main():
    DATA.mkdir(exist_ok=True)
    old_obj = read_json(SUMMER_FILE, {})
    if isinstance(old_obj, dict):
        old = old_obj.get("notices") or old_obj.get("items") or old_obj.get("summer_camp_notices") or []
    elif isinstance(old_obj, list):
        old = old_obj
    else:
        old = []
    sources = [
        ("https://admission.pku.edu.cn/xly/index.htm","官网"),
        ("https://yzb.nju.edu.cn/xlyxx/listm.htm","官网"),
        ("https://www.nssc.ac.cn/yjsb/zsxx/zsdt/","官网"),
        ("https://ipc.cas.cn/yjsjy2019/zsxx/","官网"),
        ("https://www.baoyantongzhi.com/notice","聚合来源"),
    ]
    found = []
    for url, typ in sources:
        found += parse_links(url, typ)
    merged = merge(old, found)
    obj = {"updated_at": now_iso(), "notice_count": len(merged), "note": "稳定版：抓不到新公告时保留旧公告和种子公告，避免前台空白。", "notices": merged, "items": merged, "summer_camp_notices": merged}
    SUMMER_FILE.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    schools = read_json(SCHOOLS_FILE, None)
    if isinstance(schools, dict):
        schools["last_summer_notice_update"] = obj["updated_at"]
        SCHOOLS_FILE.write_text(json.dumps(schools, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {"updated_at": obj["updated_at"], "status": "success", "existing_before": len(old), "found_this_run": len(found), "final_notice_count": len(merged), "official_count": sum(1 for n in merged if str(n.get("source_type","")).startswith("官网")), "aggregate_count": sum(1 for n in merged if "聚合" in str(n.get("source_type",""))), "message": "官网栏目 + 保研通知网公开页抓取；若抓取为空，保留已有公告和种子公告，前台不会空白。"}
    REPORT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
