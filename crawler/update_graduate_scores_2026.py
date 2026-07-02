# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "graduate_2026"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
BASE = "https://yz.chsi.com.cn"
NATIONAL_LINE_URL = "https://yz.chsi.com.cn/kyzx/kp/202602/20260228/2293449093.html"
CATALOG_URL = "https://yz.chsi.com.cn/zsml/"
LIMIT_WARNINGS: list[dict[str, Any]] = []


NATIONAL_LINES = [
    {"line_key": "01_all", "category_code": "01", "category_name": "哲学", "scope": "各学科专业", "a_total": 326, "a_single_100": 41, "a_single_gt100": 62, "b_total": 316, "b_single_100": 38, "b_single_gt100": 57, "minority_total": 261},
    {"line_key": "02_all", "category_code": "02", "category_name": "经济学", "scope": "各学科专业", "a_total": 324, "a_single_100": 40, "a_single_gt100": 60, "b_total": 314, "b_single_100": 37, "b_single_gt100": 56, "minority_total": 259},
    {"line_key": "03_all", "category_code": "03", "category_name": "法学", "scope": "各学科专业", "a_total": 321, "a_single_100": 40, "a_single_gt100": 60, "b_total": 311, "b_single_100": 37, "b_single_gt100": 56, "minority_total": 257},
    {"line_key": "04_pe", "category_code": "04", "category_name": "教育学", "scope": "体育学[0403]、体育[0452]", "a_total": 310, "a_single_100": 38, "a_single_gt100": 114, "b_total": 300, "b_single_100": 35, "b_single_gt100": 105, "minority_total": 248},
    {"line_key": "04_education", "category_code": "04", "category_name": "教育学", "scope": "教育[0451]、国际中文教育[0453]", "a_total": 347, "a_single_100": 48, "a_single_gt100": 72, "b_total": 337, "b_single_100": 45, "b_single_gt100": 68, "minority_total": 278},
    {"line_key": "04_other", "category_code": "04", "category_name": "教育学", "scope": "其他学科专业", "a_total": 347, "a_single_100": 48, "a_single_gt100": 144, "b_total": 337, "b_single_100": 45, "b_single_gt100": 135, "minority_total": 278},
    {"line_key": "05_all", "category_code": "05", "category_name": "文学", "scope": "各学科专业", "a_total": 354, "a_single_100": 48, "a_single_gt100": 72, "b_total": 344, "b_single_100": 45, "b_single_gt100": 68, "minority_total": 283},
    {"line_key": "06_all", "category_code": "06", "category_name": "历史学", "scope": "各学科专业", "a_total": 341, "a_single_100": 45, "a_single_gt100": 135, "b_total": 331, "b_single_100": 42, "b_single_gt100": 126, "minority_total": 273},
    {"line_key": "07_all", "category_code": "07", "category_name": "理学", "scope": "各学科专业", "a_total": 275, "a_single_100": 35, "a_single_gt100": 53, "b_total": 265, "b_single_100": 32, "b_single_gt100": 48, "minority_total": 220},
    {"line_key": "08_care", "category_code": "08", "category_name": "工学", "scope": "工学照顾专业", "a_total": 251, "a_single_100": 33, "a_single_gt100": 50, "b_total": 241, "b_single_100": 30, "b_single_gt100": 45, "minority_total": 201},
    {"line_key": "08_other", "category_code": "08", "category_name": "工学", "scope": "其他学科专业", "a_total": 264, "a_single_100": 35, "a_single_gt100": 53, "b_total": 254, "b_single_100": 32, "b_single_gt100": 48, "minority_total": 211},
    {"line_key": "09_all", "category_code": "09", "category_name": "农学", "scope": "各学科专业", "a_total": 240, "a_single_100": 33, "a_single_gt100": 50, "b_total": 230, "b_single_100": 30, "b_single_gt100": 45, "minority_total": 192},
    {"line_key": "10_all", "category_code": "10", "category_name": "医学", "scope": "各学科专业", "a_total": 294, "a_single_100": 36, "a_single_gt100": 108, "b_total": 284, "b_single_100": 33, "b_single_gt100": 99, "minority_total": 235},
    {"line_key": "11_all", "category_code": "11", "category_name": "军事学", "scope": "各学科专业", "a_total": 260, "a_single_100": 34, "a_single_gt100": 51, "b_total": 250, "b_single_100": 31, "b_single_gt100": 47, "minority_total": 208},
    {"line_key": "12_mba", "category_code": "12", "category_name": "管理学", "scope": "工商管理[1251]", "a_total": 146, "a_single_100": 35, "a_single_gt100": 70, "b_total": 136, "b_single_100": 30, "b_single_gt100": 60, "minority_total": 117},
    {"line_key": "12_mpa", "category_code": "12", "category_name": "管理学", "scope": "公共管理[1252]", "a_total": 168, "a_single_100": 39, "a_single_gt100": 78, "b_total": 158, "b_single_100": 34, "b_single_gt100": 68, "minority_total": 134},
    {"line_key": "12_accounting_library_audit", "category_code": "12", "category_name": "管理学", "scope": "会计[1253]、图书情报[1255]、审计[1257]", "a_total": 199, "a_single_100": 51, "a_single_gt100": 102, "b_total": 189, "b_single_100": 46, "b_single_gt100": 92, "minority_total": 159},
    {"line_key": "12_tourism", "category_code": "12", "category_name": "管理学", "scope": "旅游管理[1254]", "a_total": 151, "a_single_100": 35, "a_single_gt100": 70, "b_total": 141, "b_single_100": 30, "b_single_gt100": 60, "minority_total": 121},
    {"line_key": "12_engineering_management", "category_code": "12", "category_name": "管理学", "scope": "工程管理[1256]", "a_total": 166, "a_single_100": 39, "a_single_gt100": 78, "b_total": 156, "b_single_100": 34, "b_single_gt100": 68, "minority_total": 133},
    {"line_key": "12_other", "category_code": "12", "category_name": "管理学", "scope": "其他学科专业", "a_total": 332, "a_single_100": 41, "a_single_gt100": 62, "b_total": 322, "b_single_100": 38, "b_single_gt100": 57, "minority_total": 266},
    {"line_key": "13_all", "category_code": "13", "category_name": "艺术学", "scope": "各学科专业", "a_total": 354, "a_single_100": 38, "a_single_gt100": 57, "b_total": 344, "b_single_100": 35, "b_single_gt100": 53, "minority_total": 283},
    {"line_key": "14_all", "category_code": "14", "category_name": "交叉学科", "scope": "各学科专业", "a_total": 266, "a_single_100": 35, "a_single_gt100": 53, "b_total": 256, "b_single_100": 32, "b_single_gt100": 48, "minority_total": 213},
]


