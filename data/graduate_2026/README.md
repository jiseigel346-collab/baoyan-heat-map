# 2026 全国硕士研究生学校-专业分数线数据包

## 文件说明

- `national_lines.csv/json`：教育部 2026 年全国硕士研究生招生考试国家线，按 14 大学科门类及特殊类别整理。
- `discipline_categories.csv/json`：研招网 2026 年硕士目录的“门类-一级学科/专业学位类别”层级表。
- `specialty_catalog.csv/json`：研招网 2026 年硕士专业目录中的专业清单，覆盖学术学位与专业学位。
- `school_specialty_score_base.csv/json`：按“学校-专业”展开的底表，包含院校代码、院校名称、省份、门类、一级学科/专业学位类别、专业代码、专业名称，并自动匹配国家线。
- `admission_min_scores_official_sample.csv/json`：招生单位官网拟录取名单解析样本，按专业计算初试最低分。
- `admission_min_scores_third_party.csv/json`：第三方聚合页中可解析的拟录取最低分样本，含 `result_min_score`。
- `yanshuoshi_2026_notice_details.csv`：全国 2026 拟录取/复试结果公告索引，用来继续逐校解析官网附件和成绩表。
- `kybang_2026_file_index.csv`：考友帮 A/B/211/985/医学页面提取的 2026 拟录取文件名/目录索引；该站页面未提供公开下载直链。
- `graduate_2026_result_tables.xlsx`：可直接筛选的 Excel 汇总，包含国家线、门类层级、学校专业底表、官网样本和第三方录取最低分样本。
- `result_summary.csv/json`：结果表行数汇总。
- `sources.json`：本次生成时间、来源、数据量和口径说明。

## 重要口径

`school_specialty_score_base` 中的 `final_admission_min_score` 暂时留空，`score_status` 标记为“待从招生单位2026拟录取名单核验”。

原因：研招网专业目录能证明招生单位和专业目录，教育部国家线能证明全国最低复试要求；但“最终录取最低分”没有全国统一公开总表，必须逐校进入研究生院/学院官网拟录取名单核验，不能用国家线或复试线替代。

`admission_min_scores_official_sample` 是官网拟录取名单直接计算出的录取最低分；`admission_min_scores_third_party` 是第三方聚合结果，能先用于筛选，但必须按 `source_url` 回到原始公告或招生单位官网复核。

`yanshuoshi_2026_notice_details` 是全国公告索引，不等于已全部解析出最低分；其中很多公告的成绩附件需要登录、下载 PDF/Excel 或跳转学校官网后才能计算。

`kybang_2026_file_index` 是补充索引，能辅助确认全国待解析附件范围，但没有公开文件直链时不能直接计算最低分。

`sources.json` 的 `collection_warnings` 会记录研招网未登录分页限制、访问频控等情况。出现这些警告时，相关表是“可核验底表/增量底表”，不应宣称为最终全量录取最低分表。

## 后续补分步骤

1. 按 `is_self_marking=是` 优先处理 34 所自主划线高校，先补 `school_retest_min_score` 和来源。
2. 按学校官网“2026 年硕士研究生拟录取名单”补 `final_admission_min_score`。
3. 每补一所学校，保留 `score_source_type`、`score_source_url` 和 `notes`，便于复查。
4. 对只公布复试线、不公布拟录取名单初试成绩的学校，不填 `final_admission_min_score`，只在备注说明。
