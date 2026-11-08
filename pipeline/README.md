# 免费研究元数据适配层

这是已可在本机运行的 Python 标准库 + SQLite 适配层，服务于具身智能地图的内容验证。它不是 AIHOT 的替代发行版，也没有配置云端数据库或每日任务。费用为零，未调用模型、下载权重、创建远程仓库或开通收费服务。

## 当前可运行范围

`sources.json` 现有 6 个官方公开端点：arXiv cs.RO 最近 30 篇、4 篇基础工作的版本核查、arXiv cs.RO RSS、LeRobot 最近 5 次代码发行、OpenVLA 模型仓库元数据，以及 NVIDIA Robotics 企业动态 RSS。前 5 个来源第一批实际入库 40 条资料，二次联网采集仍为 40 条。arXiv RSS 这次合法返回 0 条，保存为 `no_change`；最初沙箱 DNS 失败也保留在运行历史中，未篡改为成功。新增 NVIDIA 源的本机请求遇到 TLS EOF，已保存 `failed`，当前没有该源入库条目；后续云端应实际重查，不能视为已经可用。前 5 源的详细 HTTP 状态、时间、响应哈希和检查项见 `verification.json`；最新状态由 `cli.py check` 与 `public-feed.json` 提供。

这是有明确数量上限的真实采集验收，不声称已覆盖全部机器人研究。当前没有跨页回补：若两次检查之间超过 30 篇新提交，可能漏收；常规查询按 submittedDate 排序，旧论文新版仅在返回窗口或基础工作清单中被发现。正式每日运行前要增加按时间游标分页、旧文版本补查和异常告警。近期条目、历史基础工作与模型仓库日期在导出中明确区分。

本层复用了项目第一阶段 `核查/check_public_sources.py` 的 Atom 辅助函数、RSS/GitHub/Hugging Face 格式校验；该文件未修改。来源中任何指令、脚本或链接都不会执行。只允许固定 HTTPS 主机与 API 路径，重定向也校验，拒绝 XML 外部实体，单响应最大 3 MB，arXiv 请求间隔至少 3.1 秒。公开资源不需要凭证。

## 命令

从本目录运行；Python 3.9 及以上，无需 pip 安装。

```sh
python3 cli.py ingest
python3 cli.py ingest --source arxiv_rss
python3 cli.py check
python3 cli.py search OpenVLA
python3 cli.py search 触觉 --domain perception
python3 cli.py export --out public-feed.json
python3 -m unittest -v
```

默认数据库为 `.data/research.sqlite`，不会放入 Git。使用另一数据库时全局参数放在子命令前：`python3 cli.py --db /absolute/path/research.sqlite check`。`check` 只读已保存运行状态并执行 SQLite 完整性检查，不暗中联网。`ingest` 会联网并保存运行记录。`export` 未传 `--out` 时直接向 stdout 输出 JSON。

修改维护者公开摘要/观点：

```sh
python3 cli.py edit WORK_ID --expected-revision 0 --json-stdin < edit.json
```

`edit.json` 内容例如 `{"title_zh":"中文标题","summary":"人工整理摘要","commentary":"明确署为整理者观点"}`，可以只提供部分字段。首次 revision 为 0；成功后递增。旧 revision 写入退出码 3，保持现有编辑；内容或数据库错误退出码 2；采集部分失败退出码 1。命令成功结果与业务错误均为 JSON。CLI 是本机维护接口，不自带网页身份系统，公网 API 调用它之前必须做服务端管理员认证、CSRF/Origin 防护及输入限制。

`verify_live.py` 是显式联网验收脚本：为 OpenVLA 纸面记录写入一段基于已读摘要的中文人工整理，再重跑全部来源，验证人工内容、版本和检索。它会修改该条公开编辑；不应作为定时任务，日常只用 `cli.py ingest`。

## 数据语义与可靠性

- `works` 是论文或发布记录；`identifiers` 把标准化 DOI、去掉版本号的 arXiv ID、GitHub release ID 或 HF 模型 ID 关联到同一 work。ID 相同不重复插入。DOI 与 arXiv 指向两个已有 work 时整批回滚并报冲突，不自动合并可能含人工编辑的资料。
- `versions` 单独保留观察到的 arXiv vN、GitHub tag、HF commit SHA。未注明版本的 RSS 观察标成 `unspecified`；不冒充确定版本。重跑既不增加 work，也不增加相同版本。同版本元数据改变时保存 `version_changes` 旧快照。
- API 首次提交时间与版本更新时间分别保存；RSS pubDate 只算公告时间，不覆盖论文原始发布日期。HF createdAt 是仓库建立时间，`published_at` 保持 null，不能拿它当模型发布日。
- `source_runs` 每次检查独立记录 `running / updated / no_change / failed`；空 feed 是成功无新增，HTTP/解析失败是 failed。`last_attempt_at`、`last_success_at` 和内容变更时间分别计算。失败不删除上一批内容，也不刷新成功时间。
- `editorial` 是单独的公开人工覆盖层；采集从不修改它。编辑有乐观版本检查，机器摘要与人工摘要始终同时保留。私人学习数据属于主应用的独立数据库，本层不接收或存储它。
- `work_domains` 只引用已有知识框架 ID。中英文词表匹配是透明规则，不是语义理解或人工确认；不能据此改地图。无匹配项保留待分类。`rule_score` 是检索排序分，不是论文质量或精选认证：官方来源 20、有摘要/说明 20、明确版本 10、每个匹配子领域 10（最多 40），最高 90。
- 搜索用绑定参数和 `instr()` 做字面子串查询，中文和英文可查，百分号和 SQL 片段不会变成通配符或代码。小规模适配层的检索会扫描内容；正式库可沿用 Postgres/PGroonga。

