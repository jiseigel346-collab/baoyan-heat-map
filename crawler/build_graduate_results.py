# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from openpyxl import Workbook
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "graduate_2026"
NOOBDREAM_URL = "https://noobdream.com/zexiao/?filter="
DEFAULT_OFFICIAL_SAMPLE_URL = "https://yjszs.uir.cn/info/1421/7541.htm"


HEADERS = {
    "专业名称", "初试科目", "分数线", "复试人数", "进复试总分均分", "进批总分均分", "进复试单科均分",
    "录取人数", "拟录取人数", "预计招生人数", "拟录取分数", "--", "筛选查看热度排行榜",
}
INSTITUTION_RE = r"(?:大学|学院|研究院|研究所|科学院|社科院|党校|中心|学校)"


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def is_school_line(lines: list[str], index: int) -> str | None:
    line = lines[index]
    if line in HEADERS or line.startswith("|"):
        return None
    match = re.match(r"^(.+?" + INSTITUTION_RE + r")(?:\s+(?:Top\d+|985|211|双一流|科研院所|[A-Z]+))?$", line)
    if not match:
        return None
    for next_index in range(index + 1, min(index + 4, len(lines))):
        if re.fullmatch(r"\d{1,3}", lines[next_index]):
            return match.group(1).strip()
        if lines[next_index] == "专业名称":
            return None
    return None


def parse_noobdream_text(path: Path) -> list[dict[str, Any]]:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows: list[dict[str, Any]] = []
    school = ""
    department = ""
    pending_specialties: list[str] = []
    last_non_header = ""

    for index, line in enumerate(lines):
        parsed_school = is_school_line(lines, index)
        if parsed_school:
            school = parsed_school
            department = ""
            pending_specialties = []
            last_non_header = ""
            continue
        if not school:
            continue
        if line == "专业名称":
            if last_non_header and last_non_header not in HEADERS and not last_non_header.startswith("|") and not re.fullmatch(r"\d+", last_non_header):
                department = last_non_header
            pending_specialties = []
            continue
        if line in HEADERS or re.fullmatch(r"\d+", line):
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= {"-", " "} for cell in cells):
                continue
            if "【" not in line or "】" not in line:
                continue
            bracket_cells = [cell for cell in cells if "【" in cell and "】" in cell]
            admission_score_raw = bracket_cells[-1] if bracket_cells else ""
            score_match = re.search(r"(?:(\d+)\s*)?【(\d+)-(\d+)】", admission_score_raw)
            if not score_match:
                continue
            specialty = ""
            if cells and not re.match(r"^[\dNn]", cells[0]) and "【" not in cells[0] and not re.search(r"\d{4,5}", cells[0]) and cells[0] != "初试科目":
                specialty = cells[0]
            elif pending_specialties:
                specialty = pending_specialties.pop(0)

            retest_line_raw = ""
            for cell in cells:
                if "、" in cell or "N诺" in cell:
                    continue
                retest_match = re.match(r"^(\d{3})(?:\D|$)", cell)
                if retest_match:
                    retest_line_raw = retest_match.group(1)
                    break

            rows.append({
                "year": "2026",
                "school_name": school,
                "department": department,
                "specialty_name": specialty,
                "score_level": "第三方拟录取最低分",
                "result_min_score": score_match.group(2),
                "final_admission_min_score": score_match.group(2),
                "final_admission_max_score": score_match.group(3),
                "admission_avg_score": score_match.group(1) or "",
                "school_retest_min_score": retest_line_raw,
                "admission_score_raw": admission_score_raw,
                "source_type": "第三方聚合-N诺考研",
                "source_url": NOOBDREAM_URL,
                "verify_status": "第三方聚合结果，建议回到招生单位拟录取名单复核",
                "notes": "仅提取原文中带“【最低-最高】”格式的拟录取分数。",
            })
            continue

        if line not in HEADERS and not line.startswith("|") and not re.fullmatch(r"\d+", line):
            pending_specialties.append(line)
            last_non_header = line
    return rows


