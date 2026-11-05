# 分析检查点契约 v1.0

在输出目录写 `analysis/analysis.json`，严格 UTF-8 JSON（不接受 NaN / Infinity 等扩展常量），顶层 `schema_version` 为 `"1.0"`。字段均区分大小写。结构示例见 [minimal-analysis.json](../examples/minimal-analysis.json)：它故意表示未取证的草稿，不是业务分析结论。

## 通用约定

除 documents 外，每条记录都有全局唯一 `id`，匹配 `[A-Za-z][A-Za-z0-9_.-]{0,127}`。采用如 `REPO-OMS`、`E-001`、`C-001`、`S-CANCEL` 的稳定标识。引用字段保存 ID 数组；空数组可以表达尚无发现，不能引用不存在的对象。

下列顶层字段全部必须存在：`run` 对象，以及 `repositories`、`data_sources`、`queries`、`evidence`、`claims`、`scenarios`、`handoffs`、`coverage`、`documents` 数组。时间使用含时区的 ISO 8601；所有路径和业务文本应脱敏。允许附加说明字段，校验脚本只核对下列契约，不代表附加字段已经过验证。

## run 与输入

`run` 必填：`id`、`system`、`scope`、`audience`、`generated_at`、`status`（draft / complete）、`discovery_complete`（布尔）、`limitations`（字符串数组）、`next_actions`（字符串数组）。limitations 写清未提供的 UI、运行环境、数据或外部系统；不以空数组掩盖已知盲区。

`repositories` 至少一条：`id`、`root`（取证时的绝对路径）、`commit`（无版本库写 unversioned）、`dirty`（布尔）。结构检查接受 POSIX 或 Windows 绝对路径，以便跨平台阅读历史记录；执行本地源码检查时必须映射为当前平台可访问的绝对路径。可以补充分支、职责、快照说明。证据哈希记录实际读取工作区文件，不能以 commit 代替未提交内容。

`data_sources` 每条：`id`、`alias`、`environment`、`schema`、`status`（available / unavailable / not_requested）。仅记录连接别名，不写 DSN、口令、token 或可识别客户的原始行。无连接时数组可为空。

## queries

每条必填 `id`、`source_id`、`sql`、`captured_at`、`window`、`tenant_filter`、`soft_delete_filter`、`grain`、`row_count`（非负整数）、`truncated`（布尔）、`outcome`（succeeded / failed）。可补充脱敏的聚合结果和失败原因。没有某类过滤时写明不适用的依据，不留空。

返回零行依然是 succeeded；失败是 failed，不能生成数据库观察证据。`row_count` 表示返回行数，不是未经计算的业务总量；截断时单列限制。脚本保存与检查查询上下文，**从不执行或批准 SQL**，也不判断 SQL 是否只读、预算是否合适、结果是否真实。

## evidence

共同必填：`id`、`kind`、`observed`（实际读取或观察到的内容）、`method`、`captured_at`。

| kind | 额外必填字段 |
| --- | --- |
| code | `repo_id`、`path`（仓库内相对路径）、`symbol`、`line_start`、`line_end`（含端点，1-based）、`sha256`（完整文件字节的小写 SHA-256） |
| database | `query_id`，指向 succeeded 查询 |
| document | `locator`（具体文件/链接及章节）、`version`（版本/日期/快照标识） |

源码证据的快照由仓库 commit/dirty 和文件 SHA-256 共同描述。可将短片段补充到 observed，不能只复制长代码作为业务解释。document 证据可记录内容哈希以增强定位，但脚本不自动抓取和验证外部文档。

## claims

共同必填：`id`、`kind`、`text`、`scope`（环境/版本/条件）、`evidence_ids`、`counter_evidence_ids`、`limitations`。反证放入 counter_evidence_ids，相关冲突和问题可另加关联字段。

| kind | 支持要求 |
| --- | --- |
| implementation | 至少一条 code 证据 |
| data_observation | 至少一条 database 证据 |
| documented_intent | 至少一条 document 证据；不等于已实现 |
| product_hypothesis | 额外必填 `rationale`；可没有直接证据，但必须显式说明推导与待验证点 |
| question | 表达待确认的信息缺口；支持数组可为空 |

类型约束只证明引用类别正确，语义上仍需人工核对。例如不能用“函数存在”的片段证明“线上有人使用”。

