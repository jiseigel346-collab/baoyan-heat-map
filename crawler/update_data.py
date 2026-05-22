# -*- coding: utf-8 -*-
"""
保研地图 v11：最新数据过滤 + 全国接收推免动态慢速滚动
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
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHOOLS_JSON = DATA / "schools.json"
SEED_JSON = DATA / "schools_seed.json"
SCHOOL_SOURCES_JSON = DATA / "school_sources.json"
RECEIVE_SOURCES_JSON = DATA / "receive_sources.json"
SUMMER_SOURCES_JSON = DATA / "summer_camp_sources.json"
SUMMER_NOTICES_JSON = DATA / "summer_camp_notices.json"

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
        "receive_recommend_verify_status","receive_recommend_by_major","receive_recommend_is_complete","receive_recommend_note",
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
            # 从公告页里找 PDF / XLS / 相关名单附件。这里只解析 PDF；Excel 后续可继续加 openpyxl。
            for a in soup.find_all("a"):
                title = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
                href = a.get("href") or ""
                full = urljoin(page_url, href)
                low = full.lower().split("?")[0]
                if low.endswith(".pdf") and (not title or any(k in title for k in ["名单", "推免", "拟录取", "推荐免试", "附件"])):
                    add_pdf(full)
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
    aggregated: Dict[str, Dict[str, Any]] = {}
    failed = []

    for src in sources_db.get("sources", []):
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
        item["receive_recommend_verify_status"] = "官网已核验"
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
    def date_key(n: Dict[str, Any]) -> str:
        d = n.get("date") or "0000-00-00"
        return d
    notices.sort(key=date_key, reverse=True)
    save_json(SUMMER_NOTICES_JSON, {"updated_at": now_cn(), "notices": notices[:60]})
    return len(notices[:60]), notices[:5]

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
    db["note"] = "当前时间按2026-05-22口径：接收推免人数仅保留2026级官网可核验数据；夏令营公告仅保留2026年以来或标题含2026的公告。"
    save_json(SCHOOLS_JSON, db)
    print(f"recommendation quota updated: {quota_updated}, receive recommend updated: {receive_updated}, summer notices: {summer_notice_count}")
    if quota_failed:
        print("quota failed samples:", quota_failed[:3])
    if receive_failed:
        print("receive failed samples:", receive_failed[:3])

if __name__ == "__main__":
    main()
