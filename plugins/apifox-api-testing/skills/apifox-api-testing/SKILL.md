---
name: apifox-api-testing
description: Use when a user provides a PRD and environment identifier and asks Codex to design or execute full-scenario API tests through configured Apifox and dbhub MCPs.
---

# Apifox API Testing

根据用户提供的 PRD 文档、测试环境标识和可选的用户测试用例，设计、审阅并执行可追踪的全场景接口测试。运行时只调用当前 Codex 会话中实际可用的 Apifox MCP 和 dbhub MCP；本 Skill 不携带凭据、不猜测环境、不把局部结果表述为生产或发布就绪。

## 1. 输入契约

最小输入必须同时包含：

1. PRD 文档或明确的需求描述。
2. 测试环境标识，例如 `staging-oms`。
3. 可选的用户测试用例，支持 Markdown 表格、YAML、JSON、编号列表或自然语言描述。

如果缺少任一项，在任何 MCP 调用前要求用户补齐缺失输入。不要要求用户把 Token、密码、Cookie 或数据库连接串粘贴到对话中。

用户可以使用以下形式：

```text
请根据以下 PRD，在测试环境 staging-oms 执行全场景接口测试：
<PRD 文档>
```

也可以同时提供已有测试用例：

```text
测试环境：staging-oms

PRD：
<PRD 文档>

已有测试用例：
<测试用例表格、YAML、JSON 或编号列表>
```

用户测试用例是高优先级基线。必须保留用户用例 ID、步骤、输入/前置条件和预期结果；插件可以补充接口和数据库断言，但不得不静默改写用户意图。

## 2. 执行总则

- 先预检 MCP 能力和环境映射，再设计测试计划，再执行。
- 先读取接口契约和数据库结构，再构造请求或 SQL 意图。
- 只读/校验场景可自动执行。
- API 新增、修改、取消、删除，以及数据库 INSERT、UPDATE、DELETE 或清理动作，必须逐条展示影响范围并获得本次确认。
- `GET` 不自动等于只读；按接口契约和业务语义判断副作用。
- 写入场景不自动重试；只读网络失败最多重试一次，并在报告中标注。
- 环境、前置数据、工具能力或清理边界不明确时，标记 `BLOCKED` 并停止相关用例。
- 只保存脱敏摘要，不保存 Token、Cookie、密码、预约码、完整响应体或完整 SQL 参数。

## 3. MCP 预检与动态工具发现

在调用任何具体工具前，读取当前会话实际暴露的 MCP 工具目录或工具描述。不要假设记忆中的工具一定存在，也不要凭空发明工具名。

### Apifox 能力

匹配以下能力：

- 分页列出 OpenAPI 接口。
- 获取单个接口的完整契约。
- 使用指定环境执行接口。

已知的优先别名是 `listOpenApiEndpoints`、`getOpenApiDetails` 和 `executeOpenApi`，但必须以当前实际工具 schema 为准。详细流程见 [apifox-mcp-workflow.md](references/apifox-mcp-workflow.md)。

### dbhub 能力

匹配以下能力：

- 查看目标数据源的 schema、表和字段。
- 执行只读查询。
- 准备或写入测试夹具。
- 查询 API 执行后的数据库状态。
- 在获准后清理测试数据。

如果 dbhub 没有结构查询或结果校验能力，不得声称已完成数据库一致性测试；相关用例标记 `BLOCKED`。详细流程见 [dbhub-mcp-workflow.md](references/dbhub-mcp-workflow.md)。

### 环境一致性

将用户给出的环境标识分别提交给 Apifox 和 dbhub 的环境解析步骤，并核对返回的目标摘要。只比较非敏感的环境别名、项目/数据源显示名或安全标识；不把凭据写入报告。

若两个 MCP 无法解析到同一测试环境，先停止执行并要求用户提供映射关系。不能默认把 `staging`、`test`、`local` 等相近名称视为同一环境。

## 4. 测试计划生成

先生成 `test-plan.yaml` 的可审阅计划，至少包含：

- 需求编号和原文摘要。
- 业务流程和状态变化。
- 候选接口的 method/path。
- API 前置条件和数据库前置数据。
- API 断言和数据库断言。
- 风险、是否需要确认、清理策略。
- 计划、执行、通过、失败、阻塞、跳过或错误状态。
- 可选用户用例的来源、原始用例 ID、期望结果和冲突信息。

当用户明确提供测试用例时，按以下顺序生成集成测试计划：

1. 解析 PRD 的需求、角色、业务流程、状态和规则。
2. 解析并归一化用户测试用例，保留其原始意图摘要。
3. 匹配 Apifox 接口契约和 dbhub schema/前置数据。
4. 为用户用例补充可验证的 API/DB 断言、风险和清理策略。
5. 生成 PRD 覆盖缺口，并与用户用例进行语义去重。
6. 审阅冲突后再进入执行门禁。

每条标准化用例增加 `source`：

