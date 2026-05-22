# -*- coding: utf-8 -*-
"""
全国保研院校热度地图 - 云端自动更新脚本 V5
运行环境：GitHub Actions

这版新增：
1）自动发现高校官网推免名额页面；
2）自动提取“推免名额 / 应届本科毕业生人数 / 推免率”；
3）只把有来源 URL 的数据写入前台字段；
4）提取不到或来源不稳定的数据不在前台显示，避免“待核验”污染用户页面；
5）保留热度指数、地图点位、城市聚合等原有逻辑。

重要原则：
- 推免资格名单可以自动更新；
- 推免名额 / 推免率必须带来源 URL；
- 如果某校官网页面是图片扫描 PDF、附件下载被拦截、验证码、JS 渲染，爬虫可能提取不到；
- 提取不到时不展示，不编造。
"""
from __future__ import annotations

import io
import json
import math
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from pypdf import PdfReader
except Exception:  # pypdf 没装也不让主程序崩
    PdfReader = None

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
SEED = DATA / "schools_seed.json"
CURRENT = DATA / "schools.json"
OUT = DATA / "schools.json"
DISCOVERED = DATA / "quota_discovery.json"
TODAY = datetime.utcnow().strftime("%Y-%m-%d")

SOURCE_URLS = {
    "研招网2018推免资格基础名单": "https://yz.chsi.com.cn/kyzx/kp/201809/20180905/1718817601.html",
    "研招网2025新增推免资格高校备案名单": "https://yz.chsi.com.cn/kyzx/jybzc/202509/20250912/2293411123.html",
}

# 首批重点学校：先自动补江苏和头部高校。后面每周可继续扩展。
AUTO_QUOTA_PRIORITY = [
    "南京大学", "东南大学", "南京航空航天大学", "南京理工大学", "河海大学", "南京师范大学", "苏州大学", "江南大学",
    "南京邮电大学", "南京信息工程大学", "南京工业大学", "南京林业大学", "南京医科大学", "南京中医药大学", "江苏大学", "扬州大学",
    "南京财经大学", "苏州科技大学", "常州大学", "中国药科大学", "南京农业大学",
    "北京大学", "清华大学", "中国人民大学", "北京航空航天大学", "北京理工大学", "中国农业大学", "北京师范大学",
    "复旦大学", "上海交通大学", "同济大学", "华东师范大学", "浙江大学", "中国科学技术大学", "武汉大学", "华中科技大学",
    "西安交通大学", "哈尔滨工业大学", "中山大学", "四川大学", "电子科技大学", "东北石油大学", "广西中医药大学",
]

# 已知官方来源入口。爬虫会优先抓这些 URL；没有的再尝试搜索发现。
# 注意：这里只放来源入口，不硬写数字；数字由 parser 自动提取。
OFFICIAL_QUOTA_SOURCE_REGISTRY = {
    "南京中医药大学": ["https://jwc.njucm.edu.cn/2025/0901/c3868a159946/page.htm"],
    "东北石油大学": ["https://jwc.nepu.edu.cn/info/1164/12852.htm"],
    "广西中医药大学": ["https://www.gxtcmu.edu.cn/upload/zjtn/contentmanage/article/file/2025/09/04/%E9%99%84%E4%BB%B6%E4%B8%80%E5%B9%BF%E8%A5%BF%E4%B8%AD%E5%8C%BB%E8%8D%AF%E5%A4%A7%E5%AD%A6%E5%85%B3%E4%BA%8E%E5%81%9A%E5%A5%BD%E6%8E%A8%E8%8D%902026%E5%B1%8A%E4%BC%98%E7%A7%80%E5%BA%94%E5%B1%8A%E6%9C%AC%E7%A7%91%E6%AF%95%E4%B8%9A%E7%94%9F%E5%85%8D%E8%AF%95%E6%94%BB%E8%AF%BB%E7%A1%95%E5%A3%AB%E5%AD%A6%E4%BD%8D%E7%A0%94%E7%A9%B6%E7%94%9F%E5%B7%A5%E4%BD%9C%E7%9A%84%E9%80%9A%E7%9F%A5.pdf"],
    "安徽中医药大学": ["https://jwc.ahtcm.edu.cn/xxgk/tmgz.htm"],
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
    "南京信息工程大学": "大气科学/计算机/电子信息/环境", "南京大学": "计算机/人工智能/大气科学/物理",
    "东南大学": "电子信息/建筑/交通/生物医学", "南京航空航天大学": "航空航天/机械/自动化/电子信息",
    "南京理工大学": "兵器/自动化/计算机/材料", "河海大学": "水利/土木/环境/管理科学",
    "南京师范大学": "教育学/心理学/汉语言/地理", "苏州大学": "材料/医学/法学/设计",
    "北京大学": "计算机/临床医学/法学/金融", "清华大学": "计算机/电子信息/自动化/建筑",
    "复旦大学": "医学/经济学/新闻/法学", "上海交通大学": "电子信息/机械/临床医学/船舶",
    "浙江大学": "计算机/控制/临床医学/农学", "中国科学技术大学": "物理/计算机/人工智能/数学",
    "武汉大学": "测绘/法学/计算机/新闻", "华中科技大学": "电气/计算机/机械/临床医学", "西安交通大学": "电气/能源动力/机械/管理",
}

