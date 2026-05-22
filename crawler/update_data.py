#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全国保研院校夏令营公告 / 接收推免名单发现爬虫 v15

核心目标：
1. 不再只依赖手工写入的几个学校链接。
2. 使用“官方站点优先 + 聚合站线索补充 + 搜索发现”的方式，自动发现全国高校 2026 年夏令营/优秀大学生营/预推免公告。
3. 同步发现 2026 级接收推免拟录取名单候选源，为后续解析专业人数做准备。
4. 前台只展示真实抓到的夏令营公告；接收推免专业人数没有成功解析前，不输出“抓取中/接入中”。

重要边界：
- GitHub Actions 是轻量定时任务，不是搜索引擎，也不是商业爬虫集群。
- 若想获得更高覆盖率，建议后续接入 SerpAPI / Bing Web Search API / 自建搜索服务。
- 本脚本不编造数据；抓不到就不写假数据。
"""

from __future__ import annotations

import json
import os
import re
import time
import hashlib
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader  # optional, used only for PDF receive-list parsing later
except Exception:
    PdfReader = None


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DATA_DIR = os.path.join(ROOT, "data")

SCHOOLS_PATH = os.path.join(DATA_DIR, "schools.json")
SUMMER_NOTICES_PATH = os.path.join(DATA_DIR, "summer_camp_notices.json")
SUMMER_SOURCES_PATH = os.path.join(DATA_DIR, "summer_camp_sources.json")
RECEIVE_CANDIDATES_PATH = os.path.join(DATA_DIR, "receive_source_candidates.json")
DISCOVERY_LOG_PATH = os.path.join(DATA_DIR, "discovery_report.json")

CURRENT_YEAR = 2026
TODAY = datetime.now(timezone.utc).date().isoformat()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
}

# 夏令营公告关键词：标题/正文命中其一即可作为候选
SUMMER_KEYWORDS = [
    "夏令营", "优秀大学生", "暑期学校", "暑期夏令营", "学术夏令营",
    "大学生夏令营", "推免夏令营", "预推免", "推免预报名", "开放日",
]

# 接收推免名单关键词：用于发现候选源，不等于已经解析人数
RECEIVE_KEYWORDS = [
    "接收推荐免试", "接收推免", "推免生拟录取", "推荐免试研究生拟录取",
    "推荐免试攻读", "推免硕士拟录取", "拟录取名单", "接收免试",
]

EXCLUDE_KEYWORDS = [
    "2023", "2024", "2025年夏令营", "2025优秀大学生夏令营",
    "博士后", "高考", "本科招生", "中考", "成人教育",
]

TRUSTED_OFFICIAL_SUFFIX = (
    ".edu.cn", ".ac.cn", ".edu", ".edu.hk", ".edu.mo", ".gov.cn"
)

AGGREGATOR_HINT_DOMAINS = [
    "baoyantongzhi.com",
    "eol.cn",
    "kaoyan.com",
    "yz.kaoyan.com",
    "baoyan.com",
    "chinakaoyan.com",
    "51baoyan.com",
]

# 限制每次 Actions 的请求数，避免跑太久或被站点限制
MAX_GLOBAL_SEARCH_QUERIES = int(os.getenv("MAX_GLOBAL_SEARCH_QUERIES", "18"))
MAX_SCHOOL_SEARCHES_PER_RUN = int(os.getenv("MAX_SCHOOL_SEARCHES_PER_RUN", "80"))
REQUEST_INTERVAL = float(os.getenv("REQUEST_INTERVAL", "1.0"))
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))


@dataclass
class Notice:
    id: str
    school: str
    title: str
    url: str
    publish_date: str
    type: str  # summer | receive_candidate
    source_type: str  # official | aggregator | search
    source_domain: str
    discovered_at: str
    score: int
    summary: str = ""


def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data):
    ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def safe_get(url: str, timeout: int = TIMEOUT) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None
        return resp
    except Exception:
        return None
    finally:
        time.sleep(REQUEST_INTERVAL)


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    # remove common trackers
    parsed = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    q = [(k, v) for k, v in q if not k.lower().startswith("utm_") and k.lower() not in ("spm", "from")]
    query = urllib.parse.urlencode(q)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def is_official_url(url: str) -> bool:
    d = domain_of(url)
    return any(d.endswith(suf) for suf in TRUSTED_OFFICIAL_SUFFIX)


def source_type_for(url: str) -> str:
    d = domain_of(url)
    if is_official_url(url):
        return "official"
    if any(x in d for x in AGGREGATOR_HINT_DOMAINS):
        return "aggregator"
    return "search"


def hash_id(*parts: str) -> str:
    raw = "||".join([p or "" for p in parts])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def clean_text(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_date(text: str, url: str = "") -> str:
    blob = f"{text} {url}"
    # 2026-05-22 / 2026/05/22 / 20260522 / 2026年5月22日
    patterns = [
        r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})",
        r"(20\d{2})年(\d{1,2})月(\d{1,2})日",
        r"(20\d{2})(\d{2})(\d{2})",
    ]
    for p in patterns:
        m = re.search(p, blob)
        if m:
            y, mo, da = m.groups()
            try:
                return f"{int(y):04d}-{int(mo):02d}-{int(da):02d}"
            except Exception:
                pass
    # some sites only show year-month
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})月?", blob)
    if m:
        y, mo = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-01"
    return ""


def is_current_summer_notice(title: str, snippet: str, url: str, date: str = "") -> bool:
    blob = f"{title} {snippet} {url}"
    if not any(k in blob for k in SUMMER_KEYWORDS):
        return False
    # summer camp in 2026 is current. Also accept publish date >= 2026-01-01.
    if "2026" in blob:
        return True
    if date and date >= f"{CURRENT_YEAR}-01-01":
        return True
    # explicitly reject old years if no current signal
    if any(k in blob for k in EXCLUDE_KEYWORDS):
        return False
    return False


def is_receive_candidate(title: str, snippet: str, url: str, date: str = "") -> bool:
    blob = f"{title} {snippet} {url}"
    if not any(k in blob for k in RECEIVE_KEYWORDS):
        return False
    # 2026级推免名单常发布于2025年秋季，所以允许 2025 + 2026 语义
    if "2026" in blob or "2026级" in blob:
        return True
    return False


def match_school(text: str, schools: List[Dict]) -> str:
    # prefer longest school names to avoid false match
    candidates = sorted([s.get("school_name") or s.get("name") or "" for s in schools], key=len, reverse=True)
    for name in candidates:
        if name and name in text:
            return name
    return ""


def build_search_queries(schools: List[Dict]) -> List[str]:
    global_queries = [
        "2026 优秀大学生 夏令营 研究生 招生",
        "2026 全国优秀大学生 夏令营 研究生院",
        "2026 暑期夏令营 推免 研究生",
        "2026 学术夏令营 优秀大学生 研究生院",
        "2026 夏令营 保研 通知",
        "2026 预推免 优秀大学生 夏令营",
        "site:edu.cn 2026 优秀大学生 夏令营 研究生院",
        "site:edu.cn 2026 暑期夏令营 推免",
        "site:ac.cn 2026 优秀大学生 夏令营",
        "site:edu.cn 2026 夏令营 招生 学院",
        "2026 接收推荐免试研究生 拟录取名单 专业",
        "2026 推免生拟录取名单 专业",
        "site:edu.cn 2026 接收推免 拟录取名单",
        "site:edu.cn 2026 推荐免试研究生拟录取名单",
    ][:MAX_GLOBAL_SEARCH_QUERIES]

    # rotate schools based on current day, so each scheduled run covers a different batch
    school_names = [s.get("school_name") or s.get("name") for s in schools if s.get("school_name") or s.get("name")]
    if not school_names:
        return global_queries

    offset = datetime.now(timezone.utc).timetuple().tm_yday % max(1, len(school_names))
    rotated = school_names[offset:] + school_names[:offset]
    school_batch = rotated[:MAX_SCHOOL_SEARCHES_PER_RUN]

    school_queries = []
    for name in school_batch:
        school_queries.append(f"{name} 2026 优秀大学生 夏令营")
        school_queries.append(f"{name} 2026 预推免 夏令营")
        # only a smaller subset for receive candidate; otherwise too many requests
        if len(school_queries) < MAX_SCHOOL_SEARCHES_PER_RUN * 3:
            school_queries.append(f"{name} 2026 接收推荐免试研究生 拟录取名单")

    return global_queries + school_queries


def parse_bing_results(html: str) -> List[Tuple[str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for item in soup.select("li.b_algo"):
        a = item.select_one("h2 a")
        if not a:
            continue
        title = clean_text(a.get_text(" ", strip=True))
        url = normalize_url(a.get("href") or "")
        snippet = clean_text(item.get_text(" ", strip=True))
        if url.startswith("http"):
            results.append((title, url, snippet))
    return results


def parse_duckduckgo_results(html: str) -> List[Tuple[str, str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.select("a.result__a"):
        title = clean_text(a.get_text(" ", strip=True))
        href = a.get("href") or ""
        # DDG sometimes wraps URL in uddg param
        parsed = urllib.parse.urlparse(href)
        q = urllib.parse.parse_qs(parsed.query)
        url = q.get("uddg", [href])[0]
        url = normalize_url(urllib.parse.unquote(url))
        parent = a.find_parent("div")
        snippet = clean_text(parent.get_text(" ", strip=True)) if parent else title
        if url.startswith("http"):
            results.append((title, url, snippet))
    return results


def search_web(query: str) -> List[Tuple[str, str, str]]:
    collected: List[Tuple[str, str, str]] = []

    # Bing HTML fallback
    bing_url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": 10})
    resp = safe_get(bing_url)
    if resp and resp.text:
        collected.extend(parse_bing_results(resp.text))

    # DuckDuckGo fallback
    ddg_url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    resp = safe_get(ddg_url)
    if resp and resp.text:
        collected.extend(parse_duckduckgo_results(resp.text))

    # de-dup
    seen = set()
    unique = []
    for title, url, snippet in collected:
        if url in seen:
            continue
        seen.add(url)
        unique.append((title, url, snippet))
    return unique


def fetch_title_and_date(url: str, fallback_title: str = "", fallback_snippet: str = "") -> Tuple[str, str, str]:
    resp = safe_get(url, timeout=TIMEOUT)
    if not resp:
        return fallback_title, extract_date(f"{fallback_title} {fallback_snippet}", url), fallback_snippet

    content_type = resp.headers.get("content-type", "").lower()
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        # For speed and robustness, do not parse full PDF here. Use fallback metadata.
        return fallback_title, extract_date(f"{fallback_title} {fallback_snippet}", url), fallback_snippet

    # best effort decoding
    resp.encoding = resp.encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    title = ""
    h1 = soup.find(["h1", "h2"])
    if h1:
        title = clean_text(h1.get_text(" ", strip=True))
    if not title and soup.title:
        title = clean_text(soup.title.get_text(" ", strip=True))
    if not title:
        title = fallback_title

    text = clean_text(soup.get_text(" ", strip=True))
    date = extract_date(text[:2000], url) or extract_date(f"{title} {fallback_snippet}", url)
    summary = clean_text(text[:260]) or fallback_snippet
    return title, date, summary


def score_notice(title: str, url: str, source_type: str, date: str) -> int:
    score = 0
    blob = f"{title} {url}"
    if "2026" in blob:
        score += 30
    if date and date >= "2026-01-01":
        score += 20
    if source_type == "official":
        score += 30
    elif source_type == "aggregator":
        score += 12
    if any(k in blob for k in ["优秀大学生", "夏令营", "暑期"]):
        score += 15
    if any(k in blob for k in ["预推免", "推免"]):
        score += 8
    return score


def discover_from_web(schools: List[Dict]) -> Tuple[List[Notice], List[Notice], Dict]:
    summer: List[Notice] = []
    receive_candidates: List[Notice] = []
    stats = {
        "queries": 0,
        "results_seen": 0,
        "summer_candidates": 0,
        "receive_candidates": 0,
        "official_summer": 0,
        "aggregator_summer": 0,
    }

    queries = build_search_queries(schools)
    seen_urls = set()

    for query in queries:
        stats["queries"] += 1
        for raw_title, raw_url, raw_snippet in search_web(query):
            url = normalize_url(raw_url)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            stats["results_seen"] += 1

            source_type = source_type_for(url)
            title, date, summary = fetch_title_and_date(url, raw_title, raw_snippet)
            text_for_match = f"{title} {summary} {url}"

            school = match_school(text_for_match, schools)
            if not school:
                # Still accept CAS/institute type notices if title is current summer
                school = infer_org_from_title(title) or "未识别院校"

            if is_current_summer_notice(title, summary, url, date):
                score = score_notice(title, url, source_type, date)
                notice = Notice(
                    id=hash_id("summer", school, title, url),
                    school=school,
                    title=title,
                    url=url,
                    publish_date=date,
                    type="summer",
                    source_type=source_type,
                    source_domain=domain_of(url),
                    discovered_at=TODAY,
                    score=score,
                    summary=summary[:180],
                )
                summer.append(notice)
                stats["summer_candidates"] += 1
                if source_type == "official":
                    stats["official_summer"] += 1
                elif source_type == "aggregator":
                    stats["aggregator_summer"] += 1

            if is_receive_candidate(title, summary, url, date):
                score = score_notice(title, url, source_type, date)
                receive_candidates.append(Notice(
                    id=hash_id("receive", school, title, url),
                    school=school,
                    title=title,
                    url=url,
                    publish_date=date,
                    type="receive_candidate",
                    source_type=source_type,
                    source_domain=domain_of(url),
                    discovered_at=TODAY,
                    score=score,
                    summary=summary[:180],
                ))
                stats["receive_candidates"] += 1

    return dedupe_notices(summer), dedupe_notices(receive_candidates), stats


def infer_org_from_title(title: str) -> str:
    # Basic fallback for Chinese Academy of Sciences institutes and schools not present in schools.json
    m = re.search(r"(中国科学院[^｜|：:，,。 ]{2,30})", title)
    if m:
        return m.group(1)
    m = re.search(r"([^｜|：:，,。 ]{2,30}(大学|学院|研究院|研究所))", title)
    if m:
        return m.group(1)
    return ""


def dedupe_notices(items: List[Notice]) -> List[Notice]:
    best: Dict[str, Notice] = {}
    for n in items:
        key = normalize_url(n.url) or n.id
        if key not in best or n.score > best[key].score:
            best[key] = n
    return sorted(best.values(), key=lambda x: (x.score, x.publish_date or ""), reverse=True)


def merge_notices(existing: List[Dict], new_items: List[Notice], max_items: int = 240) -> List[Dict]:
    by_key: Dict[str, Dict] = {}
    for item in existing:
        url = normalize_url(item.get("url", ""))
        key = url or item.get("id", "")
        if key:
            by_key[key] = item

    for n in new_items:
        key = normalize_url(n.url) or n.id
        d = asdict(n)
        old = by_key.get(key)
        if old:
            # keep best title/date/source, update discovery
            old.update({k: v for k, v in d.items() if v not in ("", None)})
            old["last_seen"] = TODAY
            by_key[key] = old
        else:
            d["first_seen"] = TODAY
            d["last_seen"] = TODAY
            by_key[key] = d

    # Keep only current 2026 notices for summer; older and weak signals removed
    merged = list(by_key.values())
    cleaned = []
    for x in merged:
        title = x.get("title", "")
        url = x.get("url", "")
        date = x.get("publish_date", "")
        if x.get("type") == "summer":
            if not is_current_summer_notice(title, x.get("summary", ""), url, date):
                continue
        cleaned.append(x)

    cleaned.sort(key=lambda x: (int(x.get("score") or 0), x.get("publish_date") or ""), reverse=True)
    return cleaned[:max_items]


def load_seed_sources() -> List[Dict]:
    # Existing file can include official columns / aggregator URLs.
    seeds = load_json(SUMMER_SOURCES_PATH, [])
    if isinstance(seeds, dict):
        seeds = seeds.get("sources", [])
    return seeds if isinstance(seeds, list) else []


def crawl_seed_sources(schools: List[Dict]) -> List[Notice]:
    notices: List[Notice] = []
    seeds = load_seed_sources()
    for src in seeds:
        url = src.get("url") if isinstance(src, dict) else str(src)
        school_hint = src.get("school", "") if isinstance(src, dict) else ""
        if not url:
            continue
        resp = safe_get(url)
        if not resp or not resp.text:
            continue
        resp.encoding = resp.encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")
        base = resp.url
        links = []
        for a in soup.find_all("a"):
            href = a.get("href") or ""
            title = clean_text(a.get_text(" ", strip=True))
            if not title or not href:
                continue
            abs_url = normalize_url(urllib.parse.urljoin(base, href))
            blob = f"{title} {abs_url}"
            if any(k in blob for k in SUMMER_KEYWORDS) and ("2026" in blob or extract_date(blob, abs_url) >= "2026-01-01"):
                links.append((title, abs_url, title))

        for title, url2, snippet in links[:80]:
            full_title, date, summary = fetch_title_and_date(url2, title, snippet)
            source_type = source_type_for(url2)
            school = school_hint or match_school(f"{full_title} {summary} {url2}", schools) or infer_org_from_title(full_title) or "未识别院校"
            if is_current_summer_notice(full_title, summary, url2, date):
                notices.append(Notice(
                    id=hash_id("summer", school, full_title, url2),
                    school=school,
                    title=full_title,
                    url=url2,
                    publish_date=date,
                    type="summer",
                    source_type=source_type,
                    source_domain=domain_of(url2),
                    discovered_at=TODAY,
                    score=score_notice(full_title, url2, source_type, date) + 8,
                    summary=summary[:180],
                ))
    return dedupe_notices(notices)


def main():
    ensure_data_dir()

    schools = load_json(SCHOOLS_PATH, [])
    if isinstance(schools, dict):
        schools = schools.get("schools", [])

    old_summer = load_json(SUMMER_NOTICES_PATH, [])
    if isinstance(old_summer, dict):
        old_summer = old_summer.get("items", [])

    old_receive = load_json(RECEIVE_CANDIDATES_PATH, [])
    if isinstance(old_receive, dict):
        old_receive = old_receive.get("items", [])

    seed_summer = crawl_seed_sources(schools)
    web_summer, receive_candidates, stats = discover_from_web(schools)

    summer_merged = merge_notices(old_summer, seed_summer + web_summer, max_items=260)
    receive_merged = merge_notices(old_receive, receive_candidates, max_items=260)

    save_json(SUMMER_NOTICES_PATH, summer_merged)
    save_json(RECEIVE_CANDIDATES_PATH, receive_merged)

    report = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "current_year": CURRENT_YEAR,
        "schools_loaded": len(schools),
        "seed_summer_found": len(seed_summer),
        "web_summer_found": len(web_summer),
        "summer_saved": len(summer_merged),
        "receive_candidates_found": len(receive_candidates),
        "receive_candidates_saved": len(receive_merged),
        "stats": stats,
        "note": "前台仅展示已抓到的真实夏令营公告；接收推免专业人数仍需名单解析成功后才展示。",
    }
    save_json(DISCOVERY_LOG_PATH, report)

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
