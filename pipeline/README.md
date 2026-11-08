# 公开研究资料采集

Python 标准库 + SQLite；不调用模型服务。公共站与每日 GitHub Actions 已由 `sir-zhang1/embodied-atlas` 承载；每天 07:17（UTC+8）检查，运行实况以站点来源页和 Actions 为准。

## 30 个来源端点

原有6个端点：arXiv Robotics 最近提交、4项基础工作版本核查、arXiv RSS、LeRobot releases、OpenVLA 模型元数据、NVIDIA Robotics官方动态。

新增24种期刊通过出版方向 Crossref 登记的元数据采集。机器人专题期刊收其期刊条目；综合/交叉期刊以题名和可获取摘要匹配机器人、具身、触觉器件、电子皮肤等主题。期刊清单、ISSN、回补窗口见 `sources.json`；分区、指标的年份、出处与可信程度见 `content/journals.json`。

首次回补为2024年以来发表、在各源配置的近7–365日内更新登记的记录，并非完整历史卷期。随后从上次成功检查起点向前重叠2日，按Crossref更新时间增量分页，每页200条。短页才完成窗口；最多30页/6000条、合并响应12 MB，超限或分页停滞保存失败且不推进成功检查点。429与部分5xx最多重试3次。大批历史回填可能触及保护限制，需人工拆分窗口；不会静默截断后报成功。

arXiv仍有最近30条的窗口和4篇固定版本检查；它尚未实现完整历史回补。不能由本站未收录推断没有相关研究。综合刊可能缺摘要而漏选，关键词也可能误选；地图关联为规则候选，非人工确认。

## 阅读与日期边界

- DOI与明确的arXiv标识用于去重；只有出版方明确登记的预印本关系才关联，不用相似题名强行合并。
- 正式出版、arXiv vN、代码tag、模型commit分别保留。日期保留原始年/月/日精度，不把元数据更新日或入库日当首次发表；未来卷期日期标为预排期。
- 期刊仅保存短摘要节选、完整摘要指纹与主题匹配依据。无摘要明确标注，不自动生成内容。中文摘要整理和全文导读由维护者独立编写，展示阅读范围；摘要指纹改变时旧摘要导读停止自动套用。
- 分区不等于单篇质量：中科院采用高校公开的2025升级版；JCR机构转载值标为 reported，并保留原表年份标签，未核对原库的指标不冒充 verified。出版方明确年份的JIF单列。

## 命令

```sh
python3 pipeline/cli.py ingest
python3 pipeline/cli.py ingest --source journal_science_robotics
python3 pipeline/cli.py check
python3 pipeline/cli.py search 触觉 --domain perception
python3 pipeline/cli.py export --out pipeline/public-feed.json
python3 -m unittest discover -s pipeline -v
```

另选数据库：`python3 pipeline/cli.py --db /absolute/path/research.sqlite check`。CLI对单个来源失败返回非零状态并保留旧资料，云端仍发布诚实的失败状态，再以source-health任务提示异常。

人工编辑独立存于editorial表，采集不改写；编辑使用expected revision防止旧版本覆盖。公开检索只使用绑定参数。公开导出白名单包括研究资料、版本、来源记录、领域关联和公开人工编辑；不含私有学习表。私有后台在本机，未发布到GitHub Pages。

云端持久状态、并发控制与公开打包边界见 `scripts/DEPLOYMENT.md`。状态分支保留版本与人工覆盖，不依赖会过期的artifact；正常运行不会调用付费服务。

## AIHOT参考范围

上游审计基线为 `KKKKhazix/AIHOT` commit `bedfb5146fce8738c02f779f62e282c8b3fa4221`。本层没有复制其代码，也没有声称已端到端集成其Postgres后台。`aihot-source-template.json`仅是可供后续接入的字段映射模板。当前运行的是本项目独立的免费公共元数据适配层。
