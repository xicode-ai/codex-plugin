# Apifox API Testing Plugin 设计

状态：已通过分段评审，待实现计划

日期：2026-09-07

## 1. 目标与边界

创建一个通用 Codex 插件 `apifox-api-testing`，根据用户输入的 PRD 文档和测试环境标识，设计并执行覆盖业务全场景的接口测试。

插件运行时依赖用户已经在 Codex 中配置好的两个 MCP：

- Apifox MCP：发现接口、读取接口契约、执行接口。
- dbhub MCP：查询数据库结构和数据、准备测试数据、验证 API 执行后的数据库结果、按需清理测试数据。

插件不保存或携带 Apifox Token、数据库账号、密码、地址、Cookie 等凭据，不绑定固定 Apifox 项目、数据库或环境。环境标识作为已配置环境的别名使用；如果 Apifox MCP 与 dbhub MCP 无法解析为同一目标环境，则停止执行并要求用户提供映射，不猜测环境。

插件默认只自动执行只读和校验场景。API 新增、修改、取消、删除，以及数据库 INSERT、UPDATE、DELETE 或清理动作，均须在执行前展示影响范围并逐条获得本次确认。

## 2. 设计选型

采用 Skill 编排型方案。插件提供 Codex Skill、测试规则、用例和报告模板以及本地辅助脚本；运行时动态发现当前会话实际暴露的 Apifox MCP 和 dbhub MCP 工具。

不采用固定 MCP 适配器：不同环境可能暴露不同工具名称或参数契约，插件应先做工具发现和能力匹配，再优先使用以下已知 Apifox 工具名：

- `listOpenApiEndpoints`：分页发现接口。
- `getOpenApiDetails`：读取接口详情和契约。
- `executeOpenApi`：执行接口。

dbhub 工具同样采用能力发现，不在插件中硬编码某一供应商版本的工具名。插件要求能够完成结构查询、只读查询、数据准备、结果核验和清理能力；缺少某项能力时，相关用例必须标记为 `BLOCKED`。

## 3. 总体流程

```text
PRD + 环境标识
  ↓
MCP 与环境预检
  ├─ Apifox MCP 可用性和环境解析
  ├─ dbhub MCP 可用性和数据源解析
  └─ 凭据、敏感输出和目标范围检查
  ↓
需求与业务流程解析
  ↓
Apifox 接口发现与契约读取
  ├─ 分页列出接口
  ├─ 按业务词、路径、HTTP 方法筛选
  └─ 获取候选接口详情
  ↓
测试场景矩阵和数据计划
  ├─ 正常、边界、非法、权限、幂等、状态、分页、异常
  ├─ API 前置数据和数据库状态快照
  └─ API 后置断言、DB 断言和清理策略
  ↓
执行门禁
  ├─ 只读/校验场景自动执行
  └─ 有副作用场景逐条确认
  ↓
API 执行 + dbhub 验证
  ↓
脱敏证据、结论和标准测试报告
```

## 4. 插件结构

```text
plugins/apifox-api-testing/
├── .codex-plugin/
│   └── plugin.json
├── skills/
│   └── apifox-api-testing/
│       ├── SKILL.md
│       └── references/
│           ├── apifox-mcp-workflow.md
│           ├── dbhub-mcp-workflow.md
│           ├── test-design-rules.md
│           └── safety-and-data-policy.md
├── templates/
│   ├── test-case-matrix.md
│   ├── test-report.md
│   └── execution-summary.json
├── scripts/
│   ├── validate_test_plan.py
│   ├── redact_evidence.py
│   └── render_report.py
├── examples/
│   ├── prd-input.md
│   └── environment-input.yaml
├── README.md
└── CHANGELOG.md
```

职责边界如下：

- `SKILL.md` 定义用户输入、完整执行流程、工具发现、确认门禁和最终输出。
- `references/` 定义 Apifox/dbhub 工作流、测试设计规则和安全数据策略。
- `templates/` 固定测试计划和标准报告的结构。
- `scripts/` 只对本地测试计划/报告做校验、脱敏和渲染，不直接连接外部 MCP。
- `examples/` 只提供不含真实地址、账号和凭据的输入样例。
- `plugin.json` 只声明插件元信息和 Skill 路径，不声明未随插件提供的 `.mcp.json`。

## 5. 用户输入契约

最小输入为一份 PRD 文档和一个环境标识：

```text
请根据以下 PRD，在测试环境 staging-oms 执行全场景接口测试：
<PRD 文档>
```

环境标识由 Skill 同时提交给 Apifox MCP 和 dbhub MCP 的环境解析步骤。可选的接口关键词、项目范围或业务模块筛选从 PRD 中推导；只有在无法可靠推导时才要求用户补充，不要求用户输入凭据。

## 6. 测试用例和数据模型

每条用例包含以下字段：

