# -*- coding: utf-8 -*-
"""
保研地图 v13：官网优先 + 第三方线索辅助 + 只展示真实抓取数据
核心原则：
1. 本校推荐名额：来自本科教务处/本科生院推免名额通知；
2. 接收推免人数：来自研究生院/研招办拟录取名单；
3. 只有官网来源能访问并能解析到专业名单，才写入前台数据；
4. 阶段性名单只标记为“已抓取接收推免人数”，不冒充全校最终总数；
5. 没有真实人数的数据不进入前台；第三方来源只作为辅助线索，并在前台标注来源类型。
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
from urllib.parse import urljoin, quote
from bs4 import BeautifulSoup
from pypdf import PdfReader

try:
    import openpyxl
except Exception:
    openpyxl = None

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHOOLS_JSON = DATA / "schools.json"
SEED_JSON = DATA / "schools_seed.json"
SCHOOL_SOURCES_JSON = DATA / "school_sources.json"
RECEIVE_SOURCES_JSON = DATA / "receive_sources.json"
SUMMER_SOURCES_JSON = DATA / "summer_camp_sources.json"
SUMMER_NOTICES_JSON = DATA / "summer_camp_notices.json"
SOURCE_DISCOVERY_JSON = DATA / "source_discovery.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
CURRENT_RECEIVE_YEAR = "2026级"
SUMMER_MIN_DATE = "2026-01-01"

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
        "receive_recommend_verify_status","receive_recommend_by_major","receive_recommend_is_complete","receive_recommend_note","receive_recommend_source_grade",
        "last_checked",
    ]:
        if k not in s:
            s[k] = [] if k == "receive_recommend_by_major" else ""

def reset_receive_fields(schools: List[Dict[str, Any]]) -> None:
    """每次运行先清空接收推免字段，避免历史旧数据/2023数据残留到前台。"""
    for s in schools:
        s["receive_recommend_total"] = ""
        s["receive_recommend_year"] = ""
        s["receive_recommend_source_name"] = ""
        s["receive_recommend_source_url"] = ""
        s["receive_recommend_verify_status"] = ""
        s["receive_recommend_by_major"] = []
        s["receive_recommend_is_complete"] = ""
        s["receive_recommend_note"] = ""

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



def parse_major_counts_from_html_tables(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    counter = Counter()
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [re.sub(r"\s+", "", c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        major_idx = None
        for ri, row in enumerate(rows[:5]):
            for ci, val in enumerate(row):
                if "专业" in val and len(val) <= 8:
                    major_idx = ci
                    start = ri + 1
                    break
            if major_idx is not None:
                break
        if major_idx is None:
            continue
        for row in rows[start:]:
            if len(row) <= major_idx:
                continue
            major = clean_major_name("", row[major_idx])
            if major and 2 <= len(major) <= 20 and not any(x in major for x in ["专业", "姓名", "学院", "备注"]):
                counter[("", major)] += 1
    rows = [{"major_code": code, "major_name": major, "count": count} for (code, major), count in counter.items()]
    rows.sort(key=lambda x: x["count"], reverse=True)
    return rows

def parse_major_counts_from_xlsx(content: bytes) -> List[Dict[str, Any]]:
    if openpyxl is None:
        return []
    counter = Counter()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception:
        return []
    for ws in wb.worksheets:
        all_rows = []
        for row in ws.iter_rows(values_only=True):
            vals = [re.sub(r"\s+", "", str(x or "")) for x in row]
            if any(vals):
                all_rows.append(vals)
        major_idx = None
        start = 0
        for ri, row in enumerate(all_rows[:15]):
            for ci, val in enumerate(row):
                if "专业" in val and len(val) <= 10:
                    major_idx = ci
                    start = ri + 1
                    break
            if major_idx is not None:
                break
        if major_idx is None:
            continue
        for row in all_rows[start:]:
            if len(row) <= major_idx:
                continue
            major = clean_major_name("", row[major_idx])
            if major and 2 <= len(major) <= 20 and not any(x in major for x in ["专业", "姓名", "学院", "备注"]):
                counter[("", major)] += 1
    rows = [{"major_code": code, "major_name": major, "count": count} for (code, major), count in counter.items()]
    rows.sort(key=lambda x: x["count"], reverse=True)
    return rows


def is_official_url(url: str) -> bool:
    u = (url or "").lower()
    return any(x in u for x in [".edu.cn", ".ac.cn", "yz.chsi.com.cn"])

def source_grade(url: str) -> str:
    return "官网来源" if is_official_url(url) else "公开来源"

def search_bing_results(query: str, limit: int = 8) -> List[Dict[str, str]]:
    """用 Bing 页面做公开网页发现。GitHub Actions 无搜索 API 时的低成本方案。"""
    results: List[Dict[str, str]] = []
    try:
        url = 'https://www.bing.com/search?q=' + quote(query)
        r = requests.get(url, timeout=25, headers={"User-Agent": UA})
        soup = BeautifulSoup(r.text, 'html.parser')
        for node in soup.select('li.b_algo'):
            a = node.select_one('h2 a')
            if not a:
                continue
            href = a.get('href') or ''
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            snippet = re.sub(r"\s+", " ", node.get_text(" ", strip=True))[:400]
            if not href.startswith('http'):
                continue
            # 排除明显无关/广告/聚合搜索页
            bad = ["baidu.com", "zhihu.com/question", "weibo.com", "bilibili.com", "taobao.com"]
            if any(x in href.lower() for x in bad):
                continue
            results.append({"title": title, "url": href, "snippet": snippet, "query": query, "grade": source_grade(href)})
            if len(results) >= limit:
                break
    except Exception:
        pass
    return results

def discover_receive_sources(schools: List[Dict[str, Any]], known_names: set, limit_schools: int = 120) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """全国自动发现 2026级接收推免拟录取名单。
    官网源优先；官网找不到时允许第三方/保研信息站作为公开来源线索。
    """
    discovered, logs = [], []
    candidates = sorted(schools, key=lambda x: int(x.get("heat_score") or 0), reverse=True)[:limit_schools]
    for s in candidates:
        name = s.get("school_name", "")
        if not name or name in known_names:
            continue
        queries = [
            f'{name} 2026 接收推荐免试研究生 拟录取名单',
            f'{name} 2026 推免生 拟录取名单 专业',
            f'{name} 2026 推荐免试研究生 接收名单',
        ]
        picked = None
        all_results = []
        for q in queries:
            res = search_bing_results(q, limit=6)
            all_results.extend(res)
        # 严格过滤：必须跟2026/推免/拟录取相关
        filtered = []
        for r in all_results:
            text = (r["title"] + " " + r.get("snippet", "") + " " + r["url"])
            if "2026" not in text:
                continue
            if not any(k in text for k in ["推免", "推荐免试", "拟录取", "接收"]):
                continue
            filtered.append(r)
        # 官网优先，其次公开来源
        official = [r for r in filtered if r["grade"] == "官网来源"]
        public = [r for r in filtered if r["grade"] != "官网来源"]
        if official:
            picked = official[0]
        elif public:
            picked = public[0]
        if picked:
            discovered.append({
                "school_name": name,
                "year": CURRENT_RECEIVE_YEAR,
                "source_name": picked["title"][:120],
                "source_url": picked["url"],
                "source_type": "auto_discovered",
                "source_grade": picked["grade"],
                "is_complete_total": "否",
                "note": "系统自动发现的公开来源；能解析出专业人数后进入前台滚动。"
            })
            known_names.add(name)
            logs.append({"school_name": name, "status": "discovered", "grade": picked["grade"], "url": picked["url"], "title": picked["title"]})
        else:
            logs.append({"school_name": name, "status": "not_found"})
    return discovered, logs

def discover_summer_camp_notices(schools: List[Dict[str, Any]], limit_schools: int = 999) -> List[Dict[str, Any]]:
    """全网发现 2026 夏令营/优秀大学生营/预推免公告。
    v14 逻辑：
    1. 不再只抓少数手工源，按院校库全量轮询；
    2. 学校官网/研究生院/学院官网优先；第三方公开来源只作为补充展示并标注；
    3. 只收 2026 年相关公告，过滤 2023/2024/2025 老公告；
    4. 每所学校最多保留 3 条，避免少数学校霸屏；
    5. 本函数依赖 Bing 公开搜索页，稳定性不如正式搜索 API。后续正式商用建议接入 Bing Search API/SerpAPI。
    """
    notices, seen = [], set()
    candidates = sorted(schools, key=lambda x: int(x.get("heat_score") or 0), reverse=True)[:limit_schools]
    for s in candidates:
        school = s.get("school_name", "")
        if not school:
            continue
        queries = [
            f'{school} 2026 夏令营 优秀大学生',
            f'{school} 2026 研究生 夏令营 推免',
            f'{school} 2026 预推免 推免 公告',
        ]
        school_items = []
        for q in queries:
            for r in search_bing_results(q, limit=8):
                text = r["title"] + " " + r.get("snippet", "") + " " + r["url"]
                if "2026" not in text:
                    continue
                if not any(k in text for k in ["夏令营", "优秀大学生", "预推免", "推免", "推荐免试"]):
                    continue
                # 排除明显往年内容，标题/摘要里出现 2023/2024 且没有明确 2026 时不要；含 2026 的保留。
                if ("2023" in text or "2024" in text) and "2026" not in text:
                    continue
                date = parse_date_from_text(text)
                if date and date < SUMMER_MIN_DATE:
                    continue
                # 没解析出完整日期但含 2026，允许进入，前台按 2026 归类。
                item = {
                    "school": school,
                    "title": r["title"][:180],
                    "date": date or "2026",
                    "url": r["url"],
                    "source_name": r.get("grade", "公开来源"),
                    "source_grade": r.get("grade", source_grade(r.get("url", ""))),
                    "last_checked": now_cn(),
                }
                key = (item["school"], item["title"], item["url"])
                if key in seen:
                    continue
                seen.add(key)
                school_items.append(item)
        # 官网源优先，公开来源补充；每校最多 3 条。
        school_items.sort(key=lambda x: (0 if x.get("source_grade") == "官网来源" else 1, str(x.get("date", ""))), reverse=False)
        notices.extend(school_items[:3])
    return notices

def collect_texts_from_receive_source(src: Dict[str, Any]) -> Tuple[str, List[str]]:
    """从接收推免来源中收集可解析文本。支持：PDF直链、公告页附件PDF、列表页自动发现公告。"""
    url = src.get("source_url") or src.get("list_url") or ""
    texts: List[str] = []
    urls: List[str] = []
    if not url:
        return "", urls

    def add_pdf(pdf_url: str):
        try:
            texts.append(get_pdf_text(pdf_url))
            urls.append(pdf_url)
        except Exception:
            pass

    def add_page(page_url: str, depth: int = 0):
        try:
            r = fetch(page_url)
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding
            soup = BeautifulSoup(r.text, "html.parser")
            page_text = re.sub(r"\s+", " ", soup.get_text("\n", strip=True))
            texts.append(page_text)
            urls.append(page_url)
            table_rows = parse_major_counts_from_html_tables(r.text)
            if table_rows:
                texts.append(' '.join([('000000 '+x['major_name']+' ')*int(x['count']) for x in table_rows]))
            # 从公告页里找 PDF / XLS / 相关名单附件。这里只解析 PDF；Excel 后续可继续加 openpyxl。
            for a in soup.find_all("a"):
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                href = a.get("href") or ""
                full = urljoin(page_url, href)
                low = full.lower().split("?")[0]
                if low.endswith(".pdf") and (not title or any(k in title for k in ["名单", "推免", "拟录取", "推荐免试", "附件"])):
                    add_pdf(full)
                if (low.endswith(".xlsx") or low.endswith(".xlsm")) and (not title or any(k in title for k in ["名单", "推免", "拟录取", "推荐免试", "附件"])):
                    try:
                        rb = fetch(full).content
                        xrows = parse_major_counts_from_xlsx(rb)
                        if xrows:
                            texts.append(' '.join([('000000 '+x['major_name']+' ')*int(x['count']) for x in xrows]))
                            urls.append(full)
                    except Exception:
                        pass
                # 列表页：自动进入“接收推免/推免拟录取”公告页，只深入一层，避免乱爬。
                if depth == 0 and not low.endswith(".pdf") and any(k in title for k in ["接收推免", "推荐免试研究生拟录取", "推免生拟录取", "推荐免试研究生"]):
                    add_page(full, depth=1)
        except Exception:
            pass

    if url.lower().split("?")[0].endswith(".pdf"):
        add_pdf(url)
    else:
        add_page(url)
    return "\n".join(texts), urls


def update_receive_recommend(schools: List[Dict[str, Any]]) -> Tuple[int, List[Dict[str, Any]]]:
    """按接收推免来源抓取专业人数。
    v11 规则：
    1. 只处理 2026级/2026 年相关来源；
    2. 同一学校多个阶段名单自动合并；
    3. 每次运行前已清空旧接收字段，避免 2023/2024 数据残留；
    4. 解析不到专业人数则不写入前台字段。
    """
    sources_db = load_json(RECEIVE_SOURCES_JSON, {"sources": []})
    base_sources = sources_db.get("sources", [])
    # 全国自动发现：官网优先，官网没有时使用公开保研/招生信息页作为辅助线索。
    existing_names = {x.get("school_name") for x in base_sources}
    auto_added, discovery_logs = discover_receive_sources(schools, existing_names, limit_schools=160)
    all_sources = base_sources + auto_added
    try:
        save_json(SOURCE_DISCOVERY_JSON, {"updated_at": now_cn(), "receive_discovery": discovery_logs})
    except Exception:
        pass
    aggregated: Dict[str, Dict[str, Any]] = {}
    failed = []

    for src in all_sources:
        name = src.get("school_name")
        url = src.get("source_url") or src.get("list_url")
        year = str(src.get("year", ""))
        if not name or not url:
            continue
        # 当前时间是 2026-05，接收推免人数以 2026级最新拟录取名单为准；不要把 2023/2024/2025 往届数据混进来。
        if year and "2026" not in year:
            failed.append({"school_name": name, "reason": "非2026级来源已跳过", "source": url})
            continue

        item = find_school_item(schools, name)
        if not item:
            failed.append({"school_name": name, "reason": "院校库未找到该学校", "source": url})
            continue

        try:
            # 支持手工核验后的专业计数：只用于官网源已经确认、但 PDF 结构难解析的场景。
            manual = src.get("manual_major_counts") or []
            if manual:
                rows = [{"major_code": str(x.get("major_code", "")), "major_name": str(x.get("major_name", "")), "count": int(x.get("count", 0))} for x in manual if int(x.get("count", 0)) > 0]
                used_urls = [url]
            else:
                text, used_urls = collect_texts_from_receive_source(src)
                # 基本年份过滤：文本里如果明确出现 2023/2024 而没有 2026，直接跳过。
                if ("2026" not in text) and ("2023" in text or "2024" in text or "2025" in text):
                    failed.append({"school_name": name, "reason": "文本未匹配2026级", "source": url})
                    continue
                rows = parse_major_counts_from_text(text)

            if not rows:
                failed.append({"school_name": name, "reason": "未解析到专业人数", "source": url})
                continue

            bucket = aggregated.setdefault(name, {
                "counter": Counter(),
                "sources": [],
                "source_names": [],
                "year": year or CURRENT_RECEIVE_YEAR,
                "is_complete": src.get("is_complete_total", "否"),
                "notes": [],
            })
            for r in rows:
                major_name = r.get("major_name") or r.get("major") or "专业"
                major_code = str(r.get("major_code") or "")
                count = int(r.get("count") or 0)
                if count > 0:
                    bucket["counter"][(major_code, major_name)] += count
            for u in used_urls or [url]:
                if u not in bucket["sources"]:
                    bucket["sources"].append(u)
            if src.get("source_name") and src.get("source_name") not in bucket["source_names"]:
                bucket["source_names"].append(src.get("source_name"))
            if src.get("note"):
                bucket["notes"].append(src.get("note"))
            if src.get("is_complete_total") == "是":
                bucket["is_complete"] = "是"
        except Exception as e:
            failed.append({"school_name": name, "reason": repr(e), "source": url})

    updated = 0
    for name, bucket in aggregated.items():
        item = find_school_item(schools, name)
        if not item:
            continue
        rows = [
            {"major_code": code, "major_name": major, "count": count}
            for (code, major), count in bucket["counter"].items()
        ]
        rows.sort(key=lambda x: x["count"], reverse=True)
        total = sum(int(r["count"]) for r in rows)
        if total <= 0:
            continue
        item["receive_recommend_total"] = str(total)
        item["receive_recommend_year"] = bucket["year"] or CURRENT_RECEIVE_YEAR
        item["receive_recommend_source_name"] = "；".join(bucket["source_names"][:2])
        item["receive_recommend_source_url"] = bucket["sources"][0] if bucket["sources"] else ""
        item["receive_recommend_verify_status"] = "已解析"
        item["receive_recommend_source_grade"] = source_grade(item["receive_recommend_source_url"]) if item.get("receive_recommend_source_url") else "官网来源"
        item["receive_recommend_by_major"] = rows[:40]
        item["receive_recommend_is_complete"] = bucket["is_complete"]
        item["receive_recommend_note"] = "；".join(bucket["notes"][:2])
        item["last_checked"] = now_cn()
        updated += 1

    return updated, failed

def parse_date_from_text(text: str) -> str:
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text or "")
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})", text or "")
    if m:
        y, mo = m.groups()
        return f"{y}-{int(mo):02d}"
    return ""

def scrape_summer_camp_notices() -> Tuple[int, List[Dict[str, Any]]]:
    """抓取夏令营/推免公告栏目。只抓标题、链接、日期，不生成未经来源确认的数据。"""
    sources_db = load_json(SUMMER_SOURCES_JSON, {"sources": []})
    existing = load_json(SUMMER_NOTICES_JSON, {"notices": []}).get("notices", [])
    notices: List[Dict[str, Any]] = []
    seen = set()
    for n in existing:
        if n.get("school") == "系统提示":
            continue
        d = str(n.get("date") or "")
        title = str(n.get("title") or "")
        if not d or d < SUMMER_MIN_DATE:
            continue
        key = (n.get("school", ""), n.get("title", ""), n.get("url", ""))
        if key not in seen and (n.get("title") or n.get("url")):
            seen.add(key)
            notices.append(n)
    for src in sources_db.get("sources", []):
        school = src.get("school") or src.get("school_name") or ""
        url = src.get("list_url") or src.get("source_url") or ""
        keywords = src.get("keywords") or ["夏令营", "推免", "预推免", "推荐免试", "优秀大学生"]
        if not url:
            continue
        try:
            r = fetch(url)
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding
            soup = BeautifulSoup(r.text, "html.parser")
            # 单页公告源：如果页面本身就是 2026 年夏令营/推免公告，也写入滚动条。
            page_title = ""
            if soup.find(["h1", "h2", "h3"]):
                page_title = re.sub(r"\s+", " ", soup.find(["h1", "h2", "h3"]).get_text(" ", strip=True))
            if not page_title and soup.title:
                page_title = re.sub(r"\s+", " ", soup.title.get_text(" ", strip=True))
            page_text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            page_date = parse_date_from_text(page_text[:1000])
            if page_title and any(k in page_title for k in keywords) and page_date >= SUMMER_MIN_DATE:
                item = {"school": school, "title": page_title, "date": page_date, "url": url, "source_name": src.get("source_name", ""), "last_checked": now_cn()}
                key = (item["school"], item["title"], item["url"])
                if key not in seen:
                    seen.add(key)
                    notices.append(item)
            for a in soup.find_all("a"):
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                href = a.get("href") or ""
                if not title or len(title) < 4:
                    continue
                if not any(k in title for k in keywords):
                    continue
                full_url = urljoin(url, href)
                context = title + " " + re.sub(r"\s+", " ", a.parent.get_text(" ", strip=True) if a.parent else "")
                date = parse_date_from_text(context)
                # 当前页面按 2026-05-22 使用，只展示 2026 年以来的新公告，避免混入 2023/2024/2025 老公告。
                if not date or date < SUMMER_MIN_DATE:
                    continue
                item = {
                    "school": school,
                    "title": title,
                    "date": date,
                    "url": full_url,
                    "source_name": src.get("source_name", ""),
                    "last_checked": now_cn(),
                }
                key = (item["school"], item["title"], item["url"])
                if key not in seen:
                    seen.add(key)
                    notices.append(item)
        except Exception as e:
            # 源页面失败时不清空原公告，避免前台空白
            continue

    # 全网自动发现夏令营/推免公告：官网优先，第三方公开来源可作为补充并标注来源类型。
    try:
        discovered_notices = discover_summer_camp_notices(load_json(SCHOOLS_JSON, {"schools": []}).get("schools", []), limit_schools=999)
        for item in discovered_notices:
            key = (item.get("school", ""), item.get("title", ""), item.get("url", ""))
            if key not in seen:
                seen.add(key)
                notices.append(item)
    except Exception:
        pass
    def date_key(n: Dict[str, Any]) -> str:
        d = n.get("date") or "0000-00-00"
        return d
    notices.sort(key=date_key, reverse=True)
    save_json(SUMMER_NOTICES_JSON, {"updated_at": now_cn(), "notices": notices[:240]})
    return len(notices[:240]), notices[:5]

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
    reset_receive_fields(schools)

    quota_updated, quota_failed = update_recommend_quota(schools)
    receive_updated, receive_failed = update_receive_recommend(schools)
    summer_notice_count, summer_notice_samples = scrape_summer_camp_notices()

    db["schools"] = schools
    db["updated_at"] = now_cn()
    db["quota_updated_count"] = quota_updated
    db["receive_updated_count"] = receive_updated
    db["summer_notice_count"] = summer_notice_count
    db["quota_failed_samples"] = quota_failed[:10]
    db["receive_failed_samples"] = receive_failed[:10]
    db["note"] = "当前时间按2026-05-22口径：接收推免人数优先采集2026级官网数据；官网抓不到时使用公开保研/招生信息源辅助发现并标注来源；夏令营公告改为右侧模块滚动展示，按全国院校库全量搜索2026年以来或标题含2026的夏令营/预推免公告。"
    save_json(SCHOOLS_JSON, db)
    print(f"recommendation quota updated: {quota_updated}, receive recommend updated: {receive_updated}, summer notices: {summer_notice_count}")
    if quota_failed:
        print("quota failed samples:", quota_failed[:3])
    if receive_failed:
        print("receive failed samples:", receive_failed[:3])

if __name__ == "__main__":
    main()
