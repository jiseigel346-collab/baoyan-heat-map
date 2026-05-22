# -*- coding: utf-8 -*-
"""
云端自动抓取脚本（GitHub Actions 运行）：
1. 抓研招网推免资格基础名单；
2. 尝试抓最新“新增推免资格高校”通知；
3. 输出 data/schools.json，供前端网页读取；
4. 推免率/名额不直接自动发布，只写“待核验”。
"""
from __future__ import annotations
import csv, json, re
from pathlib import Path
from datetime import datetime
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
SEED = DATA / 'schools_seed.json'
OUT = DATA / 'schools.json'

BASE_URL = 'https://yz.chsi.com.cn/kyzx/kp/201809/20180905/1718817601.html'
NEW_URLS = [
    'https://yz.chsi.com.cn/kyzx/jybzc/202509/20250912/2293411123.html',
]

PROVINCES = ['北京市','天津市','上海市','重庆市','河北省','山西省','辽宁省','吉林省','黑龙江省','江苏省','浙江省','安徽省','福建省','江西省','山东省','河南省','湖北省','湖南省','广东省','海南省','四川省','贵州省','云南省','陕西省','甘肃省','青海省','台湾省','内蒙古自治区','广西壮族自治区','西藏自治区','宁夏回族自治区','新疆维吾尔自治区','新疆生产建设兵团']
CAPITAL = {'北京市':'北京','上海市':'上海','天津市':'天津','重庆市':'重庆','江苏省':'南京','浙江省':'杭州','湖北省':'武汉','陕西省':'西安','广东省':'广州','四川省':'成都','山东省':'济南','安徽省':'合肥','湖南省':'长沙','福建省':'福州','河南省':'郑州','河北省':'石家庄','辽宁省':'沈阳','黑龙江省':'哈尔滨','吉林省':'长春','山西省':'太原','江西省':'南昌','广西壮族自治区':'南宁','海南省':'海口','云南省':'昆明','贵州省':'贵阳','甘肃省':'兰州','青海省':'西宁','宁夏回族自治区':'银川','新疆维吾尔自治区':'乌鲁木齐','西藏自治区':'拉萨'}

RULE_985 = set('北京大学,中国人民大学,清华大学,北京航空航天大学,北京理工大学,中国农业大学,北京师范大学,中央民族大学,南开大学,天津大学,大连理工大学,东北大学,吉林大学,哈尔滨工业大学,复旦大学,同济大学,上海交通大学,华东师范大学,南京大学,东南大学,浙江大学,中国科学技术大学,厦门大学,山东大学,中国海洋大学,武汉大学,华中科技大学,湖南大学,中南大学,中山大学,华南理工大学,四川大学,电子科技大学,重庆大学,西安交通大学,西北工业大学,西北农林科技大学,兰州大学,国防科技大学'.split(','))
RULE_211 = set('北京大学,中国人民大学,清华大学,北京交通大学,北京工业大学,北京航空航天大学,北京理工大学,北京科技大学,北京化工大学,北京邮电大学,中国农业大学,北京林业大学,北京中医药大学,北京师范大学,北京外国语大学,中国传媒大学,中央财经大学,对外经济贸易大学,北京体育大学,中央音乐学院,中央民族大学,中国政法大学,华北电力大学,南开大学,天津大学,天津医科大学,河北工业大学,太原理工大学,内蒙古大学,辽宁大学,大连理工大学,东北大学,大连海事大学,吉林大学,延边大学,东北师范大学,哈尔滨工业大学,哈尔滨工程大学,东北农业大学,东北林业大学,复旦大学,同济大学,上海交通大学,华东理工大学,东华大学,华东师范大学,上海外国语大学,上海财经大学,上海大学,南京大学,苏州大学,东南大学,南京航空航天大学,南京理工大学,中国矿业大学,河海大学,江南大学,南京农业大学,中国药科大学,南京师范大学,浙江大学,安徽大学,中国科学技术大学,合肥工业大学,厦门大学,福州大学,南昌大学,山东大学,中国海洋大学,中国石油大学(华东),郑州大学,武汉大学,华中科技大学,中国地质大学(武汉),武汉理工大学,华中农业大学,华中师范大学,中南财经政法大学,湖南大学,中南大学,湖南师范大学,中山大学,暨南大学,华南理工大学,华南师范大学,广西大学,海南大学,四川大学,西南交通大学,电子科技大学,四川农业大学,西南财经大学,重庆大学,西南大学,贵州大学,云南大学,西藏大学,西北大学,西安交通大学,西北工业大学,西安电子科技大学,长安大学,西北农林科技大学,陕西师范大学,兰州大学,青海大学,宁夏大学,新疆大学,石河子大学'.split(','))
DOUBLE = RULE_211 | set(['南京信息工程大学','上海科技大学','中国科学院大学','南方科技大学','西湖大学','成都中医药大学','广州中医药大学','天津中医药大学','南京林业大学','南京医科大学','宁波大学','湘潭大学','山西大学','河南大学'])