- `provided`：用户提供且未做语义补充；
- `generated`：由 PRD、接口契约或数据库分析生成；
- `merged`：与用户用例语义相同，保留用户基线并补充 API/DB 断言；
- `conflict`：与 PRD、接口契约、数据库事实或另一用户用例存在未解决冲突。

语义指纹使用：需求引用 + method/path + 场景分类 + 关键输入条件 + 核心预期结果。只有能证明语义相同才合并；文本相似但语义不确定时保留为独立用例。

以下情况必须标记为 `conflict`，不得自动执行：用户预期与 PRD 规则或接口契约冲突、method/path 不匹配、预期状态/错误码/字段不匹配、dbhub 无法证实要求的数据状态，或清理范围不可安全确定。冲突必须记录来源、脱敏差异、影响范围和待确认问题；未解决冲突的执行状态为 `BLOCKED`。

至少覆盖以下场景类型：

- 主流程和业务分支。
- 必填/非必填、类型、长度、格式和边界。
- 缺失、非法、重复请求和幂等。
- 状态机正向、逆向、乱序和重复事件。
- 权限、角色、租户和资源隔离。
- 分页、排序、筛选、空结果和大结果集。
- 数据一致性、事务、关联记录和计数变化。
- 超时、依赖失败、错误响应和兼容字段。
- 数据清理、残留检查和重复执行影响。

如果 PRD 没有覆盖某类场景，仍要在计划中列出该类别并说明不适用的依据。具体规则见 [test-design-rules.md](references/test-design-rules.md)。

## 5. Apifox 接口发现和执行

按照以下顺序执行：

1. 分页列出接口，不一次性复制全部结果。
2. 用 PRD 业务词、模块、路径、HTTP method 和标签筛选候选接口。
3. 对候选接口获取完整契约，核对 path 参数、query、header、body、认证要求、响应结构和示例。
4. 生成脱敏请求模板，并区分只读和副作用。
5. 只读用例直接执行；副作用用例先展示确认卡片，再逐条执行获准项。
6. 对响应只保留状态码、业务码、字段存在性、数量和经过脱敏的短摘要。
7. 在调用后立即执行对应 dbhub 校验，不用另一接口的成功响应替代数据库证据。

执行细节见 [apifox-mcp-workflow.md](references/apifox-mcp-workflow.md)。

## 6. dbhub 数据准备和校验

执行数据库动作前：

1. 先只读查看 schema 和相关字段。
2. 生成有唯一测试标识的 fixture 计划。
3. 在 API 调用前读取必要的状态快照。
4. API 调用后使用针对性条件核验状态、关联关系、计数、幂等或事务结果。
5. 写入和清理逐条确认；无法安全界定影响行时标记 `BLOCKED`。

数据库证据使用逻辑条件和聚合结果，不保存原始敏感参数。详细规则见 [dbhub-mcp-workflow.md](references/dbhub-mcp-workflow.md) 和 [safety-and-data-policy.md](references/safety-and-data-policy.md)。

## 7. 确认门禁

每个需要确认的用例单独展示：

- 用例 ID、接口 method/path。
- 将要发送或修改的业务范围。
- 将查询、创建、修改或删除的数据库对象类别。
- 预期副作用和清理策略。
- 不执行该用例的风险。

用户确认只授权当前用例，不延伸到其他用例。用户拒绝时将该用例记为 `SKIPPED`，并继续执行独立的只读用例；如果继续执行会依赖被拒绝的数据，则标记为 `BLOCKED`。

## 8. 结果和报告

为每条用例记录：

- API 结果：状态码、响应结构、业务码和脱敏摘要。
- DB 结果：调用前快照、调用后条件、状态/关联/计数核验。
- 清理结果：完成、未执行、失败或遗留风险。
- 工具错误、重试和用户确认记录。
- 用户用例 ID、标准化用例 ID、来源类型和需求追踪关系。
- `provided`、`generated`、`merged`、`conflict` 来源统计。
- 冲突和待用户确认的问题。

状态定义：

- `PASS`：API 和适用的 DB 断言全部通过。
- `FAIL`：出现可复现的业务断言失败、数据不一致或状态机异常。
- `BLOCKED`：MCP、环境、前置数据、权限或安全边界不满足。
- `SKIPPED`：用户拒绝确认或范围被明确排除。
- `ERROR`：工具、网络或数据库异常导致无法判断结果。

输出以下文件：

- `test-report.md`：标准测试报告。
- `test-plan.yaml`：可追踪测试计划。
- `execution-summary.json`：不含敏感值的机器可读汇总。

总体结论仅在全部范围内的可执行用例通过、无阻塞/错误且清理完成时为 `PASS`。否则为 `PARTIAL` 或 `FAIL`，并明确缺少的证据。

## 9. 使用本地辅助脚本

MCP 执行结束后，可使用插件自带脚本：

```bash
python3 scripts/validate_test_plan.py test-plan.yaml
python3 scripts/redact_evidence.py raw-evidence.txt --output redacted-evidence.txt
python3 scripts/render_report.py execution-summary.json --output test-report.md
```

脚本只读写本地文件，不连接 Apifox、dbhub 或任何外部网络。