def parse_official_admission_table(url: str, school_name: str) -> list[dict[str, Any]]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    grouped: dict[str, list[int]] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [cell.get_text(" ", strip=True).replace(" ", "") for cell in rows[0].find_all(["td", "th"])]
        specialty_index = score_index = admission_status_index = remark_index = None
        for index, header_text in enumerate(header):
            if specialty_index is None and ("专业名称" in header_text or header_text == "专业"):
                specialty_index = index
            if score_index is None and "初试" in header_text and ("成绩" in header_text or header_text == "初试"):
                score_index = index
            if admission_status_index is None and ("是否拟录取" in header_text or header_text in {"录取状态", "拟录取"}):
                admission_status_index = index
            if remark_index is None and ("备注" in header_text or "拟录取" in header_text):
                remark_index = index
        if specialty_index is None or score_index is None:
            continue
        for tr in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if len(cells) <= max(specialty_index, score_index):
                continue
            status_text = ""
            if admission_status_index is not None and len(cells) > admission_status_index:
                status_text += cells[admission_status_index]
            if remark_index is not None and len(cells) > remark_index:
                status_text += cells[remark_index]
            if status_text and any(text in status_text for text in ["否", "未录取", "不录取", "不合格"]):
                continue
            if admission_status_index is not None and "是" not in status_text and "拟录取" not in status_text and "待录取" not in status_text:
                continue
            specialty = cells[specialty_index]
            scores = [int(score) for score in re.findall(r"\b\d{3}\b", cells[score_index])]
            if specialty and scores:
                grouped.setdefault(specialty, []).extend(scores)

    rows = []
    for specialty, scores in sorted(grouped.items()):
        rows.append({
            "year": "2026",
            "school_name": school_name,
            "department": "",
            "specialty_name": specialty,
            "score_level": "官网拟录取最低分",
            "result_min_score": min(scores),
            "final_admission_min_score": min(scores),
            "final_admission_max_score": max(scores),
            "admission_avg_score": round(sum(scores) / len(scores), 2),
            "school_retest_min_score": "",
            "admission_score_raw": f"{len(scores)}人，初试分范围 {min(scores)}-{max(scores)}",
            "source_type": "招生单位官网拟录取名单",
            "source_url": url,
            "verify_status": "官网表格按专业聚合",
            "notes": "从招生单位官网拟录取名单按专业计算初试最低分。",
        })
    return rows


def official_rows_from_grouped(grouped: dict[str, list[int]], school_name: str, source_url: str, source_type: str, note: str) -> list[dict[str, Any]]:
    rows = []
    for specialty, scores in sorted(grouped.items()):
        rows.append({
            "year": "2026",
            "school_name": school_name,
            "department": "",
            "specialty_name": specialty,
            "score_level": "官网拟录取最低分",
            "result_min_score": min(scores),
            "final_admission_min_score": min(scores),
            "final_admission_max_score": max(scores),
            "admission_avg_score": round(sum(scores) / len(scores), 2),
            "school_retest_min_score": "",
            "admission_score_raw": f"{len(scores)}人，初试分范围 {min(scores)}-{max(scores)}",
            "source_type": source_type,
            "source_url": source_url,
            "verify_status": "官网来源按专业聚合",
            "notes": note,
        })
    return rows


