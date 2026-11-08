# 零费用公共站：GitHub Actions + GitHub Pages

此目录包含可执行部署流程；文件存在不代表已经发布。确认某次运行时，应核对 Actions 任务、`helm/public-state` 分支的状态提交、Pages 部署结果和站点来源状态页。手动 workflow_dispatch 验证云执行；只有真实 `schedule` 触发过的构建才标注“定时运行已观察”。

## 公开边界

公开仓库只存公开网页资产、领域介绍、研究解读、官方来源元数据和采集/构建代码。它不包含本机 server.py、账号、会话、学习目标、进度、笔记或 `.runtime`。原框架文件里的个性学习 `learning` 字段在打包时删除。公开 GitHub Pages 不提供登录或私人笔记服务；这些仍在本机。公开仓库意味着其中代码和公开内容也可被他人读取，这是 GitHub Free 的这条部署路线所需条件。

不要把原项目或整个“学校课程”目录推送到新仓库。必须先创建独立公开包：

```sh
python3 app/server.py --export-domains
python3 scripts/stage_public_repo.py --out /absolute/path/to/new-public-repo
```

第一行只导出维护者已修改的公开领域介绍，排除作者身份；第二行按明确文件白名单打包，并从公共采集 DB 抽取公共表作为 bootstrap。检查包内 PUBLIC-MANIFEST.json 后，从新包初始化 Git。默认分支可为 `helm/site`，不需要名为 main。

## 首次部署前提

1. 使用拥有权限的 GitHub 账号创建**独立公开仓库**，推送已检查的包；不能复用已有私人课程仓库，也不用发布本机后台。
2. 工作流 `.github/workflows/daily-public.yml` 位于默认分支。仓库允许 GitHub Actions，并允许该工作流 `contents: write`；它只向新分支 `helm/public-state` 保存公共快照，不修改默认分支。
3. 仓库 Settings → Pages → Source 选择 GitHub Actions。配置 `github-pages` environment 允许默认分支部署。
4. 无需任何第三方 API key、模型服务、数据库服务或信用卡。工作流只使用 GitHub 给这次运行签发的最小权限 token，不保存自己的 PAT。
5. 手动运行 Daily public research and Pages；不必等到下一天才能验证云端抓取与写入。定时是每天 UTC 23:17，即 Asia/Taipei 次日 07:17，调度不是准点保证。

主线程可在上述独立包创建仓库并配置 Pages；此准备脚本自己不创建云资源、不推送或开启付费产品。

## 每轮实际流程

`恢复公共状态 → 应用版本化公开编辑 → 联网采集 → 保存公共状态 → 导出公开JSON → 构建静态站 → Pages部署 → 标记来源健康状态 → 清理本次Pages artifact`。

- SQLite 运行文件只在 runner 临时目录。长期状态来自单独 `helm/public-state` 分支中 `research-state.json`，不是会过期的 Actions artifact/cache。恢复按表和列白名单插入数据，不执行远端 SQL。
- 状态仅包含 works、identifiers、versions、version_changes、work_sources、work_domains、editorial、source_runs。任何其他表都不会导出，未知 checkpoint 表则拒绝恢复。运行日志每来源保留最近 100 次；更早版本仍可从 Git 历史恢复。
- 来源失败记录会被保存，旧内容保留。部分来源失败时仍部署带有真实失败状态的网页，随后 source-health job 失败，让 Actions 状态提醒维护者；不是忽略失败而变绿。
- 同一工作流并发串行，状态 push 不使用 force。并发写入冲突会停止发布，避免覆盖已保存状态。默认分支更新和公开内容修订不受状态分支提交污染。
- 公开中文摘要/观点覆盖来自 `pipeline/editorial-overrides.json`。较新 revision 可覆盖云状态，较旧版本跳过，同 revision 不同文本报冲突。领域介绍的 `content/domain-overrides.json` 每次静态构建应用。
- 日常采集不需要重新生成深读文章，也不调用模型。内容精选是维护者公开编辑，规则分不等于论文质量。

## 手动验收

在独立公开仓库中可按相同流程本机验证：

```sh
python3 -m unittest discover -s pipeline -v
python3 -m unittest discover -s scripts -p test_cloud.py -v
python3 scripts/public_state.py pull
python3 scripts/public_state.py apply-editorial
python3 pipeline/cli.py ingest
python3 pipeline/cli.py export --out pipeline/public-feed.json
python3 scripts/build_public.py --out _site
```

`pull/push` 是真实 Git 远端操作，部署准备之外不要对错误仓库运行。工作流实际执行它们；一般本机预览只需 export/build。本机 `--cloud` 被拒绝，防止伪造云端状态。

验收要看：真实来源 HTTP 状态与检查时间；第二次运行 work/version 数不重复；人工摘要和领域修订保留；站点可检索与回到地图；匿名站点没有私人数据；私有后端/DB不在仓库或 `_site`。校园网可达性需实际访问站点、JS和原始来源，不能用 Pages 部署成功代替。

## 免费额度和运行边界（2026-11-08核实）

- [GitHub Actions账单规则](https://docs.github.com/en/billing/concepts/product-billing/github-actions)：公开仓库标准 hosted runner 免费；大型 runner 会收费，因此本方案固定普通 ubuntu-latest。Artifacts 与 Packages 共享存储额度，Free 为 500 MB；不用持久 cache。Pages artifact 保留期设 1 天，并在本次部署成功/失败后仅删除本次运行名为 github-pages 的 artifact；未删除成功时最多留到过期。不能用该额度推出整个账号其他项目都不会计费。
- [GitHub Pages限制](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)：Free 支持公开仓库；发布站点不超过 1 GB、源仓库建议不超过 1 GB、每月软带宽 100 GB、部署 10 分钟超时。10 builds/hour 软限不适用于自定义 Actions 工作流。本项目额外限制静态包 20 MB、公共 checkpoint 50 MB，超出停止而不自动购买升级。
- [自定义Pages工作流](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)：使用官方 checkout、upload-pages-artifact、deploy-pages；部署 job 单独拥有 pages/id-token 写权限，采集 job 只有内容写权限，清理 job 只有本仓库 artifact 写权限。
- [定时故障说明](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows)：schedule 高负载会延迟甚至丢弃；选非整点减轻碰撞但不能保证准点。失败后可手动补跑。
- [停用规则](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)：公开仓库 60 天无活动自动停用，fork 的 schedule 默认不启用。真实检查状态提交通常会产生仓库活动，但仍需观察任务是否按天运行；不能把“没有新论文”和“任务未执行”混同。

GitHub Pages 适合公开学术知识阅读，不应接收密码、信用卡或承担商业SaaS交易。本方案不收集这些信息；公开和私人系统分开。

## 维护与增长

定期在 Sources 页面检查失败和最近成功时间；默认资料查询目前有数量窗口，分页回补仍待完善，不能宣称收全具身智能。Git 状态采用文本 JSON，方便审阅和增量压缩；持续积累仍会增加仓库历史体积，应在触及保护上限前评估更合适的持久存储或归档，不自动产生费用。不得为了省空间静默删除用户人工观点或私人数据。

公开修订需要从本机明确导出到公开文件并提交默认分支；未导出的本机修改不会神奇同步到云端。首次手动云端验收通过后仍应等真实下一次 schedule 的证据，才能确认定时触发实际发生。