```yaml
id: API-001
requirementRefs: [REQ-01]
businessFlow: 客户创建预约
category: normal | boundary | invalid | permission | idempotency | state | pagination | exception
endpoint:
  method: POST
  path: /example
preconditions:
  - 数据库中存在可用业务主体
dataPlan:
  setup: 查询或准备什么数据
  beforeSnapshot: 执行接口前需要核对什么
request:
  parameters: 脱敏后的请求参数模板
assertions:
  api:
    - status
    - response fields
    - business code
  database:
    - record existence
    - field/status transition
    - relationship or count
cleanup:
  strategy: none | rollback | delete-fixture | manual
risk: low | medium | high
requiresConfirmation: true | false
executionStatus: planned | passed | failed | blocked | skipped | error
evidence: 脱敏后的摘要
```

场景生成至少覆盖：

- 主流程和业务分支。
- 必填、非必填、类型、长度、格式和边界值。
- 缺失参数、非法参数、重复请求和幂等。
- 状态机正向、逆向、乱序和重复事件。
- 权限、租户、角色和资源隔离。
- 分页、排序、筛选和空结果。
- 数据一致性、事务、关联记录和计数变化。
- 超时、错误响应、依赖不可用和兼容字段。
- 测试数据清理和残留检查。

## 7. 数据与安全策略

1. 先通过 dbhub MCP 查询结构和数据分布，再决定测试数据方案。
2. 优先复用可识别的测试夹具；必须新增数据时使用唯一测试标识，避免污染业务数据。
3. API 调用前保存必要的数据库状态快照，调用后执行针对性查询验证。
4. `GET` 不自动视为只读；只要契约或业务语义可能产生副作用，就进入确认流程。
5. API 写操作和数据库写操作逐条确认，不使用一次确认覆盖不相关用例。
6. 写入场景默认不自动重试；只读场景最多允许一次明确标记的网络重试。
7. 无法安全准备前置数据、无法确认清理范围或环境映射不明确时，标记为 `BLOCKED`。
8. 报告只记录逻辑条件、统计结果和脱敏摘要，不记录明文凭据、完整 SQL 参数、Token、Cookie 或完整响应体。

## 8. 报告输出

每次执行输出：

- `test-report.md`：面向用户的标准测试报告。
- `test-plan.yaml`：需求、接口、数据和断言的可追踪测试计划。
- `execution-summary.json`：可供后续系统消费的统计结果，不含敏感值。

`test-report.md` 固定包含：

1. 测试基本信息：任务、PRD 标识/版本、环境、执行时间、MCP 预检结果。
2. 执行摘要：总用例、通过、失败、阻塞、跳过、错误，以及 API/DB 断言统计。
3. 需求追踪矩阵：需求、业务流程、覆盖用例和覆盖状态。
4. 用例执行明细：接口、前置数据、API 结果、DB 结果、断言、脱敏证据和清理结果。
5. 缺陷与风险：严重级别、复现条件、期望/实际、影响范围和后续动作。
6. 阻塞与未执行项：MCP、环境、数据、用户确认和清理方面的阻塞。
7. 数据清理与环境影响：创建、修改、删除/回滚和遗留风险。
8. 测试结论与建议：本次范围是否达标、未覆盖范围和下一步建议。
9. 附录：工具调用摘要、接口/数据库对象索引、规则和版本信息。

状态定义：

- `PASS`：API 断言和数据库断言全部通过。
- `FAIL`：出现业务断言失败、数据不一致或状态机异常。
- `BLOCKED`：前置条件、环境、MCP、数据或安全边界不满足。
- `SKIPPED`：用户未批准副作用操作，或该范围被明确排除。
- `ERROR`：MCP、网络、数据库连接或工具调用异常，无法判断业务结果。

只有全部范围内的可执行用例通过、无阻塞/错误且数据清理完成时，总体结论才为 `PASS`。否则输出 `PARTIAL` 或 `FAIL`，明确缺少的证据，不将局部执行结果表述为生产或发布就绪。

## 9. 验收标准

- 插件 manifest 合法，插件名称、目录名和 manifest 名称一致。
- 新线程中调用插件时，能够识别 PRD 和环境标识，并在执行前完成 Apifox MCP/dbhub MCP 能力预检。
- 能够分页发现 Apifox 接口、读取候选接口详情，并把接口映射到需求和测试场景。
- 能够生成包含 API 断言和 DB 断言的测试计划。
- 只读场景可自动执行；所有有副作用的 API/DB 操作都有逐条确认门禁。
- 无法安全执行的场景被标记为 `BLOCKED` 或 `SKIPPED`，不猜测、不静默跳过。
- 标准报告包含需求追踪、API 结果、数据库校验、失败/阻塞、清理和总体结论。
- 本地脚本能够校验计划结构并对报告证据做敏感信息脱敏。
- 插件自身不包含真实 MCP 凭据、环境凭据、固定项目 ID 或真实业务数据。

## 10. 非目标

- 不在插件内安装或配置 Apifox MCP、dbhub MCP。
- 不实现新的 Apifox/dbhub MCP 服务端或代理层。
- 不替代 CI 测试框架、性能压测工具或生产监控。
- 不自动执行没有明确环境、前置数据或清理边界的破坏性操作。