def parse_official_markdown_table(path: Path, school_name: str, source_url: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[int]] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    specialty_index = score_index = admission_status_index = remark_index = None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        normalized = [cell.replace(" ", "") for cell in cells]
        if any("初试" in cell for cell in normalized) and any(("专业名称" in cell or cell == "专业") for cell in normalized):
            header = normalized
            specialty_index = score_index = admission_status_index = remark_index = None
            for index, header_text in enumerate(header):
                if specialty_index is None and ("专业名称" in header_text or header_text == "专业"):
                    specialty_index = index
                if score_index is None and "初试" in header_text and ("成绩" in header_text or header_text == "初试"):
                    score_index = index
                if admission_status_index is None and ("是否拟录取" in header_text or header_text in {"录取状态", "拟录取"}):
                    admission_status_index = index
                if remark_index is None and ("备注" in header_text or "拟录取" in header_text):
                    remark_index = index
            continue
        if header is None or specialty_index is None or score_index is None:
            continue
        if len(cells) <= max(specialty_index, score_index):
            continue
        status_text = ""
        if admission_status_index is not None and len(cells) > admission_status_index:
            status_text += cells[admission_status_index]
        if remark_index is not None and len(cells) > remark_index:
            status_text += cells[remark_index]
        if status_text and any(text in status_text for text in ["否", "未录取", "不录取", "不合格"]):
            continue
        if admission_status_index is not None and "是" not in status_text and "拟录取" not in status_text and "待录取" not in status_text:
            continue
        specialty = cells[specialty_index]
        scores = [int(score) for score in re.findall(r"\b\d{3}\b", cells[score_index])]
        if specialty and scores:
            grouped.setdefault(specialty, []).extend(scores)
    return official_rows_from_grouped(grouped, school_name, source_url, "招生单位官网拟录取名单(WebFetch文本)", "从官网页面文本表格按专业计算初试最低分。")


def parse_official_pdf(url: str, school_name: str) -> list[dict[str, Any]]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    reader = PdfReader(io.BytesIO(response.content))
    text = re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))
    sections = []
    for match in re.finditer(r"2026年([^（\s]+)（(\d{6})）拟录取名单", text):
        sections.append((match.start(), match.group(1), match.group(2)))
    grouped: dict[str, list[int]] = {}
    for index, (start, specialty, _code) in enumerate(sections):
        end = sections[index + 1][0] if index + 1 < len(sections) else len(text)
        section = text[start:end]
        scores = [int(score) for score in re.findall(r"\b\d{12,15}\s+\S+\s+(\d{3})\b", section)]
        if scores and not section.startswith("2025年"):
            grouped.setdefault(specialty, []).extend(scores)
    return official_rows_from_grouped(grouped, school_name, url, "招生单位官网拟录取名单PDF", "从官网 PDF 按专业段落计算初试最低分。")


