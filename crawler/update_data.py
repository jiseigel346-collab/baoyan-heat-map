# -*- coding: utf-8 -*-
"""
全国保研院校热度地图 - 云端自动更新脚本 V2
运行环境：GitHub Actions
作用：
1）保留现有 data/schools_seed.json / data/schools.json 里的基础数据；
2）尝试抓取研招网推免资格基础名单、新增推免资格高校通知；
3）用更符合家长认知的热度算法重新排序；
4）推免率、名额、学院分配等高风险字段只标记“待核验”，不自动硬写；
5）输出 data/schools.json，供前端地图读取。
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SEED = DATA / "schools_seed.json"
CURRENT = DATA / "schools.json"
OUT = DATA / "schools.json"

TODAY = datetime.utcnow().strftime("%Y-%m-%d")

SOURCE_URLS = {
    "研招网2018推免资格基础名单": "https://yz.chsi.com.cn/kyzx/kp/201809/20180905/1718817601.html",
    "研招网2025新增推免资格高校备案名单": "https://yz.chsi.com.cn/kyzx/jybzc/202509/20250912/2293411123.html",
}

PROVINCES = [
    "北京市", "天津市", "上海市", "重庆市", "河北省", "山西省", "辽宁省", "吉林省", "黑龙江省",
    "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "河南省", "湖北省", "湖南省",
    "广东省", "海南省", "四川省", "贵州省", "云南省", "陕西省", "甘肃省", "青海省", "台湾省",
    "内蒙古自治区", "广西壮族自治区", "西藏自治区", "宁夏回族自治区", "新疆维吾尔自治区", "新疆生产建设兵团"
]

PROVINCE_TO_CITY = {
    "北京市": "北京", "上海市": "上海", "天津市": "天津", "重庆市": "重庆",
    "江苏省": "南京", "浙江省": "杭州", "湖北省": "武汉", "陕西省": "西安", "广东省": "广州",
    "四川省": "成都", "山东省": "济南", "安徽省": "合肥", "湖南省": "长沙", "福建省": "福州",
    "河南省": "郑州", "河北省": "石家庄", "辽宁省": "沈阳", "黑龙江省": "哈尔滨", "吉林省": "长春",
    "山西省": "太原", "江西省": "南昌", "广西壮族自治区": "南宁", "海南省": "海口", "云南省": "昆明",
    "贵州省": "贵阳", "甘肃省": "兰州", "青海省": "西宁", "宁夏回族自治区": "银川",
    "新疆维吾尔自治区": "乌鲁木齐", "西藏自治区": "拉萨", "内蒙古自治区": "呼和浩特"
}

CORE_CITY_SCORE = {
    "北京": 12, "上海": 12, "南京": 11, "武汉": 11, "西安": 10, "杭州": 10, "广州": 10,
    "成都": 9, "天津": 8, "重庆": 8, "长沙": 8, "合肥": 8, "哈尔滨": 8, "济南": 7,
    "苏州": 7, "大连": 7, "青岛": 7, "厦门": 7, "福州": 6, "郑州": 6, "沈阳": 6,
}

RULE_985 = set("""
北京大学 中国人民大学 清华大学 北京航空航天大学 北京理工大学 中国农业大学 北京师范大学 中央民族大学
南开大学 天津大学 大连理工大学 东北大学 吉林大学 哈尔滨工业大学 复旦大学 同济大学 上海交通大学 华东师范大学
南京大学 东南大学 浙江大学 中国科学技术大学 厦门大学 山东大学 中国海洋大学 武汉大学 华中科技大学 湖南大学
中南大学 中山大学 华南理工大学 四川大学 电子科技大学 重庆大学 西安交通大学 西北工业大学 西北农林科技大学 兰州大学 国防科技大学
""".split())

RULE_211 = set("""
北京大学 中国人民大学 清华大学 北京交通大学 北京工业大学 北京航空航天大学 北京理工大学 北京科技大学 北京化工大学 北京邮电大学 中国农业大学 北京林业大学 北京中医药大学 北京师范大学 北京外国语大学 中国传媒大学 中央财经大学 对外经济贸易大学 北京体育大学 中央音乐学院 中央民族大学 中国政法大学 华北电力大学
南开大学 天津大学 天津医科大学 河北工业大学 太原理工大学 内蒙古大学 辽宁大学 大连理工大学 东北大学 大连海事大学 吉林大学 延边大学 东北师范大学 哈尔滨工业大学 哈尔滨工程大学 东北农业大学 东北林业大学
复旦大学 同济大学 上海交通大学 华东理工大学 东华大学 华东师范大学 上海外国语大学 上海财经大学 上海大学
南京大学 苏州大学 东南大学 南京航空航天大学 南京理工大学 中国矿业大学 河海大学 江南大学 南京农业大学 中国药科大学 南京师范大学
浙江大学 安徽大学 中国科学技术大学 合肥工业大学 厦门大学 福州大学 南昌大学 山东大学 中国海洋大学 中国石油大学(华东) 郑州大学
武汉大学 华中科技大学 中国地质大学(武汉) 武汉理工大学 华中农业大学 华中师范大学 中南财经政法大学
湖南大学 中南大学 湖南师范大学 中山大学 暨南大学 华南理工大学 华南师范大学 广西大学 海南大学
四川大学 西南交通大学 电子科技大学 四川农业大学 西南财经大学 重庆大学 西南大学 贵州大学 云南大学 西藏大学
西北大学 西安交通大学 西北工业大学 西安电子科技大学 长安大学 西北农林科技大学 陕西师范大学 兰州大学 青海大学 宁夏大学 新疆大学 石河子大学
""".split())

DOUBLE_FIRST_EXTRA = set("""
中国科学院大学 中国社会科学院大学 外交学院 中国人民公安大学 中国音乐学院 中央美术学院 中央戏剧学院 上海海洋大学 上海中医药大学 上海体育大学 上海音乐学院 南京信息工程大学 南京林业大学 南京医科大学 南京中医药大学 宁波大学 中国美术学院 河南大学 成都理工大学 成都中医药大学 西南石油大学 广州中医药大学 天津中医药大学 山西大学 湘潭大学 南方科技大学 上海科技大学 西湖大学
""".split())
DOUBLE_FIRST = RULE_211 | DOUBLE_FIRST_EXTRA

# 用来把右侧榜单排序拉回“家长认知”的顶尖学校优先表
PRESTIGE_SCORE = {
    "清华大学": 99, "北京大学": 99, "复旦大学": 98, "上海交通大学": 98, "浙江大学": 98,
    "中国科学技术大学": 97, "南京大学": 97, "中国人民大学": 96, "北京航空航天大学": 95,
    "哈尔滨工业大学": 95, "武汉大学": 95, "华中科技大学": 95, "西安交通大学": 95,
    "同济大学": 94, "东南大学": 94, "南开大学": 94, "天津大学": 94, "中山大学": 94,
    "北京理工大学": 93, "厦门大学": 93, "四川大学": 93, "电子科技大学": 93, "华南理工大学": 93,
    "西北工业大学": 93, "山东大学": 92, "中南大学": 92, "吉林大学": 92, "大连理工大学": 91,
    "重庆大学": 91, "湖南大学": 91, "中国农业大学": 91, "北京师范大学": 91,
}

SCHOOL_TYPE_HINTS = [
    ("师范", "师范"), ("医科", "医药"), ("中医药", "医药"), ("财经", "财经"), ("政法", "政法"),
    ("外国语", "语言"), ("体育", "体育"), ("美术", "艺术"), ("音乐", "艺术"), ("戏剧", "艺术"),
    ("航空", "理工"), ("航天", "理工"), ("理工", "理工"), ("工业", "理工"), ("科技", "理工"),
    ("电子", "理工"), ("邮电", "理工"), ("交通", "理工"), ("矿业", "理工"), ("石油", "理工"),
    ("农业", "农林"), ("林业", "农林"), ("海洋", "综合"),
]

MAJOR_BY_TYPE = {
    "综合": "计算机/电子信息/金融/法学",
    "理工": "计算机/电子信息/机械/材料",
    "师范": "教育学/心理学/汉语言/数学",
    "医药": "临床医学/药学/公共卫生/生物",
    "财经": "金融/会计/经济学/统计",
    "政法": "法学/公共管理/社会学/政治学",
    "语言": "外国语言文学/翻译/国际传播/教育",
    "艺术": "设计/美术/音乐/戏剧影视",
    "农林": "农学/食品/生物/生态",
    "体育": "体育教育/运动训练/体育人文",
}

MAJOR_BY_SCHOOL = {
    "南京信息工程大学": "大气科学/计算机/电子信息/环境",
    "南京大学": "计算机/人工智能/大气科学/物理",
    "东南大学": "电子信息/建筑/交通/生物医学",
    "南京航空航天大学": "航空航天/机械/自动化/电子信息",
    "南京理工大学": "兵器/自动化/计算机/材料",
    "河海大学": "水利/土木/环境/管理科学",
    "南京师范大学": "教育学/心理学/汉语言/地理",
    "苏州大学": "材料/医学/法学/设计",
    "北京大学": "计算机/临床医学/法学/金融",
    "清华大学": "计算机/电子信息/自动化/建筑",
    "复旦大学": "医学/经济学/新闻/法学",
    "上海交通大学": "电子信息/机械/临床医学/船舶",
    "浙江大学": "计算机/控制/临床医学/农学",
    "中国科学技术大学": "物理/计算机/人工智能/数学",
    "武汉大学": "测绘/法学/计算机/新闻",
    "华中科技大学": "电气/计算机/机械/临床医学",
    "西安交通大学": "电气/能源动力/机械/管理",
}

# 2025教育部办公厅备案新增推免资格高校中，用户重点会关心的部分，作为抓取失败时的兜底增量。
NEW_2025_FALLBACK = [
    ("北京电子科技学院", "北京市", "北京"), ("北京物资学院", "北京市", "北京"),
    ("南京财经大学", "江苏省", "南京"), ("苏州科技大学", "江苏省", "苏州"),
    ("浙江财经大学", "浙江省", "杭州"), ("西湖大学", "浙江省", "杭州"),
    ("成都信息工程大学", "四川省", "成都"), ("西安邮电大学", "陕西省", "西安"),
]


def fetch_text(url: str) -> str:
    try:
        resp = requests.get(url, timeout=25, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return BeautifulSoup(resp.text, "html.parser").get_text("\n")
    except Exception:
        return ""


def norm_name(name: str) -> str:
    name = re.sub(r"\s+", "", str(name or ""))
    name = name.replace("（", "(").replace("）", ")")
    return name.strip("，,。；;、：:")


def looks_like_school(name: str) -> bool:
    if not name or len(name) < 3 or len(name) > 24:
        return False
    bad_words = ["名单", "资格", "高校", "通知", "教育部", "办公厅", "推荐", "优秀", "应届", "本科", "毕业生", "免试", "攻读", "研究生", "单位", "名称", "所在", "省市", "附件"]
    if any(w in name for w in bad_words):
        return False
    return bool(re.search(r"(大学|学院|研究院|研究所|科学技术大学|科技大学|工业大学|师范大学|医科大学|中医药大学|财经大学|政法大学|外国语大学|农业大学|林业大学|海洋大学|民族大学|体育大学|音乐学院|美术学院|戏剧学院)$", name))


def infer_type(name: str) -> str:
    for key, val in SCHOOL_TYPE_HINTS:
        if key in name:
            return val
    return "综合"


def infer_city(name: str, province: str, old_city: str = "") -> str:
    if old_city:
        return old_city
    # 一些非省会城市高校，避免都落到省会。
    special = {
        "苏州大学": "苏州", "苏州科技大学": "苏州", "江南大学": "无锡", "江苏大学": "镇江", "扬州大学": "扬州",
        "宁波大学": "宁波", "厦门大学": "厦门", "青岛大学": "青岛", "中国海洋大学": "青岛", "大连理工大学": "大连",
        "哈尔滨工业大学": "哈尔滨", "西湖大学": "杭州", "深圳大学": "深圳", "南方科技大学": "深圳",
    }
    return special.get(name) or PROVINCE_TO_CITY.get(province, province.replace("省", "").replace("市", ""))


def level_flags(name: str) -> Tuple[str, str, str]:
    return ("是" if name in RULE_985 else "否", "是" if name in RULE_211 else "否", "是" if name in DOUBLE_FIRST else "否")


def level_name(name: str) -> str:
    if name in RULE_985:
        return "985"
    if name in RULE_211:
        return "211"
    if name in DOUBLE_FIRST:
        return "双一流"
    return "普通"


def heat_score(name: str, city: str, is_new: str, verify_status: str) -> int:
    if name in PRESTIGE_SCORE:
        base = PRESTIGE_SCORE[name]
        return max(60, min(99, base))
    score = 50
    if name in RULE_985:
        score += 25
    elif name in RULE_211:
        score += 17
    elif name in DOUBLE_FIRST:
        score += 13
    else:
        score += 7
    score += CORE_CITY_SCORE.get(city, 4)
    if is_new == "是":
        score += 2
    if "已核验" in verify_status:
        score += 2
    return max(55, min(94, score))


def heat_level(score: int) -> str:
    if score >= 95:
        return "S级"
    if score >= 85:
        return "A级"
    if score >= 75:
        return "B级"
    return "C级"


def load_existing() -> Dict[str, dict]:
    result: Dict[str, dict] = {}
    for path in [SEED, CURRENT]:
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            rows = raw.get("schools", raw if isinstance(raw, list) else [])
            for item in rows:
                name = norm_name(item.get("school_name") or item.get("name"))
                if name:
                    item["school_name"] = name
                    result[name] = item
        except Exception:
            continue
    return result


def parse_base_official() -> List[Tuple[str, str]]:
    text = fetch_text(SOURCE_URLS["研招网2018推免资格基础名单"])
    if not text:
        return []
    tokens = [norm_name(t) for t in re.split(r"[\s\n\t]+", text) if norm_name(t)]
    found: List[Tuple[str, str]] = []
    for i in range(len(tokens) - 1):
        name, prov = tokens[i], tokens[i + 1]
        if prov in PROVINCES and looks_like_school(name):
            found.append((name, prov))
    return found


def parse_new_2025() -> List[Tuple[str, str]]:
    all_found: List[Tuple[str, str]] = []
    for source_name, url in SOURCE_URLS.items():
        if "新增" not in source_name:
            continue
        text = fetch_text(url)
        if not text:
            continue
        # 对每个省份附近的学校名称进行保守抽取。
        for prov in PROVINCES:
            if prov not in text:
                continue
            # 找省份前后较短范围，抽高校名。
            for m in re.finditer(re.escape(prov), text):
                snippet = text[max(0, m.start() - 250):m.end() + 550]
                names = re.findall(r"([\u4e00-\u9fa5A-Za-z0-9()（）·]{2,24}(?:大学|学院|研究院|研究所))", snippet)
                for n in names:
                    n = norm_name(n)
                    if looks_like_school(n):
                        all_found.append((n, prov))
    # 兜底补充用户重点可能用到的新增院校，避免页面抓取结构变动导致漏掉。
    for name, prov, _city in NEW_2025_FALLBACK:
        all_found.append((name, prov))
    return all_found


def make_record(name: str, province: str, old: dict | None = None, is_new: str = "否", source_name: str = "自动抓取/基础库合并", source_url: str = "") -> dict:
    old = old or {}
    old_city = old.get("city", "")
    city = infer_city(name, province or old.get("province", ""), old_city)
    school_type = old.get("school_type") or infer_type(name)
    is_985, is_211, is_double = level_flags(name)
    if old.get("is_985") == "是": is_985 = "是"
    if old.get("is_211") == "是": is_211 = "是"
    if old.get("is_double_first_class") == "是": is_double = "是"
    if old.get("is_2025_new") == "是": is_new = "是"
    verify_status = old.get("verify_status") or "基础名单已核验；推免率/名额/学院分配待采集"
    score = heat_score(name, city, is_new, verify_status)
    majors = old.get("hot_majors") or MAJOR_BY_SCHOOL.get(name) or MAJOR_BY_TYPE.get(school_type, "计算机/电子信息/管理/法学")
    return {
        "school_name": name,
        "province": province or old.get("province", ""),
        "city": city,
        "has_recommendation_qualification": "是",
        "is_2025_new": is_new,
        "is_985": is_985,
        "is_211": is_211,
        "is_double_first_class": is_double,
        "school_type": school_type,
        "recommendation_quota": old.get("recommendation_quota", ""),
        "graduate_count": old.get("graduate_count", ""),
        "recommendation_rate": old.get("recommendation_rate", ""),
        "city_score": str(80 + CORE_CITY_SCORE.get(city, 4)),
        "school_level_score": str(100 if is_985 == "是" else 88 if is_211 == "是" else 78 if is_double == "是" else 65),
        "heat_score": str(score),
        "heat_level": heat_level(score),
        "source_name": source_name or old.get("source_name", ""),
        "source_url": source_url or old.get("source_url", ""),
        "publish_date": old.get("publish_date", ""),
        "last_checked": TODAY,
        "verify_status": verify_status,
        "hot_majors": majors,
        "level_display": level_name(name),
    }


def main() -> None:
    existing = load_existing()
    merged: Dict[str, dict] = dict(existing)

    # 1. 官方基础名单：能抓到就合并，抓不到就保留既有数据。
    for name, prov in parse_base_official():
        old = merged.get(name, {})
        merged[name] = make_record(
            name, prov, old, is_new=old.get("is_2025_new", "否"),
            source_name="研招网2018推免资格基础名单",
            source_url=SOURCE_URLS["研招网2018推免资格基础名单"],
        )

    # 2. 新增推免资格高校：标记为新增。
    for name, prov in parse_new_2025():
        old = merged.get(name, {})
        merged[name] = make_record(
            name, prov or old.get("province", ""), old, is_new="是",
            source_name="研招网2025新增推免资格高校备案名单/兜底重点名单",
            source_url=SOURCE_URLS["研招网2025新增推免资格高校备案名单"],
        )

    # 3. 对所有既有院校重新计算热度，解决右侧榜单排序不符合家长认知的问题。
    final_rows = []
    for name, item in merged.items():
        final_rows.append(make_record(
            name=name,
            province=item.get("province", ""),
            old=item,
            is_new=item.get("is_2025_new", "否"),
            source_name=item.get("source_name", "自动抓取/基础库合并"),
            source_url=item.get("source_url", ""),
        ))

    final_rows.sort(key=lambda x: (-int(x.get("heat_score") or 0), x.get("province", ""), x.get("school_name", "")))
    payload = {
        "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "note": "推免率、推免名额、学院分配等字段需要官网逐条核验，当前不做无审核自动发布。",
        "sources": SOURCE_URLS,
        "schools": final_rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {len(final_rows)} schools -> {OUT}")
    print("top 10:", ", ".join([f"{r['school_name']}({r['heat_score']})" for r in final_rows[:10]]))


if __name__ == "__main__":
    main()
