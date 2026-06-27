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
        specialty_index = score_index = None
        for index, header_text in enumerate(header):
            if specialty_index is None and ("专业名称" in header_text or header_text == "专业"):
                specialty_index = index
            if score_index is None and "初试" in header_text and ("成绩" in header_text or header_text == "初试"):
                score_index = index
        if specialty_index is None or score_index is None:
            continue
        for tr in rows[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            if len(cells) <= max(specialty_index, score_index):
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
    specialty_index = score_index = None
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        normalized = [cell.replace(" ", "") for cell in cells]
        if any("初试" in cell for cell in normalized) and any(("专业名称" in cell or cell == "专业") for cell in normalized):
            header = normalized
            specialty_index = score_index = None
            for index, header_text in enumerate(header):
                if specialty_index is None and ("专业名称" in header_text or header_text == "专业"):
                    specialty_index = index
                if score_index is None and "初试" in header_text and ("成绩" in header_text or header_text == "初试"):
                    score_index = index
            continue
        if header is None or specialty_index is None or score_index is None:
            continue
        if len(cells) <= max(specialty_index, score_index):
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
    parser.add_argument("--official-markdown-source", action="append", default=[], help="Official markdown source as school|url|path.")
    parser.add_argument("--official-pdf-source", action="append", default=[], help="Official PDF source as school|url.")
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    updated_at = now_iso()
    third_party_rows = parse_noobdream_text(args.noobdream_text) if args.noobdream_text else []
    official_rows = parse_official_admission_table(args.official_sample_url, args.official_sample_school) if args.official_sample_url else []
    for source in args.official_markdown_source:
        school, url, path = source.split("|", 2)
        official_rows.extend(parse_official_markdown_table(Path(path), school, url))
    for source in args.official_pdf_source:
        school, url = source.split("|", 1)
        official_rows.extend(parse_official_pdf(url, school))

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