NEW_2025_FALLBACK = [
    ("北京电子科技学院", "北京市", "北京"), ("北京物资学院", "北京市", "北京"),
    ("南京财经大学", "江苏省", "南京"), ("苏州科技大学", "江苏省", "苏州"),
    ("浙江财经大学", "浙江省", "杭州"), ("西湖大学", "浙江省", "杭州"),
    ("成都信息工程大学", "四川省", "成都"), ("西安邮电大学", "陕西省", "西安"),
]

def http_get(url: str) -> Optional[requests.Response]:
    try:
        resp = requests.get(url, timeout=25, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        resp.raise_for_status()
        return resp
    except Exception:
        return None


def fetch_page_text(url: str) -> Tuple[str, str]:
    """返回：正文纯文本、页面标题/来源名。"""
    resp = http_get(url)
    if not resp:
        return "", ""
    ctype = resp.headers.get("content-type", "").lower()
    lower = url.lower()
    if "pdf" in ctype or lower.endswith(".pdf"):
        if PdfReader is None:
            return "", "PDF附件（当前未安装pypdf）"
        try:
            reader = PdfReader(io.BytesIO(resp.content))
            pages = []
            for page in reader.pages[:10]:
                pages.append(page.extract_text() or "")
            return "\n".join(pages), "PDF附件"
        except Exception:
            return "", "PDF附件"
    enc = resp.apparent_encoding or resp.encoding or "utf-8"
    resp.encoding = enc
    soup = BeautifulSoup(resp.text, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return text, title


def fetch_text(url: str) -> str:
    text, _title = fetch_page_text(url)
    return text


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
    special = {
        "苏州大学": "苏州", "苏州科技大学": "苏州", "江南大学": "无锡", "江苏大学": "镇江", "扬州大学": "扬州", "常州大学": "常州",
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


def heat_score(name: str, city: str, is_new: str, has_verified_quota: bool, has_rate: bool) -> int:
    if name in PRESTIGE_SCORE:
        base = PRESTIGE_SCORE[name]
    else:
        base = 50
        if name in RULE_985:
            base += 25
        elif name in RULE_211:
            base += 17
        elif name in DOUBLE_FIRST:
            base += 13
        else:
            base += 7
        base += CORE_CITY_SCORE.get(city, 4)
        if is_new == "是":
            base += 2
    if has_verified_quota:
        base += 2
    if has_rate:
        base += 2
    return max(55, min(99, int(base)))


def heat_level(score: int) -> str:
    if score >= 95: return "S级"
    if score >= 85: return "A级"
    if score >= 75: return "B级"
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
        for prov in PROVINCES:
            if prov not in text:
                continue
            for m in re.finditer(re.escape(prov), text):
                snippet = text[max(0, m.start() - 250):m.end() + 550]
                names = re.findall(r"([\u4e00-\u9fa5A-Za-z0-9()（）·]{2,24}(?:大学|学院|研究院|研究所))", snippet)
                for n in names:
                    n = norm_name(n)
                    if looks_like_school(n):
                        all_found.append((n, prov))
    for name, prov, _city in NEW_2025_FALLBACK:
        all_found.append((name, prov))
    return all_found


def is_official_school_url(url: str, school_name: str = "") -> bool:
    host = urlparse(url).netloc.lower()
    if not host:
        return False
    bad = ["baidu", "zhihu", "sohu", "163.com", "toutiao", "weixin", "kaoyan", "eol.cn", "gaokao", "cnki"]
    if any(b in host for b in bad):
        return False
    return host.endswith(".edu.cn") or host.endswith(".edu") or "chsi.com.cn" in host or "moe.gov.cn" in host or host.endswith(".ac.cn")


def discover_urls_by_search(school_name: str, max_urls: int = 3) -> List[str]:
    """用 DuckDuckGo HTML 做轻量发现；搜不到不影响主流程。"""
    queries = [
        f'{school_name} 2026届 推免 名额 分配',
        f'{school_name} 2026届 推荐免试 名额 教务处',
        f'{school_name} 2026届 推免生计划 名额',
    ]
    urls: List[str] = []
    for q in queries:
        search_url = "https://duckduckgo.com/html/?q=" + quote_plus(q)
        resp = http_get(search_url)
        if not resp:
            continue
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.select("a.result__a, a[href]"):
            href = a.get("href") or ""
            if "uddg=" in href:
                from urllib.parse import parse_qs, urlparse, unquote
                qs = parse_qs(urlparse(href).query)
                href = unquote(qs.get("uddg", [href])[0])
            if href.startswith("http") and is_official_school_url(href, school_name):
                if href not in urls:
                    urls.append(href)
            if len(urls) >= max_urls:
                break
        if len(urls) >= max_urls:
            break
        time.sleep(0.8 + random.random() * 0.5)
    return urls


def clean_num(n: str) -> Optional[int]:
    try:
        v = int(re.sub(r"[^0-9]", "", n))
        if 1 <= v <= 10000:
            return v
    except Exception:
        return None
    return None


def extract_quota_from_text(text: str) -> Tuple[Optional[int], str]:
    if not text:
        return None, ""
    compact = re.sub(r"\s+", "", text)
    patterns = [
        r"(?:教育部|上级|学校|我校).{0,18}(?:下达|获得|获批).{0,18}(?:推免生)?(?:计划|指标|名额).{0,8}(?:为|共|合计)?(\d{2,4})人",
        r"(?:推免生|推荐免试|推荐计划|推免资格)(?:计划|指标|名额).{0,8}(?:为|共|合计)?(\d{2,4})人",
        r"(?:总名额|推荐名额总数|推荐计划数总数|推免名额总数).{0,8}(?:为|共|合计)?(\d{2,4})人",
        r"(\d{2,4})个?(?:推免生)?(?:名额|指标)",
    ]
    candidates: List[Tuple[int, str]] = []
    for p in patterns:
        for m in re.finditer(p, compact):
            v = clean_num(m.group(1))
            if v:
                snippet = compact[max(0, m.start()-40):m.end()+40]
                candidates.append((v, snippet))
    # 取最可信：一般学校总名额不会特别小，排除明显学院名额；优先出现“总/教育部/我校/下达”的片段。
    if not candidates:
        return None, ""
    candidates = sorted(candidates, key=lambda x: (
        0 if re.search(r"总|教育部|我校|下达|获得|获批", x[1]) else 1,
        -x[0]
    ))
    return candidates[0]


def extract_grad_count_from_text(text: str) -> Tuple[Optional[int], str]:
    if not text:
        return None, ""
    compact = re.sub(r"\s+", "", text)
    patterns = [
        r"(?:应届本科毕业生|本科毕业生|普通本科毕业生).{0,12}(?:人数|总数|共|为)(\d{3,5})人",
        r"(\d{3,5})名?(?:应届)?本科毕业生",
    ]
    candidates: List[Tuple[int, str]] = []
    for p in patterns:
        for m in re.finditer(p, compact):
            v = clean_num(m.group(1))
            if v and v >= 300:
                snippet = compact[max(0, m.start()-40):m.end()+40]
                candidates.append((v, snippet))
    if not candidates:
        return None, ""
    # 毕业生人数一般大于推免名额，取较大但不过分离谱的数。
    candidates = sorted(candidates, key=lambda x: -x[0])
    return candidates[0]


def parse_quota_table_from_html(url: str) -> Tuple[Optional[int], str]:
    resp = http_get(url)
    if not resp or "pdf" in resp.headers.get("content-type", "").lower() or url.lower().endswith(".pdf"):
        return None, ""
    resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    best_total = None
    best_snip = ""
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        if len(rows) < 2:
            continue
        header_text = "".join(rows[0])
        if "名额" not in header_text and "推荐计划" not in header_text and "计划数" not in header_text:
            continue
        # 找名额列
        quota_cols = [i for i, h in enumerate(rows[0]) if any(k in h for k in ["名额", "计划", "指标"])]
        if not quota_cols:
            quota_cols = [len(rows[0]) - 1]
        nums = []
        for row in rows[1:]:
            for ci in quota_cols:
                if ci < len(row):
                    m = re.search(r"\b(\d{1,4})\b", row[ci].replace(",", ""))
                    if m:
                        v = clean_num(m.group(1))
                        if v and 1 <= v <= 1000:
                            nums.append(v)
        if len(nums) >= 2:
            total = sum(nums)
            if total >= 20 and (best_total is None or total > best_total):
                best_total = total
                best_snip = f"表格名额列求和：{total}人"
    return best_total, best_snip


def extract_verified_recommendation(school_name: str, old: dict | None = None) -> dict:
    """自动抓取推免名额和推免率。只返回带来源的字段。"""
    old = old or {}
    # 已有来源且已有名额/率，先保留，避免反复覆盖。
    existing_quota = str(old.get("recommendation_quota") or "").strip()
    existing_src = str(old.get("recommendation_source_url") or "").strip()
    existing_rate = str(old.get("recommendation_rate") or "").strip()
    if existing_src and (existing_quota or existing_rate):
        return {
            "recommendation_year": old.get("recommendation_year", ""),
            "recommendation_quota": existing_quota,
            "graduate_count": old.get("graduate_count", ""),
            "recommendation_rate": existing_rate,
            "recommendation_source_name": old.get("recommendation_source_name", ""),
            "recommendation_source_url": existing_src,
            "recommendation_verify_status": old.get("recommendation_verify_status", "官网来源已记录"),
        }

    urls = list(OFFICIAL_QUOTA_SOURCE_REGISTRY.get(school_name, []))
    # 优先学校列表或原来有较高热度的学校才自动搜索，避免 400 多所一起搜被搜索引擎限流。
    if school_name in AUTO_QUOTA_PRIORITY:
        urls += discover_urls_by_search(school_name, max_urls=2)
    # 去重
    seen = set()
    urls = [u for u in urls if not (u in seen or seen.add(u))]
    if not urls:
        return {}

    best = {}
    discovery_log = []
    for url in urls[:4]:
        if not is_official_school_url(url, school_name):
            continue
        text, title = fetch_page_text(url)
        if not text:
            continue
        lower_text = text[:5000]
        if not any(k in lower_text for k in ["推免", "推荐免试", "免试攻读", "推荐优秀应届本科"]):
            continue
        quota, q_snip = extract_quota_from_text(text)
        table_quota, table_snip = parse_quota_table_from_html(url)
        # 若页面有明确总数，用明确总数；否则用表格求和。
        if not quota and table_quota:
            quota, q_snip = table_quota, table_snip
        grad, g_snip = extract_grad_count_from_text(text)
        if quota or grad:
            rate = ""
            if quota and grad and grad > quota:
                rate = f"{quota / grad * 100:.2f}%"
            best = {
                "recommendation_year": "2026届" if "2026" in text[:3000] or "2026届" in text else "",
                "recommendation_quota": f"{quota}人" if quota else "",
                "graduate_count": str(grad) if grad else "",
                "recommendation_rate": rate,
                "recommendation_source_name": title or "学校官网推免公告",
                "recommendation_source_url": url,
                "recommendation_verify_status": "自动抓取到官网来源；建议人工抽查口径" if quota else "自动抓取到毕业生人数来源；建议人工抽查口径",
                "_debug_snippet": q_snip or g_snip,
            }
            break
        discovery_log.append({"url": url, "title": title, "found": False})
        time.sleep(0.5 + random.random() * 0.3)
    if best:
        discovery_log.append({"url": best.get("recommendation_source_url"), "title": best.get("recommendation_source_name"), "found": True, "snippet": best.get("_debug_snippet", "")})
    return best


def make_record(name: str, province: str, old: dict | None = None, is_new: str = "否", source_name: str = "自动抓取/基础库合并", source_url: str = "") -> dict:
    old = old or {}
    city = infer_city(name, province or old.get("province", ""), old.get("city", ""))
    school_type = old.get("school_type") or infer_type(name)
    is_985, is_211, is_double = level_flags(name)
    if old.get("is_985") == "是": is_985 = "是"
    if old.get("is_211") == "是": is_211 = "是"
    if old.get("is_double_first_class") == "是": is_double = "是"
    if old.get("is_2025_new") == "是": is_new = "是"
    majors = old.get("hot_majors") or MAJOR_BY_SCHOOL.get(name) or MAJOR_BY_TYPE.get(school_type, "计算机/电子信息/管理/法学")

    rec = extract_verified_recommendation(name, old)
    rec_quota = rec.get("recommendation_quota", "")
    rec_rate = rec.get("recommendation_rate", "")
    rec_year = rec.get("recommendation_year", "")
    rec_source_name = rec.get("recommendation_source_name", "")
    rec_source_url = rec.get("recommendation_source_url", "")
    grad_count = rec.get("graduate_count", old.get("graduate_count", ""))
    verify_status = old.get("verify_status") or "基础名单已核验；推免率/名额持续自动抓取"
    if rec_quota or rec_rate:
        verify_status = rec.get("recommendation_verify_status", "自动抓取到官网来源；建议人工抽查口径")
    score = heat_score(name, city, is_new, bool(rec_quota), bool(rec_rate))
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
        # 注意：没抓到真实来源就留空；前台不会展示“待核验”。
        "recommendation_quota": rec_quota,
        "graduate_count": grad_count,
        "recommendation_rate": rec_rate,
        "recommendation_year": rec_year,
        "recommendation_source_name": rec_source_name,
        "recommendation_source_url": rec_source_url,
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

    for name, prov in parse_base_official():
        old = merged.get(name, {})
        merged[name] = dict(old, school_name=name, province=prov, has_recommendation_qualification="是", source_name="研招网2018推免资格基础名单", source_url=SOURCE_URLS["研招网2018推免资格基础名单"])

    for name, prov in parse_new_2025():
        old = merged.get(name, {})
        merged[name] = dict(old, school_name=name, province=prov or old.get("province", ""), has_recommendation_qualification="是", is_2025_new="是", source_name="研招网2025新增推免资格高校备案名单/兜底重点名单", source_url=SOURCE_URLS["研招网2025新增推免资格高校备案名单"])

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
        "note": "推免名额、推免率由爬虫自动抓取官网来源；未抓到真实来源的不在前台展示。",
        "sources": SOURCE_URLS,
        "auto_quota_priority_count": len(AUTO_QUOTA_PRIORITY),
        "schools": final_rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"updated {len(final_rows)} schools -> {OUT}")
    with_quota = [r for r in final_rows if r.get("recommendation_quota") or r.get("recommendation_rate")]
    print(f"verified/auto-extracted quota/rate records: {len(with_quota)}")
    print("top 10:", ", ".join([f"{r['school_name']}({r['heat_score']})" for r in final_rows[:10]]))


if __name__ == "__main__":
    main()
