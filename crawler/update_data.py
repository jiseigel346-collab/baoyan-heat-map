# -*- coding: utf-8 -*-
"""
保研地图 v6：官网推免名额自动抓取补丁
逻辑：
1. 读取 data/schools.json 中已有院校库；
2. 读取 data/school_sources.json 中的官网来源清单；
3. 自动访问官网页面，只有页面包含核验关键词时才写入推免名额；
4. 没有官网来源、抓不到页面、关键词不匹配的学校，一律不写数字；
5. 推免率只有在 graduate_count 同时存在时才自动计算。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHOOLS_JSON = DATA / "schools.json"
SEED_JSON = DATA / "schools_seed.json"
SOURCES_JSON = DATA / "school_sources.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

def now_cn() -> str:
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")

def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def get_text(url: str) -> str:
    r = requests.get(url, timeout=25, headers={"User-Agent": UA})
    r.raise_for_status()
    if not r.encoding or r.encoding.lower() == "iso-8859-1":
        r.encoding = r.apparent_encoding
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\s+", " ", text)

def find_school_item(schools: List[Dict[str, Any]], name: str) -> Dict[str, Any] | None:
    for s in schools:
        if s.get("school_name") == name:
            return s
    compact = re.sub(r"\s+", "", name)
    for s in schools:
        if re.sub(r"\s+", "", s.get("school_name","")) == compact:
            return s
    return None

def parse_quota(src: Dict[str, Any], text: str) -> int | None:
    stype = src.get("source_type")
    if stype == "allocation_sum_with_extra":
        values = src.get("allocation_values") or []
        if not values:
            return None
        return int(sum(int(x) for x in values) + int(src.get("extra_quota") or 0))
    if stype == "regex_quota":
        pattern = src.get("quota_regex")
        if not pattern:
            return None
        m = re.search(pattern, text)
        if m:
            return int(m.group(1))
    return None

def calc_rate(quota: int, graduate_count: Any) -> str:
    try:
        g = int(str(graduate_count).replace(",", "").strip())
        if g > 0:
            return f"{quota / g * 100:.2f}%"
    except Exception:
        pass
    return ""

def ensure_fields(s: Dict[str, Any]) -> None:
    for k in [
        "recommendation_quota",
        "graduate_count",
        "recommendation_rate",
        "recommendation_year",
        "recommendation_source_name",
        "recommendation_source_url",
        "recommendation_note",
        "recommendation_verify_status",
        "last_checked",
    ]:
        s.setdefault(k, "")

def update_heat_score_if_needed(s: Dict[str, Any]) -> None:
    try:
        score = int(str(s.get("heat_score") or "0"))
    except Exception:
        score = 0
    if s.get("recommendation_quota"):
        score = min(99, score + 3)
    s["heat_score"] = str(score) if isinstance(s.get("heat_score"), str) else score
    s["level_display"] = str(score)

def main() -> None:
    DATA.mkdir(exist_ok=True)
    db = load_json(SCHOOLS_JSON, None)
    if db is None:
        db = load_json(SEED_JSON, {"schools": []})
    if isinstance(db, list):
        db = {"schools": db}
    schools = db.get("schools", [])
    sources_db = load_json(SOURCES_JSON, {"sources": []})
    sources = sources_db.get("sources", [])

    updated = 0
    failed = []
    for src in sources:
        name = src.get("school_name")
        url = src.get("source_url")
        if not name or not url:
            continue
        item = find_school_item(schools, name)
        if not item:
            failed.append({"school_name": name, "reason": "院校库未找到该学校"})
            continue
        ensure_fields(item)
        try:
            text = get_text(url)
            missing = [kw for kw in src.get("verify_keywords", []) if kw not in text]
            if missing:
                failed.append({"school_name": name, "reason": "关键词未匹配", "missing": missing[:3]})
                continue
            quota = parse_quota(src, text)
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
                update_heat_score_if_needed(item)
                updated += 1
            else:
                failed.append({"school_name": name, "reason": "未提取到推免名额数字"})
        except Exception as e:
            failed.append({"school_name": name, "reason": repr(e)})

    db["schools"] = schools
    db["updated_at"] = now_cn()
    db["quota_source_count"] = len(sources)
    db["quota_updated_count"] = updated
    db["quota_failed_samples"] = failed[:10]
    db["note"] = "推免名额/推免率只写入官网可核验数据；未抓到真实来源的不在前台显示。"
    save_json(SCHOOLS_JSON, db)
    print(f"quota sources: {len(sources)}, updated: {updated}, failed: {len(failed)}")
    if failed:
        print("failed samples:", failed[:3])

if __name__ == "__main__":
    main()