def get(url):
    r = requests.get(url, timeout=30, headers={'User-Agent':'Mozilla/5.0'})
    r.encoding = r.apparent_encoding or 'utf-8'
    return r.text

def normalize_name(x):
    return re.sub(r'\s+', '', x).strip('，,。；;、')

def parse_base_list():
    html = get(BASE_URL)
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text('\n')
    # 研招网页面是“学校名 省份”的反复结构；用省份分割附近文本做启发式抽取
    schools = []
    tokens = [normalize_name(t) for t in re.split(r'[\n\t\r ]+', text) if normalize_name(t)]
    for i in range(len(tokens)-1):
        if tokens[i+1] in PROVINCES and len(tokens[i]) >= 2 and '单位名称' not in tokens[i]:
            schools.append((tokens[i], tokens[i+1]))
    return schools

def parse_new_lists():
    found = []
    for url in NEW_URLS:
        try:
            text = BeautifulSoup(get(url), 'html.parser').get_text('\n')
        except Exception:
            continue
        for prov in PROVINCES:
            # 抽取“学校名 省份”结构
            for m in re.finditer(r'([\u4e00-\u9fa5（）()A-Za-z·]{2,40})\s*' + re.escape(prov), text):
                name = normalize_name(m.group(1))
                if len(name) > 2 and '教育部' not in name and '高校' not in name:
                    found.append((name, prov))
    return found

def score(name, province, is_new=False):
    city_score = 98 if province in ['北京市','上海市'] else 90 if province in ['江苏省','浙江省','广东省'] else 82
    school_level = 100 if name in RULE_985 else 88 if name in RULE_211 else 78 if name in DOUBLE else 65
    heat = round(0.52*school_level + 0.32*city_score + (6 if is_new else 0))
    return min(100, max(50, heat))

def main():
    # 用 seed 保底，避免网络或网页结构变化导致清空数据
    if SEED.exists():
        seed = json.loads(SEED.read_text(encoding='utf-8')).get('schools', [])
    else:
        seed = []
    by_name = {s['school_name']: s for s in seed}
    try:
        for name, prov in parse_base_list():
            by_name.setdefault(name, {'school_name':name, 'province':prov})
    except Exception as e:
        print('base fetch failed:', e)
    try:
        for name, prov in parse_new_lists():
            item = by_name.setdefault(name, {'school_name':name, 'province':prov})
            item['is_2025_new'] = '是'
    except Exception as e:
        print('new fetch failed:', e)
    out=[]
    now = datetime.now().strftime('%Y-%m-%d')
    for name, s in by_name.items():
        prov=s.get('province','')
        is_new=s.get('is_2025_new','否')=='是'
        heat=score(name, prov, is_new)
        out.append({
            **s,
            'school_name':name, 'province':prov, 'city':s.get('city') or CAPITAL.get(prov,''),
            'has_recommendation_qualification':'是',
            'is_2025_new':'是' if is_new else s.get('is_2025_new','否'),
            'is_985':'是' if name in RULE_985 else '否',
            'is_211':'是' if name in RULE_211 else '否',
            'is_double_first_class':'是' if name in DOUBLE else s.get('is_double_first_class','否'),
            'school_type':s.get('school_type','待补充'),
            'recommendation_quota':s.get('recommendation_quota',''),
            'graduate_count':s.get('graduate_count',''),
            'recommendation_rate':s.get('recommendation_rate',''),
            'heat_score':s.get('heat_score') or heat,
            'heat_level': 'S级' if heat>=90 else 'A级' if heat>=80 else 'B级' if heat>=70 else 'C级',
            'source_name':'自动抓取：研招网/教育部公开来源',
            'source_url':BASE_URL,
            'last_checked':now,
            'verify_status':'基础资格已自动更新；推免率/名额待核验',
        })
    out.sort(key=lambda x: int(float(x.get('heat_score') or 0)), reverse=True)
    OUT.write_text(json.dumps({'updated_at':now,'schools':out}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('updated', len(out), 'schools at', now)

if __name__ == '__main__':
    main()