SELF_MARKING_SCHOOLS = [
    "北京大学", "中国人民大学", "清华大学", "北京航空航天大学", "北京理工大学", "中国农业大学", "北京师范大学",
    "南开大学", "天津大学", "大连理工大学", "东北大学", "吉林大学", "哈尔滨工业大学", "复旦大学", "同济大学",
    "上海交通大学", "南京大学", "东南大学", "浙江大学", "中国科学技术大学", "厦门大学", "山东大学", "武汉大学",
    "华中科技大学", "湖南大学", "中南大学", "中山大学", "华南理工大学", "四川大学", "重庆大学", "电子科技大学",
    "西安交通大学", "西北工业大学", "兰州大学",
]

PROVINCES = [
    {"code": "11", "name": "北京"}, {"code": "12", "name": "天津"}, {"code": "13", "name": "河北"}, {"code": "14", "name": "山西"},
    {"code": "15", "name": "内蒙古"}, {"code": "21", "name": "辽宁"}, {"code": "22", "name": "吉林"}, {"code": "23", "name": "黑龙江"},
    {"code": "31", "name": "上海"}, {"code": "32", "name": "江苏"}, {"code": "33", "name": "浙江"}, {"code": "34", "name": "安徽"},
    {"code": "35", "name": "福建"}, {"code": "36", "name": "江西"}, {"code": "37", "name": "山东"}, {"code": "41", "name": "河南"},
    {"code": "42", "name": "湖北"}, {"code": "43", "name": "湖南"}, {"code": "44", "name": "广东"}, {"code": "45", "name": "广西"},
    {"code": "46", "name": "海南"}, {"code": "50", "name": "重庆"}, {"code": "51", "name": "四川"}, {"code": "52", "name": "贵州"},
    {"code": "53", "name": "云南"}, {"code": "54", "name": "西藏"}, {"code": "61", "name": "陕西"}, {"code": "62", "name": "甘肃"},
    {"code": "63", "name": "青海"}, {"code": "64", "name": "宁夏"}, {"code": "65", "name": "新疆"},
]


