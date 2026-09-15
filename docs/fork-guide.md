# Codex + 富途投研使用指南

本 fork 面向中文投研、Codex 桌面工作区和已有富途相关能力。没有预设投资者的资金规模、持仓、期限或风险承受能力；这些条件缺失时，输出观察条件和情景估值，不编造个性化仓位。

## 推荐流程

1. `investment-checklist` 初筛：决定值得深入、待补证据或暂不研究。
2. `earnings-review` 财报精读：优先公司 IR、交易所披露、SEC 等原始文件。
3. 有实质分歧再运行 `investment-team`：四个分析角色按当前并发容量分批执行，保留各自来源和反方论点。
4. `thesis-tracker` / `thesis-drift`：记录原始假设、证伪条件、后续事实变化，不把措辞调整包装成基本面改善。

报告默认中文，先写结论、证据、主要不确定性；给出报告时间、行情时间、市场时区和财报截止期。历史样例只用于格式参考，不能当作当前事实。

## 富途怎么接入

- 先检查当前会话实际可调用的富途工具或已安装 `futuapi` Skill，阅读其说明；不要凭名称假设某个接口存在。
- 可用时，用富途获取行情、成交、公司资讯、财务字段。使用 `futuapi` 时按该 Skill 检查 SDK/OpenD 和数据权限，保留接口原始返回值及时间戳。
- 本 fork 不自带富途凭证或 SDK，不会自动启动 OpenD。不可用时明确记录原因，使用原始披露及可用的第二数据来源；不要伪装为已成功查询富途。
- 财报与富途的独立取数渠道可以检查转录错误，但两者可能来自同一份原始披露，不能宣称是两个独立事实证据。
- 财务关键数值回到原始财报核对；富途新闻若转引公告，与公告是同一证据链，不算两个独立事实证据。
- 情绪、技术面、资金流和衍生品异常只能补充研究，不能替代盈利、现金流和估值证据。
- 本工作流是研究用途。研究请求不包含交易授权；不会因报告中出现买入建议而调用下单、撤单、改单或解锁交易。

## 证据记录

在 `local/research/<公司>/<日期>/` 保存原始数据、证据表和报告。该目录被 Git 忽略，适合保存个人研究与组合信息；发布前另行整理公开版本。

每个关键数字记录以下字段，不能只留一个来源名称：

| 字段 | 内容 |
|---|---|
| metric / value / unit | 指标、原始数值字符串、单位 |
| currency / period / basis | 币种、期间、GAAP/Non-GAAP、合并/归母等口径 |
| source / url / locator | 来源、原文链接或本地文件、页码/表格位置/接口名 |
| observed_at / fetched_at | 数据时点及实际获取时间，包含时区 |
| evidence_chain | 原始披露/转引关系；两网站转载同一消息不算独立验证 |
| normalization | 汇率、ADR 比例、复权与换算步骤；保留换算前数值 |

例如，港股股价和以人民币披露的每股收益不能直接相除。先说明换算日期、汇率来源、单位和股本口径，再调用计算工具。

## 新的校验规则

`financial_rigor.py`：市值偏差超过 1% 返回失败；股价、股本、报告市值必须为正且有限。交叉验证以输入 JSON 的第一个来源为参考，偏差为 `abs(其他值 - 参考值) / abs(参考值)`，默认阈值 1%。参考值为零时仅其他值也为零才一致。至少提供两个不同名称的来源，来源独立性仍须人工/研究者核实。

`calc` 仅支持数字、括号、一元正负号和 `+ - * /`，数值从原始字符直接解析为 Decimal；有效数字精度为 28，循环小数仍需舍入。幂、整除、函数调用不在支持范围。原有 Benford 统计及部分展示/返回字段不承诺任意精度。

`report_audit.py verdict` 的三种结果：

| 结果 | 含义 | 退出码 |
|---|---|---|
| PASS | 所有提交的样本具备两个不同的具名来源，数值均在 1% 容差内 | 0 |
| FAIL | 至少存在一个已知的数值不一致，包括仅有一个核验来源且该来源不一致 | 1 |
| INCOMPLETE | 空样本、未完成项、缺少来源、重复来源或非法数值 | 1 |

发生数据不一致和缺证同时存在时，优先返回 FAIL。修正后重新核验。程序不能证明来源真实、口径一致，也不能证明你提交的清单覆盖了原始抽样；PASS 仅表示所提供样本的数值核验通过。

**股价、股本、汇率、EPS、自由现金流及影响结论的估值输入必须全量核验**；15% 随机抽样仅作为其他数据的补充。解析器是启发式的 Markdown 数字提取，须检查提取清单有无漏项。

```bash
python3 tools/financial_rigor.py calc --expr '0.1 + 0.2'
python3 tools/financial_rigor.py cross-validate --field revenue --values '{"原始年报":"100","富途":"100.5"}'
python3 tools/report_audit.py extract --report local/research/report.md --seed 42
```

将抽检模板的 `fetched_value`、`fetched_source`、`fetched_value2`、`fetched_source2` 全部填好，再传给 `verdict --results`。使用 `--output-json` 时 stdout 只包含 JSON，说明文字输出到 stderr；脚本应检查退出码。

## 安装与更新

在 fork 根目录运行，Python 3.9+：

```bash
./scripts/install-codex-skills.sh --skill investment-checklist --skill earnings-review --skill thesis-tracker
```

不指定 `--skill` 会安装全部 Skill。默认保留已存在的同名 Skill；要更新，加 `--replace`，旧版本保存在目标目录的 `.berkshire-backups/<时间>/`。Windows 使用同名 `.bat`，参数一致。`--dest` 可安装到测试目录。

安装包的 `references/runtime/` 含共享工具、研究流程和此指南，可在仓库外调用核心计算/抽检工具。市场取数脚本仍依赖网络和各自可选依赖，引用报告或历史数据的脚本仍可能需要完整仓库。更新工具后需重新安装相应 Skill。工具工作目录设为 runtime 时，报告输入与输出必须使用项目中的绝对路径，避免将文件读写到安装目录。

团队角色按 Codex 实际并发容量运行。联网不可用时交付资料缺口清单或基于用户原文的有限分析，不能给出伪装成实时核验的目标价。

## 开发验证

```bash
python3 -m unittest discover -s tests
python3 scripts/sync-codex-skills.py --check
python3 scripts/sync-codex-prompts.py --check
```

测试覆盖数值校验、缺证阻断、JSON/退出码、安装保留与备份、仓库外工具运行。它们不验证真实行情可用性、收益表现或完整多 Agent 研究质量。