## 前台公开 JSON 契约

顶层：`schema_version`、`generated_at`、`mode=local_verified_adapter`、`model_calls=0`、`automatic_interpretation=false`、`schedule_status=not_deployed`、`sources`、`items`、`counts`、`domain_updates`。

每条 `items` 含稳定 `id`、`kind`、`title/title_en`、原文 `url`、来源原文 `summary`、`doi/arxiv_id`、`published_at`、`publication_date_basis`、`version/version_updated_at`、`versions`、`source_ids`、`domain_ids`、`domain_mapping_basis`、`classification_status`、`method_tags`、`rule_score/selection_reason`、`evidence_scope/claims_status`、`first_seen_at/last_seen_at/content_changed_at`。

`editorial` 为 null 或 `{title_zh,summary,commentary,revision,updated_at}`。前台可以优先使用人工中文标题与摘要，但须显示其为人工整理，始终保留英文原题和原始摘要入口；`commentary` 是维护者公开观点，不是私人笔记。所有外部文本都以 textContent 或经审查的 Markdown 渲染器处理，不能直接插入 innerHTML。

`domain_updates[domain_id].count` 是总关联资料数，**不是今日更新数**。`last_content_change_at` 是本地内容发生变化的时间，包括首次入库和人工编辑；不能把 2024 年论文首次入库标成 2026 年新论文。`generated_at` 只说明导出时间，不能显示为最近成功采集。

导出显式选择公开表和字段，未使用全库导出。测试向同一临时库插入 private_notes 哨兵数据后，公开 JSON 仍不含它。SQLite、备份、运行日志不得放进站点静态目录；公开的只有导出文件。

## 与 AIHOT 的复用边界

审计基线：`KKKKhazix/AIHOT` commit `bedfb5146fce8738c02f779f62e282c8b3fa4221`。本目录没有复制上游代码；只是新增研究领域适配。正式集成保持上游 LICENSE/NOTICE，不使用 AIHOT 名称或 Logo。

| 本层 | 后续 AIHOT 集成位置 |
|---|---|
| 官方源配置与解析 | 复用 `industry/sources.json`、`packages/backend/src/sources/`；特有论文 ID/版本处理放模块 |
| works / identifiers / versions | `modules/embodied/` 自己的 Postgres 迁移与读取层；与 article_id 显式关联 |
| work_domains / 规则词表 | `industry/` 的具身分类与 `modules/embodied/` 地图关系；不改共享新闻评分逻辑 |
| editorial | 对照原有 `editorial_overrides` 的版本化覆盖语义；不把本机表直接冒充已兼容迁移 |
| source_runs | 对接上游 worker job_runs 与模块 `server.ts` 的 schedule/queue 插口 |
| public-feed.json | 所有正式公开出口最终通过上游 `publication/` 读取层 |

附 `aihot-source-template.json` 为上游 `json_list` 的字段映射模板，已按当前 `docs/sources.md` 对照；需替换自己的站点 URL 后后台预览，**尚未导入或端到端运行 AIHOT**。通用 source 字段只能传标题、摘要、原链接、日期和 external ID；丰富的领域与版本关系仍由独立模块读取完整契约，不能靠模板自动完成。

## 免费云端后续

本机脚本可在 GitHub Actions 的 Python 环境运行，但当前未创建任何 schedule、repo 或云资源。Actions runner 磁盘不持久；不能每次创建空 SQLite 就声称持续去重或保护人工编辑。云端上线前须选择持久存储/原子备份恢复方案或迁回 AIHOT 的 Postgres，实现单写入者与并发控制，并从全新云任务验证：读取上一批状态 → 采集 → 保留人工覆盖 → 持久写入 → 回读 → 更新公开 JSON。

免费模式下采集与规则整理继续；没有模型服务就不会自动生成深度解读。模型预算始终为零，不因缺全文、网络失败或待翻译而自动调用付费服务。每日更新尚未部署，身份权限由主应用单独验收。

未验证：全年来源覆盖、分页回补、云端无人值守运行、校园网连通性、AIHOT Postgres 模块集成；这些限制不会被本机测试通过所替代。