## scenarios

每条必填：`id`、`name`、`actor`、`trigger`、`preconditions`（字符串数组）、`claim_ids`、`steps`（至少一步）、`states`、`exceptions`、`handoff_ids`、`closure`、`outcome`。

- steps / exceptions 每条：`text`、`claim_ids`（非空）。
- states 每条：`object`、`from`、`to`、`claim_ids`（非空）。不确定的转换用 question claim，图中也标为待确认。
- closure 为 closed / boundary / gap。boundary、gap 额外必填 `gap`，说明外部责任边界或缺失环节。
- closed 不能包含 one_sided / conflict handoff；此字段表达证据可支持的场景边界，不是交易最终成功保证。

场景还可补充用户问题、输入输出、权限、验收示例。没有真实角色信息时写“调用方，身份待确认”并附 question，不凭方法名发明用户角色。

## handoffs

每条必填：`id`、`scenario_id`、`from_repo`、`to_repo`（仓库 ID 或 null）、`transport`（http / event / shared_data / rpc / other）、`contract`、`business_key`、`status`、`producer_evidence_ids`、`consumer_evidence_ids`。

- 至少有一端证据。证据为 code，属于对应端的仓库；外部系统缺源码时保留未知端。
- matched 必须两端均有证据，to_repo 非 null；还需分析者核对接口、topic、字段及语义一致，脚本不推断契约是否兼容。
- one_sided / conflict 额外必填 `issue`。to_repo 为 null 时必填 `external_name`。
- scenario_id 与对应场景的 handoff_ids 必须互相引用。

消息版本或载荷不一致时，contract 保存两端实际契约并在 issue 解释；不要先统一名称再把冲突抹掉。

## coverage 与 documents

coverage 每条：`id`、`kind`（repository / scenario / handoff / entrypoint / capability）、`subject_id`、`status`（unseen / in_progress / analyzed / blocked / excluded）。blocked / excluded 必填 `reason`。相同 kind + subject_id 只记一条。前三类 subject_id 必须指向对应记录；入口和能力 ID 由发现清单维护。

完整报告必须覆盖声明的每个仓库、场景、交接；`complete` 要求 discovery_complete=true、coverage 无未完成状态、documents 非空。已分析但发现业务断点仍可记录 analyzed，正文必须保留断点。complete 表示约定范围分析完成，不能解释为全系统无未知或业务已闭环。

documents 每条包含 path（相对输出根目录）、claim_ids 和 scenario_ids；这些数组在私有分析记录中建立文档与结论/场景的映射，引用的对象必须存在。每份知识文档至少有一条私有 claim 关联，不能在去掉公开引用时连同证据映射一起丢弃。knowledge/ 文档的业务正文不出现 ID、源码位置、版本/哈希、类/方法或接口信息，也不在隐藏注释中保留它们。图示与正文的证据关联同样保存在 analysis/。

knowledge 对象包含稳定 ID、层次、系统/模块/主题和别名，规则见 [knowledge-document-guide.md](knowledge-document-guide.md)。基础 analysis schema 仍为 1.0。validate_analysis.py 核对私有引用和快照，不再要求知识正文出现审计引用；export_knowledge.py 进一步核对知识目录、层次、Markdown 说明结构以及明显代码/审计残留。普通 analysis 文档若声明了内联审计引用仍按原规则核对。

## 校验与局限

```bash
python3 <skill-dir>/scripts/validate_analysis.py <output>/analysis/analysis.json --check-sources --docs-root <output>
```

退出码：0 结构检查通过；1 契约、引用、快照或文档检查失败；2 输入不可读或 JSON 错误。默认只核对结构及引用，不读取源码或文档。`--check-sources` 检查哈希、行范围与路径越界，不运行 git、不验证当前 commit、不判断文件内容的业务意义。`--docs-root` 检查映射文件、私有引用（知识正文不要求内联 ID）、围栏闭合和代码围栏外的基础内联 Markdown 本地链接（支持平衡括号、反斜杠转义和尖括号目标）；不检查锚点、reference-style 链接、Mermaid 语法或图表语义。

脚本不充当敏感信息扫描器；错误信息避免回显输入内容，操作者仍需在保存产物之前脱敏。结构合法的陈述仍可能事实错误，必须结合来源作语义复核。
