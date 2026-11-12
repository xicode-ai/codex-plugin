---
name: meeting-stone-timesheet
description: Generate today's cross-project work log and, when authorized, record explicitly specified hours against uniquely matched Meeting Stone tasks. Use when the user asks to write Meeting Stone timesheets, fill in 工时, or generate a daily report and record hours in Meeting Stone.
---

# Meeting Stone 日报与工时

仅在用户请求此流程时运行；创建、安装或查看插件本身不构成一次工时运行。仅调用 Meeting Stone API，不打开或操作其 UI，也不做写入后的 UI 回查。本次运行以 Asia/Shanghai 当天为准；跨日继续时重新生成当日日报并重新执行全部查询和授权步骤。

## 日报

先调用可用的 `daily-work-log` 技能；它不可用时按本节独立执行，不因依赖缺失而编造内容。覆盖当天可访问的 Claude、Codex、Zcode 工作会话，优先运行 `tl mem projects --global`，再分别对 `codex`、`claude`、`zcode` 执行 `tl mem list --global --since YYYY-MM-DD --until YYYY-MM-DD --platform PLATFORM --limit 100`，有更多结果时继续取得可访问的相关会话。对相关会话按需用 `tl mem context SESSION_ID --turns 3 --around 1` 或其他安全的摘要方式确认当天工作及最新状态；不足时回退到可访问的原始会话、Trellis journal 等。不要把本次日报或写工时的任务本身当作工作进展，也不要将旧会话的历史内容误记为今天的进展。合并同一事项，保留未提交、未验证、未确认、推进中、排查中等状态；数据不全时说明来源限制。

按以下格式在当前会话输出，不默认保存文件：

```md
# YYYY-MM-DD - Person

## 今日进展
- 项目 / Item：做了什么，进度如何。

## 问题、风险、需帮助项
- 项目 / Item：风险或问题；无阻塞时写“无阻塞项”。

## 明日计划
- 项目 / Item：有证据支持的后续工作；无记录时写“未记录”。
```

## 认证和传输

- 仅使用用户在本次运行消息中明确提供的 `meeting_stone_session`；提取值后只在内存中保留，不使用历史会话、环境变量、配置文件或浏览器中已有凭据。未获得有效 Cookie 时不调用 API，输出日报及“工时未写入：暂无有效认证 Cookie（等待用户提供 meeting_stone_session）”。同一会话用户随后提供 Cookie 时继续。
- 对 `https://meeting-stone.yfsj.cc` 的所有 curl HTTPS 请求使用 `-k`；不对其他目标使用此例外。Cookie 必须内联传递为 `-b 'meeting_stone_session=<值>'` 或 `-H 'Cookie: meeting_stone_session=<值>'`，禁止 Cookie 文件。不要重定向到其他域名或自动跟随重定向。
- Cookie、Token、密码、完整响应体和原始请求不得写入计划任务提示、文件、日报或日志，也不得向用户或工具输出回显。执行器应关闭命令回显和调试输出，避免把带有真实 Cookie 的命令、请求或响应直接显示在工具调用参数、终端历史或工具结果中；无法做到时停止，说明认证无法安全使用。仅在内存解析 API 返回，输出脱敏后的必要字段和状态。绝不把真实凭据放进命令示例。
- 任一接口 401 时停止后续调用，允许等待用户随后最新明确提供的 Cookie，对同一接口最多重试一次。重试仍 401、连接失败或响应仍无法可靠判断时，立即停止所有后续 Meeting Stone 调用和写入，输出日报及未写入原因。不要把 401 文案解释为必须飞书登录。
- 除该 401 规则外，连接失败、超时、非成功响应、解析失败或结果不明确均立即停止后续调用及写入；特别是 POST 结果不明确时不得重试，报告结果未知及尚未提交的任务，避免重复写入。明确成功的 POST 每个任务最多一次。

## 写入流程

1. 使用 `GET https://meeting-stone.yfsj.cc/api/timesheet/logs?scope=mine` 读取当前用户日志。返回应为 JSON 数组；若接口明确提供分页，安全读取所有可访问的相关页面，不能确认分页完整、日期字段含义、结构或查询成功则停止。以当天 `YYYY-MM-DD` 严格检查每条日期：任何今天的日志都立即跳过所有 POST，并报告“今天已有工时日志，未重复写入”。不能可靠解析任一相关记录时停止，不推断为空。
2. 确认当天无任何日志后调用 `GET https://meeting-stone.yfsj.cc/api/my-tasks?scope=current`。按返回任务的 `title`、`projectName` 和 Teable 标识与日报的项目 / Item 精确或明确匹配；只能关联唯一候选。多个候选、不匹配或关键标识缺失时不猜、不写，报告未关联任务。合并属于同一任务的今日进展，生成脱敏简洁描述。
3. 向用户展示每个唯一匹配任务的日期、任务标题、脱敏工作描述及留空的工时，明确要求用户为每个任务指定小时数。Cookie 构成本次写入流程的认证授权，但不构成工时数值。禁止默认 8 小时、固定总时长、平均分配或从内容推断时长。仅用户本次运行明确、无歧义给出有效数值的任务可写；未给出或含糊的任务不写并标记“待用户输入工时”。部分给出时可仅写已明确指定的任务，并报告其余任务。数值按用户给出写入，不做上限截断；单日合计明显超过 24 小时，先向用户确认，得到确认前不写。保留已匹配清单供同一会话继续。
4. 所有已获工时的任务准备完成后，紧接首次 POST 前再次完整读取 `GET /api/timesheet/logs?scope=mine`，按第 1 步检查。如已出现任意当天日志，跳过全部写入并报告“授权后发现今天已有工时日志，未重复写入”。失败或无法判断则停止。复核之后逐个 POST，不将自己刚提交的第一条当作阻止同一批后续任务的重复日志。
5. 对每个已明确指定工时且唯一匹配的任务，仅提交一次 `POST https://meeting-stone.yfsj.cc/api/timesheet/logs`，JSON 含 `date`（上海当天）、`workType`（默认“项目工作”）、`hours`（用户数值）、`completionStatus`（默认“正常”）、`description`（脱敏简洁合并进展）以及 `teableSourceId`、`teableBaseId`、`teableTableId`、`teableRecordId`、`teableTaskTitleSnapshot`，依次原样取自任务接口的 `sourceId`、`baseId`、`tableId`、`recordId`、`title`；不编造。仅在状态和响应明确表明成功时报告成功。

始终先输出日报，再报告已成功写入的任务标题和小时数、重复日志跳过、待输入工时的匹配任务，或未写入任务及原因。部分成功时准确区分已成功、结果未知及未提交；不要伪造成功。