def parse_peking_medical_text(path: Path, source_url: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    normalized = re.sub(r"[|]", " ", text)
    normalized = re.sub(r"\s+", " ", normalized)
    pattern = re.compile(
        r"(100016\d{9})\s+\S+\s+\d{3}-.+?-(\d{6})-([^\s|]+(?:\s+[^\s|]+){0,3}?)\s+"
        r"(?:学术学位|专业学位)\s+[^\s]+\s+(\d{3})\s+\d{2,3}\.\d{2}\s+\d{2,3}\.\d{2}"
    )
    grouped: dict[str, list[int]] = {}
    for match in pattern.finditer(normalized):
        specialty_code = match.group(2)
        specialty_name = re.sub(r"\s+", "", match.group(3).strip())
        score = int(match.group(4))
        key = f"{specialty_code} {specialty_name}"
        grouped.setdefault(key, []).append(score)
    return official_rows_from_grouped(grouped, "北京大学医学部", source_url, "招生单位官网拟录取名单PDF(WebFetch文本)", "从北京大学医学部 PDF 文本按专业/方向计算初试最低分。")


def parse_ynau_agriculture_text(path: Path, source_url: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"\b\d+\s+106766\d+\s+\S+\s+006\s+农学与生物技术学院\s+(\d{6})\s+"
        r"(.+?)\s+\d{2}\s+(?:全日制|非全日制)\s+(\d{3})\s+.*?待录取"
    )
    grouped: dict[str, list[int]] = {}
    for match in pattern.finditer(normalized):
        specialty_code = match.group(1)
        specialty_name = re.sub(r"\s+", "", match.group(2).strip())
        score = int(match.group(3))
        key = f"{specialty_code} {specialty_name}"
        grouped.setdefault(key, []).append(score)
    return official_rows_from_grouped(grouped, "云南农业大学农学与生物技术学院", source_url, "招生单位官网拟录取名单PDF(WebFetch文本)", "从云南农业大学学院 PDF 文本按专业计算初试最低分。")


def parse_jlu_nursing_attachments(page_url: str) -> list[dict[str, Any]]:
    response = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    grouped: dict[str, list[int]] = {}
    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        if ".pdf" not in title.lower():
            continue
        href = requests.compat.urljoin(page_url, link["href"])
        pdf_response = None
        for _attempt in range(3):
            try:
                pdf_response = requests.get(href, headers={"User-Agent": "Mozilla/5.0", "Referer": page_url}, timeout=45)
                pdf_response.raise_for_status()
                break
            except Exception:
                pdf_response = None
        if pdf_response is None:
            continue
        reader = PdfReader(io.BytesIO(pdf_response.content))
        text = re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))
        for match in re.finditer(r"\b\d+\s+([\u4e00-\u9fa5]+)\s+(\d{4})\s+\S+\s+(\d{3})\s+\d{2,3}\.\d{2}\s+\d{2,3}\.\d{2}\s+拟录取", text):
            specialty_name = match.group(1)
            specialty_code = match.group(2)
            score = int(match.group(3))
            grouped.setdefault(f"{specialty_code} {specialty_name}", []).append(score)
    return official_rows_from_grouped(grouped, "吉林大学护理学院", page_url, "招生单位官网拟录取名单PDF附件", "从吉林大学护理学院 PDF 附件按专业计算初试最低分。")