CARE_ENGINEERING = {"0801", "0806", "0807", "0815", "0818", "0819", "0824", "0825", "0826", "0827", "0828"}
LINE_BY_KEY = {row["line_key"]: row for row in NATIONAL_LINES}
CATEGORY_NAMES = {
    "01": "哲学", "02": "经济学", "03": "法学", "04": "教育学", "05": "文学", "06": "历史学", "07": "理学",
    "08": "工学", "09": "农学", "10": "医学", "11": "军事学", "12": "管理学", "13": "艺术学", "14": "交叉学科",
}


def now_iso() -> str:
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Referer": CATALOG_URL,
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    })
    return s


def request_json(s: requests.Session, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    url = path if path.startswith("http") else BASE + path
    last_error: Exception | None = None
    for attempt in range(4):
        try:
            r = s.request(method, url, timeout=25, **kwargs)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last_error = exc
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last_error}")


def all_pages(s: requests.Session, path: str, form: dict[str, Any], context: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cur_page = 1
    total_page = 1
    while cur_page <= total_page:
        page_form = dict(form)
        page_form["curPage"] = str(cur_page)
        page_form["start"] = str((cur_page - 1) * 10)
        data = request_json(s, "post", path, data=page_form)
        for retry in range(3):
            if data.get("flag") or data.get("msg") != "访问太频繁":
                break
            time.sleep(3 * (retry + 1))
            data = request_json(s, "post", path, data=page_form)
        if not data.get("flag"):
            LIMIT_WARNINGS.append({"path": path, "context": context, "page": cur_page, "message": data.get("msg")})
            break
        msg = data["msg"]
        rows.extend(msg.get("list") or [])
        total_page = int(msg.get("totalPage") or 1)
        if total_page > 1 and cur_page == 1:
            LIMIT_WARNINGS.append({"path": path, "context": context, "available_first_page_rows": len(msg.get("list") or []), "reported_total_count": msg.get("totalCount"), "reported_total_page": total_page, "message": "研招网未登录状态通常只允许查看第一页；脚本会尝试翻页，失败则保留此警告。"})
        cur_page += 1
    return rows


def discipline_rows(s: requests.Session) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    ml_rows = request_json(s, "get", "/zsml/code/ml.json")["dms"]
    academic_rows: list[dict[str, Any]] = []
    for ml in ml_rows:
        yjxk_rows = request_json(s, "get", f"/zsml/code/yjxk/{ml['dm']}.json").get("dms") or []
        for yjxk in yjxk_rows:
            if not yjxk.get("mc"):
                continue
            academic_rows.append({
                "year": "2026",
                "degree_type": "学术学位",
                "degree_type_code": "xs",
                "category_code": ml["dm"],
                "category_name": ml["mc"],
                "first_discipline_code": yjxk["dm"],
                "first_discipline_name": yjxk["mc"],
                "source_url": CATALOG_URL,
            })
    professional_rows = []
    for zyly in request_json(s, "get", "/zsml/code/zyly.json")["dms"]:
        if not zyly.get("mc"):
            continue
        professional_rows.append({
            "year": "2026",
            "degree_type": "专业学位",
            "degree_type_code": "zyxw",
            "category_code": zyly["dm"][:2],
            "category_name": CATEGORY_NAMES.get(zyly["dm"][:2], ""),
            "first_discipline_code": zyly["dm"],
            "first_discipline_name": zyly["mc"],
            "source_url": CATALOG_URL,
        })
    return ml_rows, academic_rows, professional_rows


def catalog_programs(s: requests.Session, catalog_delay: float, max_queries: int | None) -> list[dict[str, Any]]:
    programs: dict[tuple[str, str], dict[str, Any]] = {}
    ml_rows, academic_rows, professional_rows = discipline_rows(s)
    query_count = 0

    for ml in ml_rows:
        yjxk_rows = [row for row in academic_rows if row["category_code"] == ml["dm"]]
        for yjxk in yjxk_rows:
            if max_queries is not None and query_count >= max_queries:
                LIMIT_WARNINGS.append({"path": "/zsml/rs/zys.do", "context": "specialty catalog", "message": f"按 --max-specialty-queries={max_queries} 截断，后续可增量续跑。"})
                return sorted(programs.values(), key=lambda r: (r.get("mldm") or "", r.get("xwlx") or "", r.get("zydm") or ""))
            form = base_program_form(xwlx="xs", mldm=ml["dm"], yjxkdm=yjxk["first_discipline_code"])
            context = f"学术学位 {ml['dm']}{ml['mc']} / {yjxk['first_discipline_code']}{yjxk['first_discipline_name']}"
            for row in all_pages(s, "/zsml/rs/zys.do", form, context):
                programs[(row["xwlx"], row["zydm"], row["zymc"])] = row
            query_count += 1
            print(f"catalog query {query_count}: {context}; specialties={len(programs)}")
            if catalog_delay:
                time.sleep(catalog_delay)

    for item in professional_rows:
        if max_queries is not None and query_count >= max_queries:
            LIMIT_WARNINGS.append({"path": "/zsml/rs/zys.do", "context": "specialty catalog", "message": f"按 --max-specialty-queries={max_queries} 截断，后续可增量续跑。"})
            return sorted(programs.values(), key=lambda r: (r.get("mldm") or "", r.get("xwlx") or "", r.get("zydm") or ""))
        form = base_program_form(xwlx="zyxw", mldm="", yjxkdm=item["first_discipline_code"])
        context = f"专业学位 {item['first_discipline_code']}{item['first_discipline_name']}"
        for row in all_pages(s, "/zsml/rs/zys.do", form, context):
            programs[(row["xwlx"], row["zydm"], row["zymc"])] = row
        query_count += 1
        print(f"catalog query {query_count}: {context}; specialties={len(programs)}")
        if catalog_delay:
            time.sleep(catalog_delay)

    return sorted(programs.values(), key=lambda r: (r.get("mldm") or "", r.get("xwlx") or "", r.get("zydm") or ""))


def base_program_form(xwlx: str, mldm: str, yjxkdm: str) -> dict[str, str]:
    return {
        "zydm": "", "zymc": "", "xwlx": xwlx, "mldm": mldm, "yjxkdm": yjxkdm,
        "xxfs": "", "tydxs": "", "jsggjh": "", "start": "0", "curPage": "1",
        "pageSize": "20", "totalPage": "0", "totalCount": "0",
    }


def school_program_rows(s: requests.Session, programs: list[dict[str, Any]], delay: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for idx, program in enumerate(programs, 1):
        for province in PROVINCES:
            form = {
                "zydm": program["zydm"],
                "zymc": program["zymc"],
                "dwmc": "",
                "dwdm": "",
                "ssdm": province["code"],
                "xxfs": program.get("mxxfs") or "",
                "dwlxs": "all",
                "tydxs": program.get("mtydxs") or "",
                "jsggjh": program.get("mjsggjh") or "",
                "start": "0",
                "curPage": "1",
                "pageSize": "20",
                "totalPage": "0",
                "totalCount": "0",
                "sign": program["sign"],
            }
            context = f"{program['zydm']}{program['zymc']} / {province['code']}{province['name']}"
            for school in all_pages(s, "/zsml/rs/zydws.do", form, context):
                key = (school.get("dwdm") or "", program["zydm"], program["zymc"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(build_school_program_row(program, school))
        if delay:
            time.sleep(delay)
        if idx % 50 == 0:
            print(f"collected school lists for {idx}/{len(programs)} programs; rows={len(rows)}")
    return rows


def line_key_for(row: dict[str, Any]) -> str:
    mldm = row.get("mldm") or row.get("category_code") or ""
    yjxkdm = row.get("yjxkdm") or row.get("first_discipline_code") or ""
    zydm = row.get("zydm") or row.get("specialty_code") or ""
    if mldm == "04":
        if yjxkdm in {"0403", "0452"}:
            return "04_pe"
        if yjxkdm in {"0451", "0453"}:
            return "04_education"
        return "04_other"
    if mldm == "08":
        return "08_care" if yjxkdm in CARE_ENGINEERING else "08_other"
    if mldm == "12":
        prefix = yjxkdm or zydm[:4]
        if prefix == "1251":
            return "12_mba"
        if prefix == "1252":
            return "12_mpa"
        if prefix in {"1253", "1255", "1257"}:
            return "12_accounting_library_audit"
        if prefix == "1254":
            return "12_tourism"
        if prefix == "1256":
            return "12_engineering_management"
        return "12_other"
    return f"{mldm}_all"


def build_school_program_row(program: dict[str, Any], school: dict[str, Any]) -> dict[str, Any]:
    line = LINE_BY_KEY[line_key_for(program)]
    is_self = school.get("dwmc") in SELF_MARKING_SCHOOLS or school.get("zhx") == "1"
    return {
        "year": "2026",
        "school_code": school.get("dwdm") or "",
        "school_name": school.get("dwmc") or "",
        "province_code": school.get("szssm") or "",
        "province": school.get("szss") or "",
        "is_self_marking": "是" if is_self else "否",
        "has_doctoral_program": "是" if school.get("bs") == "1" else "否",
        "is_double_first_class": "是" if school.get("syl") == "1" else "否",
        "degree_type": program.get("xwlxmc") or "",
        "degree_type_code": program.get("xwlx") or "",
        "category_code": program.get("mldm") or "",
        "category_name": program.get("mlmc") or "",
        "first_discipline_code": program.get("yjxkdm") or "",
        "first_discipline_name": program.get("yjxkmc") or "",
        "specialty_code": program.get("zydm") or "",
        "specialty_name": program.get("zymc") or "",
        "national_line_scope": line["scope"],
        "national_line_a_total": line["a_total"],
        "national_line_a_single_100": line["a_single_100"],
        "national_line_a_single_gt100": line["a_single_gt100"],
        "national_line_b_total": line["b_total"],
        "national_line_b_single_100": line["b_single_100"],
        "national_line_b_single_gt100": line["b_single_gt100"],
        "minority_total_line": line["minority_total"],
        "school_retest_min_score": "",
        "final_admission_min_score": "",
        "score_status": "待从招生单位2026拟录取名单核验",
        "score_source_type": "",
        "score_source_url": "",
        "catalog_source_url": CATALOG_URL,
        "national_line_source_url": NATIONAL_LINE_URL,
        "notes": "研招网专业目录只证明2026招生专业和开设单位；最终录取最低分需逐校以拟录取名单核验。",
    }


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 2026 graduate admission school-specialty score base tables.")
    parser.add_argument("--delay", type=float, default=0.05, help="Delay between per-specialty school-list requests.")
    parser.add_argument("--catalog-delay", type=float, default=0.2, help="Delay between specialty catalog requests.")
    parser.add_argument("--skip-school-programs", action="store_true", help="Only refresh national lines and specialty catalog.")
    parser.add_argument("--code-only", action="store_true", help="Only refresh national lines and discipline category code tables.")
    parser.add_argument("--max-specialty-queries", type=int, default=None, help="Limit specialty catalog queries for incremental runs.")
    parser.add_argument("--max-school-programs", type=int, default=None, help="Limit how many specialties are expanded into school-specialty rows.")
    args = parser.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    s = session()
    updated_at = now_iso()

    national_rows = [{"year": "2026", "source_url": NATIONAL_LINE_URL, **row} for row in NATIONAL_LINES]
    write_csv(DATA / "national_lines.csv", national_rows)
    write_json(DATA / "national_lines.json", {"updated_at": updated_at, "source_url": NATIONAL_LINE_URL, "items": national_rows})

    _, academic_disciplines, professional_disciplines = discipline_rows(s)
    discipline_items = academic_disciplines + professional_disciplines
    write_csv(DATA / "discipline_categories.csv", discipline_items)
    write_json(DATA / "discipline_categories.json", {"updated_at": updated_at, "source_url": CATALOG_URL, "items": discipline_items})

    programs: list[dict[str, Any]] = []
    program_rows: list[dict[str, Any]] = []
    if not args.code_only:
        programs = catalog_programs(s, args.catalog_delay, args.max_specialty_queries)
        for program in programs:
            line = LINE_BY_KEY[line_key_for(program)]
            program_rows.append({
                "year": "2026",
                "degree_type": program.get("xwlxmc") or "",
                "degree_type_code": program.get("xwlx") or "",
                "category_code": program.get("mldm") or "",
                "category_name": program.get("mlmc") or "",
                "first_discipline_code": program.get("yjxkdm") or "",
                "first_discipline_name": program.get("yjxkmc") or "",
                "specialty_code": program.get("zydm") or "",
                "specialty_name": program.get("zymc") or "",
                "national_line_scope": line["scope"],
                "catalog_source_url": CATALOG_URL,
            })
        write_csv(DATA / "specialty_catalog.csv", program_rows)
        write_json(DATA / "specialty_catalog.json", {"updated_at": updated_at, "source_url": CATALOG_URL, "items": program_rows})

    school_rows: list[dict[str, Any]] = []
    if not args.skip_school_programs:
        school_programs = programs[:args.max_school_programs] if args.max_school_programs is not None else programs
        if args.max_school_programs is not None and len(programs) > len(school_programs):
            LIMIT_WARNINGS.append({"path": "/zsml/rs/zydws.do", "context": "school specialty expansion", "message": f"按 --max-school-programs={args.max_school_programs} 截断，后续可增量续跑。"})
        school_rows = school_program_rows(s, school_programs, args.delay)
        write_csv(DATA / "school_specialty_score_base.csv", school_rows)
        write_json(DATA / "school_specialty_score_base.json", {"updated_at": updated_at, "source_url": CATALOG_URL, "items": school_rows})

    sources = {
        "updated_at": updated_at,
        "scope": "2026年全国硕士研究生招生考试；14大学科门类；研招网硕士专业目录开设招生单位；国家线自动匹配。",
        "important_note": "本数据包不把复试线等同于最终录取最低分。final_admission_min_score 留空，需后续逐校采集拟录取名单核验。",
        "official_sources": {
            "national_line": NATIONAL_LINE_URL,
            "chsi_specialty_catalog": CATALOG_URL,
            "chsi_retest_topic": "https://yz.chsi.com.cn/kyzx/zt/kyfs.shtml",
        },
        "generated_files": {
            "national_lines": ["data/graduate_2026/national_lines.csv", "data/graduate_2026/national_lines.json"],
            "specialty_catalog": ["data/graduate_2026/specialty_catalog.csv", "data/graduate_2026/specialty_catalog.json"],
            "discipline_categories": ["data/graduate_2026/discipline_categories.csv", "data/graduate_2026/discipline_categories.json"],
            "school_specialty_score_base": ["data/graduate_2026/school_specialty_score_base.csv", "data/graduate_2026/school_specialty_score_base.json"],
        },
        "counts": {
            "national_line_rows": len(national_rows),
            "discipline_category_rows": len(discipline_items),
            "specialties": len(program_rows),
            "school_specialty_rows": len(school_rows),
        },
        "collection_warnings": LIMIT_WARNINGS,
    }
    write_json(DATA / "sources.json", sources)
    print(json.dumps(sources, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
