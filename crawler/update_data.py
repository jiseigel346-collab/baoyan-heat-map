# -*- coding: utf-8 -*-
"""
保研地图 v7：增加“接收推免人数 + 各专业人数”自动抓取
核心原则：
1. 本校推荐名额：来自本科教务处/本科生院推免名额通知；
2. 接收推免人数：来自研究生院/研招办拟录取名单；
3. 只有官网来源能访问并能解析到专业名单，才写入前台数据；
4. 阶段性名单只标记为“已抓取接收推免人数”，不冒充全校最终总数；
5. 没有来源的数据不显示，不写“待核验”。
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Tuple

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHOOLS_JSON = DATA / "schools.json"
SEED_JSON = DATA / "schools_seed.json"
SCHOOL_SOURCES_JSON = DATA / "school_sources.json"
RECEIVE_SOURCES_JSON = DATA / "receive_sources.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

# 医学类、工学类、管理类等专业名称常见结束词。用于从 PDF 文本中截断专业名，避免把研究方向也算进去。
MAJOR_END_WORDS = [
    "中医内科学","中医外科学","中医骨伤科学","中医妇科学","中医儿科学","中医五官科学",
    "针灸推拿学","中西医结合临床","中药学","药学","护理","护理学","公共卫生","全科医学",
    "内科学","外科学","妇产科学","儿科学","影像医学与核医学","临床检验诊断学",
    "计算机科学与技术","软件工程","电子信息","机械","材料","土木工程","交通运输",
    "金融","法学","教育学","心理学","数学","物理学","化学","生物学","生态学",
]

def now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch(url: str) -> requests.Response:
    r = requests.get(url, timeout=30, headers={"User-Agent": UA})
    r.raise_for_status()
    return r

def get_html_text(url: str) -> str:
    r = fetch(url)
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    return re.sub(r"\s+", " ", soup.get_text("\n", strip=True))

def get_pdf_text(url: str) -> str:
    r = fetch(url)
    reader = PdfReader(io.BytesIO(r.content))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            pass
    return "\n".join(parts)

def find_school_item(schools: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    target = re.sub(r"\s+", "", name)
    for s in schools:
        if re.sub(r"\s+", "", s.get("school_name", "")) == target:
            return s
    return None

def ensure_common_fields(s: Dict[str, Any]) -> None:
    for k in [
        "recommendation_quota","graduate_count","recommendation_rate","recommendation_year",
        "recommendation_source_name","recommendation_source_url","recommendation_note","recommendation_verify_status",
        "receive_recommend_total","receive_recommend_year","receive_recommend_source_name","receive_recommend_source_url",
        "receive_recommend_verify_status","receive_recommend_by_major","receive_recommend_is_complete","receive_recommend_note",
        "last_checked",
    ]:
        if k not in s:
            s[k] = [] if k == "receive_recommend_by_major" else ""

def calc_rate(quota: int, graduate_count: Any) -> str:
    try:
        g = int(str(graduate_count).replace(",", "").strip())
        if g > 0:
            return f"{quota / g * 100:.2f}%"
    except Exception:
        pass
    return ""

def parse_recommend_quota(src: Dict[str, Any], text: str) -> int | None:
    stype = src.get("source_type")
    if stype == "allocation_sum_with_extra":
        values = src.get("allocation_values") or []
        if values:
            return int(sum(int(x) for x in values) + int(src.get("extra_quota") or 0))
    if stype == "regex_quota":
        pattern = src.get("quota_regex")
        if pattern:
            m = re.search(pattern, text)
            if m:
                return int(m.group(1))
    return None

def update_recommend_quota(schools: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    sources_db = load_json(SCHOOL_SOURCES_JSON, {"sources": []})
    updated, failed = 0, []
    for src in sources_db.get("sources", []):
        name, url = src.get("school_name"), src.get("source_url")
        if not name or not url:
            continue
        item = find_school_item(schools, name)
        if not item:
            failed.append({"school_name": name, "reason": "院校库未找到该学校"})
            continue
        ensure_common_fields(item)
        try:
            text = get_html_text(url)
            missing = [kw for kw in src.get("verify_keywords", []) if kw not in text]
            if missing:
                failed.append({"school_name": name, "reason": "关键词未匹配", "missing": missing[:3]})
                continue
            quota = parse_recommend_quota(src, text)
            if quota:
                item["recommendation_quota"] = str(quota)
                item["recommendation_year"] = src.get("year", "")
                item["recommendation_source_name"] = src.get("source_name", "")
                item["recommendation_source_url"] = url
                item["recommendation_note"] = src.get("quota_note", "")
                item["recommendation_verify_status"] = "官网已核验"
                item["last_checked"] = now_cn()
                rate = calc_rate(quota, item.get("graduate_count", ""))
                if rate:
                    item["recommendation_rate"] = rate
                updated += 1
        except Exception as e:
            failed.append({"school_name": name, "reason": repr(e)})
    return updated, failed

def clean_major_name(code: str, raw: str) -> str:
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"^(拟录取专业|专业名称|专业)", "", raw)
    # 优先按常见专业词截断
    for w in sorted(MAJOR_END_WORDS, key=len, reverse=True):
        if raw.startswith(w):
            return w
    # 通用截断：最多取 12 个中文字符，避免带入研究方向
    m = re.match(r"([\u4e00-\u9fa5]{2,12})", raw)
    return m.group(1) if m else raw[:20]

def parse_major_counts_from_text(text: str) -> List[Dict[str, Any]]:
    # 将 PDF 断行统一为空格，按“专业代码 + 专业名”提取。
    t = re.sub(r"\s+", " ", text)
    # 常见硕士专业代码：学硕/专硕均覆盖。后面抓取一段中文，再清洗成专业名。
    pattern = re.compile(r"(?<!\d)((?:0[1-9]|1[0-3])\d{4}|(?:025|035|045|055|085|095|105|125)\d{3})\s*([\u4e00-\u9fa5]{2,30})")
    counter = Counter()
    for code, raw_major in pattern.findall(t):
        major = clean_major_name(code, raw_major)
        if not major or "拟录取" in major or "研究方向" in major:
            continue
        counter[(code, major)] += 1
    rows = [
        {"major_code": code, "major_name": major, "count": count}
        for (code, major), count in counter.items()
    ]
    rows.sort(key=lambda x: x["count"], reverse=True)
    return rows

def update_receive_recommend(schools: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    sources_db = load_json(RECEIVE_SOURCES_JSON, {"sources": []})
    updated, failed = 0, []
    for src in sources_db.get("sources", []):
        name, url = src.get("school_name"), src.get("source_url")
        if not name or not url:
            continue
        item = find_school_item(schools, name)
        if not item:
            failed.append({"school_name": name, "reason": "院校库未找到该学校"})
            continue
        ensure_common_fields(item)
        try:
            if url.lower().split("?")[0].endswith(".pdf"):
                text = get_pdf_text(url)
            else:
                text = get_html_text(url)
            rows = parse_major_counts_from_text(text)
            total = sum(int(r["count"]) for r in rows)
            if total > 0:
                item["receive_recommend_total"] = str(total)
                item["receive_recommend_year"] = src.get("year", "")
                item["receive_recommend_source_name"] = src.get("source_name", "")
                item["receive_recommend_source_url"] = url
                item["receive_recommend_verify_status"] = "官网已核验"
                item["receive_recommend_by_major"] = rows
                item["receive_recommend_is_complete"] = src.get("is_complete_total", "否")
                item["receive_recommend_note"] = src.get("note", "")
                item["last_checked"] = now_cn()
                updated += 1
            else:
                failed.append({"school_name": name, "reason": "未解析到专业人数"})
        except Exception as e:
            failed.append({"school_name": name, "reason": repr(e)})
    return updated, failed

def main() -> None:
    DATA.mkdir(exist_ok=True)
    db = load_json(SCHOOLS_JSON, None)
    if db is None:
        db = load_json(SEED_JSON, {"schools": []})
    if isinstance(db, list):
        db = {"schools": db}
    schools = db.get("schools", [])
    for s in schools:
        ensure_common_fields(s)

    quota_updated, quota_failed = update_recommend_quota(schools)
    receive_updated, receive_failed = update_receive_recommend(schools)

    db["schools"] = schools
    db["updated_at"] = now_cn()
    db["quota_updated_count"] = quota_updated
    db["receive_updated_count"] = receive_updated
    db["quota_failed_samples"] = quota_failed[:10]
    db["receive_failed_samples"] = receive_failed[:10]
    db["note"] = "本校推荐名额与接收推免人数为不同口径；仅显示官网可核验数据。"
    save_json(SCHOOLS_JSON, db)
    print(f"recommendation quota updated: {quota_updated}, receive recommend updated: {receive_updated}")
    if quota_failed:
        print("quota failed samples:", quota_failed[:3])
    if receive_failed:
        print("receive failed samples:", receive_failed[:3])

if __name__ == "__main__":
    main()