def parse_lishui_pdf_attachments(page_url: str) -> list[dict[str, Any]]:
    response = requests.get(page_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    grouped: dict[str, list[int]] = {}
    for link in soup.find_all("a", href=True):
        title = link.get_text(" ", strip=True)
        href = requests.compat.urljoin(page_url, link["href"])
        if ".pdf" not in (title + href).lower():
            continue
        pdf_response = requests.get(href, headers={"User-Agent": "Mozilla/5.0", "Referer": page_url}, timeout=30, verify=False)
        pdf_response.raise_for_status()
        reader = PdfReader(io.BytesIO(pdf_response.content))
        text = re.sub(r"\s+", " ", "\n".join(page.extract_text() or "" for page in reader.pages))
        code_match = re.search(r"复试专业代码[:：]\s*(\d{6})", text)
        name_match = re.search(r"复试专业名称[:：]\s*([^◆第序]+?)(?:\s+同等学力|\s+序号|\s+◆|\s+第\s*\d|\s*$)", text)
        if not code_match:
            file_match = re.search(r"(\d{6})([^/\\s]+?)(?:一志愿|待录取|拟录取)", title)
            if not file_match:
                continue
            specialty_code = file_match.group(1)
            specialty_name = file_match.group(2)
        else:
            specialty_code = code_match.group(1)
            specialty_name = name_match.group(1).strip() if name_match else title
        scores = []
        for match in re.finditer(r"\b\d+\s+103526\d+\s+\S+\s+(\d{3})(?:\.00)?\s+\d{2,3}\.\d{1,2}\s+\d{2,3}\.\d{1,2}\s+\d+\s+(?:/|[\d\s]+)?\s*(?:是|拟录取)\b", text):
            scores.append(int(match.group(1)))
        if scores:
            clean_name = re.sub(r"\s+", "", specialty_name)
            grouped.setdefault(f"{specialty_code} {clean_name}", []).extend(scores)
    return official_rows_from_grouped(grouped, "丽水学院", page_url, "招生单位官网拟录取名单PDF附件", "从丽水学院 PDF 附件按专业计算初试最低分。")


def parse_guangzhou_management_pdf(url: str) -> list[dict[str, Any]]:
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    response.raise_for_status()
    reader = PdfReader(io.BytesIO(response.content))
    text = re.sub(r"\s+", " ", " ".join(page.extract_text() or "" for page in reader.pages))
    known_majors = ["管理科学与工程", "技术经济及管理", "企业管理", "会计学"]
    grouped: dict[str, list[int]] = {}
    for match in re.finditer(r"\b\d+\s+\S+\s+(.+?)\s+(\d{3})\.00\s+\d{2,3}\.\d{2}\s+\d{2,3}\.\d{2}\s+\d+\s+是\b", text):
        prefix = match.group(1)
        score = int(match.group(2))
        for major in sorted(known_majors, key=len, reverse=True):
            if prefix.endswith(major):
                grouped.setdefault(major, []).append(score)
                break
    return official_rows_from_grouped(grouped, "广州大学管理学院", url, "招生单位官网调剂待录取名单PDF", "从广州大学管理学院调剂待录取 PDF 按调入专业计算初试最低分。")


def write_workbook(path: Path, sheets: dict[str, list[dict[str, Any]]]) -> None:
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    for sheet_name, rows in sheets.items():
        ws = wb.create_sheet(sheet_name[:31])
        if not rows:
            ws.append(["empty"])
            continue
        headers = list(rows[0].keys())
        ws.append(headers)
        for row in rows:
            ws.append([row.get(header, "") for header in headers])
    wb.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build user-facing 2026 graduate score result tables.")
    parser.add_argument("--noobdream-text", type=Path, help="Markdown/text export of N诺择校数据 page.")
    parser.add_argument("--official-sample-url", default=DEFAULT_OFFICIAL_SAMPLE_URL, help="Official admitted list sample URL.")
    parser.add_argument("--official-sample-school", default="国际关系学院", help="School name for the official sample URL.")
    parser.add_argument("--official-web-source", action="append", default=[], help="Official web table source as school|url.")
    parser.add_argument("--official-markdown-source", action="append", default=[], help="Official markdown source as school|url|path.")
    parser.add_argument("--official-pdf-source", action="append", default=[], help="Official PDF source as school|url.")
    parser.add_argument("--peking-medical-text-source", type=Path, help="WebFetch text export of Peking University Health Science Center admitted PDF.")
    parser.add_argument("--ynau-agriculture-text-source", type=Path, help="WebFetch text export of YNAU agriculture admitted PDF.")
    parser.add_argument("--jlu-nursing-url", help="JLU nursing admitted-list page with PDF attachments.")
    parser.add_argument("--lishui-url", help="Lishui University admitted-list page with PDF attachments.")
    parser.add_argument("--guangzhou-management-pdf", help="Guangzhou University management-school adjustment admitted PDF.")
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    updated_at = now_iso()
    third_party_rows = parse_noobdream_text(args.noobdream_text) if args.noobdream_text else []
    official_rows = parse_official_admission_table(args.official_sample_url, args.official_sample_school) if args.official_sample_url else []
    for source in args.official_web_source:
        school, url = source.split("|", 1)
        official_rows.extend(parse_official_admission_table(url, school))
    for source in args.official_markdown_source:
        school, url, path = source.split("|", 2)
        official_rows.extend(parse_official_markdown_table(Path(path), school, url))
    for source in args.official_pdf_source:
        school, url = source.split("|", 1)
        official_rows.extend(parse_official_pdf(url, school))
    if args.peking_medical_text_source:
        official_rows.extend(parse_peking_medical_text(args.peking_medical_text_source, "https://yjsy.bjmu.edu.cn/docs/2026-04/296b738c12d741eb8849becb5aafbca0.pdf"))
    if args.ynau_agriculture_text_source:
        official_rows.extend(parse_ynau_agriculture_text(args.ynau_agriculture_text_source, "https://yjs.ynau.edu.cn/006nongxueyushengwujishuxueyuan.pdf"))
    if args.jlu_nursing_url:
        official_rows.extend(parse_jlu_nursing_attachments(args.jlu_nursing_url))
    if args.lishui_url:
        official_rows.extend(parse_lishui_pdf_attachments(args.lishui_url))
    if args.guangzhou_management_pdf:
        official_rows.extend(parse_guangzhou_management_pdf(args.guangzhou_management_pdf))

    if third_party_rows:
        write_csv(DATA / "admission_min_scores_third_party.csv", third_party_rows)
        write_json(DATA / "admission_min_scores_third_party.json", {
            "updated_at": updated_at,
            "source_url": NOOBDREAM_URL,
            "source_type": "第三方聚合-N诺考研",
            "items": third_party_rows,
        })
    if official_rows:
        write_csv(DATA / "admission_min_scores_official_sample.csv", official_rows)
        write_json(DATA / "admission_min_scores_official_sample.json", {
            "updated_at": updated_at,
            "source_url": args.official_sample_url,
            "source_type": "招生单位官网拟录取名单",
            "items": official_rows,
        })

    national_rows = read_csv(DATA / "national_lines.csv")
    discipline_rows = read_csv(DATA / "discipline_categories.csv")
    specialty_rows = read_csv(DATA / "specialty_catalog.csv")
    school_base_rows = read_csv(DATA / "school_specialty_score_base.csv")
    nationwide_notice_rows = read_csv(DATA / "yanshuoshi_2026_notice_details.csv")
    kybang_file_rows = read_csv(DATA / "kybang_2026_file_index.csv")
    coverage_audit_rows = []
    coverage_audit_path = DATA / "coverage_audit.json"
    if coverage_audit_path.exists():
        audit = json.loads(coverage_audit_path.read_text(encoding="utf-8"))
        coverage_audit_rows = [{"field": key, "value": json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value} for key, value in audit.items()]

    summary_rows = [
        {"item": "国家线", "rows": len(national_rows), "file": "national_lines.csv"},
        {"item": "14门类-一级学科/专业学位类别", "rows": len(discipline_rows), "file": "discipline_categories.csv"},
        {"item": "研招网专业目录增量样本", "rows": len(specialty_rows), "file": "specialty_catalog.csv"},
        {"item": "研招网学校-专业底表增量样本", "rows": len(school_base_rows), "file": "school_specialty_score_base.csv"},
        {"item": "全国2026拟录取/复试结果公告索引", "rows": len(nationwide_notice_rows), "file": "yanshuoshi_2026_notice_details.csv"},
        {"item": "考友帮2026文件名/目录索引", "rows": len(kybang_file_rows), "file": "kybang_2026_file_index.csv"},
        {"item": "第三方拟录取最低分样本", "rows": len(third_party_rows), "file": "admission_min_scores_third_party.csv"},
        {"item": "官网拟录取最低分样本", "rows": len(official_rows), "file": "admission_min_scores_official_sample.csv"},
    ]

    write_csv(DATA / "result_summary.csv", summary_rows)
    write_json(DATA / "result_summary.json", {
        "updated_at": updated_at,
        "note": "result_min_score 优先使用可解析的拟录取最低分；第三方来源需回到招生单位官网复核。",
        "items": summary_rows,
    })

    write_workbook(DATA / "graduate_2026_result_tables.xlsx", {
        "结果说明": summary_rows,
        "覆盖核查": coverage_audit_rows,
        "全国拟录取公告索引": nationwide_notice_rows,
        "考友帮文件名索引": kybang_file_rows,
        "官网录取最低分样本": official_rows,
        "第三方录取最低分样本": third_party_rows,
        "14门类国家线": national_rows,
        "门类学科专业类别": discipline_rows,
        "研招网学校专业底表": school_base_rows,
        "研招网专业目录样本": specialty_rows,
    })
    print(json.dumps({"updated_at": updated_at, "summary": summary_rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
