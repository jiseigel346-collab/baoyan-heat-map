# -*- coding: utf-8 -*-
"""
保研热度地图 - v18 保研通知网增强抓取版
功能：
1. 保留现有 schools.json，不清空已有院校库；
2. 抓取保研通知网公开的 2026 夏令营/预推免/正式推免通知；
3. 自动生成 data/summer_camp_notices.json；
4. 给 schools.json 中的学校增加 summer_notice_count / latest_summer_notice / heat_score 动态因子；
5. 生成 data/discovery_report.json，便于排查本次抓了多少、失败多少。

说明：
- 本脚本只抓公开页面，控制请求频率，不绕过登录、验证码、付费墙。
- 第三方聚合源用于“公告发现”和“热度参考”，重要人数/录取数据仍建议以高校官网/研招网核验为准。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHOOLS_JSON = DATA / "schools.json"
SUMMER_NOTICES_JSON = DATA / "summer_camp_notices.json"
DISCOVERY_REPORT_JSON = DATA / "discovery_report.json"

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime.now(CN_TZ)
TODAY = NOW.strftime("%Y-%m-%d")

LIST_URLS = [
    "https://www.baoyantongzhi.com/notice",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BaoyanHeatMapBot/1.0; +https://github.com/)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

NOTICE_KEYWORDS = ["2026", "夏令营", "优秀大学生", "暑期", "科学营", "开放日", "预推免", "推免"]
EXCLUDE_KEYWORDS = ["2023", "2024", "2022", "高考", "本科招生", "成人教育", "考研调剂"]


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def get_html(url: str, timeout: int = 20) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:
        print(f"[WARN] fetch failed: {url} -> {e}")
        return None


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def is_valid_notice_title(title: str) -> bool:
    t = title or ""
    if any(k in t for k in EXCLUDE_KEYWORDS):
        return False
    if "2026" not in t:
        return False
    return any(k in t for k in ["夏令营", "优秀大学生", "暑期", "科学营", "开放日", "预推免", "推免"])


def classify_notice(title: str, body: str = "") -> str:
    text = title + " " + body[:500]
    if any(k in text for k in ["夏令营", "优秀大学生", "暑期", "科学营", "开放日", "训练营", "探索营"]):
        return "夏令营"
    if "预推免" in text:
        return "预推免"
    if "推免" in text:
        return "推免"
    return "保研通知"


def find_school_name(title: str, body: str, school_names: List[str]) -> str:
    text = title + " " + body[:1000]
    for name in school_names:
        if name and name in text:
            return name
    # 兜底：从标题里粗略截学校/研究所名称
    m = re.search(r"2026年(.{2,40}?)(?:学院|大学|研究院|研究所|中心|实验室)", title)
    if m:
        raw = m.group(1)
        # 尽量保留完整机构名
        for suffix in ["大学", "学院", "研究院", "研究所", "中心", "实验室"]:
            idx = title.find(raw + suffix)
            if idx >= 0:
                return title[idx:idx + len(raw + suffix)]
    return ""


def extract_dates(text: str) -> Tuple[str, str]:
    # 抓报名开始/截止等日期，格式优先 YYYY-MM-DD
    dates = re.findall(r"20\d{2}[年\-/\.](?:0?[1-9]|1[0-2])[月\-/\.](?:0?[1-9]|[12]\d|3[01])日?", text)
    norm = []
    for d in dates:
        nums = re.findall(r"\d+", d)
        if len(nums) >= 3:
            y, m, day = nums[:3]
            norm.append(f"{int(y):04d}-{int(m):02d}-{int(day):02d}")
    norm = sorted(set(norm))
    if not norm:
        return "", ""
    if len(norm) == 1:
        return "", norm[0]
    return norm[0], norm[-1]


def extract_original_url(soup: BeautifulSoup, base_url: str) -> str:
    # 优先找“查看原文”链接
    for a in soup.find_all("a", href=True):
        txt = clean_text(a.get_text(" "))
        if "查看原文" in txt or "原文" == txt:
            href = urljoin(base_url, a["href"])
            if "baoyantongzhi.com" not in href:
                return href
    # 没找到就返回空
    return ""


def parse_detail(detail_url: str, school_names: List[str]) -> Optional[Dict[str, Any]]:
    html = get_html(detail_url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find(["h1", "h2"])
    title = clean_text(h1.get_text(" ")) if h1 else ""
    if not title:
        title_tag = soup.find("title")
        title = clean_text(title_tag.get_text(" ")) if title_tag else ""
    # 标题兜底：去掉站点名
    title = re.sub(r"\s*[-_|].*保研通知网.*$", "", title)
    body = clean_text(soup.get_text(" "))
    if not is_valid_notice_title(title) and not ("2026" in body[:800] and any(k in body[:800] for k in ["夏令营", "优秀大学生", "预推免", "推免"])):
        return None
    school = find_school_name(title, body, school_names)
    start_date, deadline = extract_dates(body[:3000])
    original_url = extract_original_url(soup, detail_url)
    notice_type = classify_notice(title, body)
    source_type = "第三方聚合"
    if original_url and re.search(r"\.(edu|ac)\.cn|\.edu\.cn|\.ac\.cn", original_url):
        source_type = "官网原文"
    return {
        "school_name": school or "未识别院校",
        "title": title[:160],
        "notice_type": notice_type,
        "publish_date": "",
        "start_date": start_date,
        "deadline": deadline,
        "source_name": "保研通知网",
        "source_type": source_type,
        "source_url": detail_url,
        "original_url": original_url,
        "last_checked": TODAY,
        "status": "已抓取",
    }


def parse_list_page(list_url: str) -> List[str]:
    html = get_html(list_url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/notice/detail/" not in href:
            continue
        title = clean_text(a.get_text(" "))
        abs_url = urljoin(list_url, href)
        if abs_url in seen:
            continue
        # 列表链接文本有时就是标题，有时是“查看详情”，后者也保留，详情页再判断
        if title and (is_valid_notice_title(title) or "查看" in title):
            urls.append(abs_url)
            seen.add(abs_url)
    return urls


def dedupe_notices(notices: List[Dict[str, Any]], max_items: int = 120) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    # 优先官网原文、再按截止时间靠前/近期检查排序
    def key_sort(n):
        source_weight = 0 if n.get("source_type") == "官网原文" else 1
        deadline = n.get("deadline") or "9999-12-31"
        return (source_weight, deadline, n.get("school_name", ""), n.get("title", ""))
    for n in sorted(notices, key=key_sort):
        k = n.get("original_url") or n.get("source_url") or n.get("title")
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(n)
        if len(out) >= max_items:
            break
    return out


def boost_school_heat(schools_data: Dict[str, Any], notices: List[Dict[str, Any]]) -> Dict[str, Any]:
    schools = schools_data.get("schools", []) if isinstance(schools_data, dict) else []
    by_school: Dict[str, List[Dict[str, Any]]] = {}
    for n in notices:
        s = n.get("school_name")
        if not s or s == "未识别院校":
            continue
        by_school.setdefault(s, []).append(n)
    for s in schools:
        name = s.get("school_name", "")
        items = by_school.get(name, [])
        if not items:
            continue
        s["summer_notice_count"] = str(len(items))
        s["latest_summer_notice"] = items[0].get("title", "")
        s["latest_summer_notice_url"] = items[0].get("original_url") or items[0].get("source_url", "")
        try:
            base = int(s.get("heat_score") or s.get("level_display") or 70)
        except Exception:
            base = 70
        s["heat_score"] = str(min(100, base + min(8, len(items) * 2)))
    schools_data["updated_at"] = NOW.strftime("%Y-%m-%dT%H:%M:%S%z")
    schools_data["summer_notice_total"] = len(notices)
    return schools_data


def main() -> None:
    DATA.mkdir(exist_ok=True)
    schools_data = read_json(SCHOOLS_JSON, {"schools": []})
    schools = schools_data.get("schools", []) if isinstance(schools_data, dict) else []
    school_names = sorted([s.get("school_name", "") for s in schools if s.get("school_name")], key=len, reverse=True)

    previous_notices = read_json(SUMMER_NOTICES_JSON, [])
    if isinstance(previous_notices, dict):
        previous_notices = previous_notices.get("items", []) or previous_notices.get("notices", []) or []
    all_notices: List[Dict[str, Any]] = list(previous_notices) if isinstance(previous_notices, list) else []

    report = {
        "updated_at": NOW.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "baoyantongzhi_public_pages",
        "list_urls": LIST_URLS,
        "list_detail_urls_found": 0,
        "detail_pages_checked": 0,
        "notices_added": 0,
        "errors": [],
    }

    detail_urls = []
    for list_url in LIST_URLS:
        urls = parse_list_page(list_url)
        detail_urls.extend(urls)
        time.sleep(0.8)
    # 去重，限制数量，避免对网站造成压力
    detail_urls = list(dict.fromkeys(detail_urls))[:80]
    report["list_detail_urls_found"] = len(detail_urls)

    before = len(all_notices)
    for i, u in enumerate(detail_urls, 1):
        item = parse_detail(u, school_names)
        report["detail_pages_checked"] += 1
        if item:
            all_notices.append(item)
        time.sleep(0.6)

    all_notices = dedupe_notices(all_notices, max_items=150)
    report["notices_added"] = max(0, len(all_notices) - before)
    report["summer_notice_total_after_dedupe"] = len(all_notices)

    # 如果本次没有新增，也保留旧数据，不清空
    write_json(SUMMER_NOTICES_JSON, all_notices)
    schools_data = boost_school_heat(schools_data, all_notices)
    write_json(SCHOOLS_JSON, schools_data)
    write_json(DISCOVERY_REPORT_JSON, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
