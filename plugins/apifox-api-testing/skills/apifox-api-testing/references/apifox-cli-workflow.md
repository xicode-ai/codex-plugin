# Apifox CLI 工作流

## 预检与登录

按顺序执行，任何一步失败先修复再继续：

1. `apifox --version`：确认 CLI 已安装。未安装时征得用户同意后 `npm install -g apifox-cli`。
2. 检查环境变量 `APIFOX_TOKEN`（Apifox 账号 API 访问令牌，`APS-` 开头）。未配置时停止并提醒用户自行配置，禁止用户在对话中粘贴令牌，禁止把令牌写入日志或报告。
3. `apifox login --with-token "$APIFOX_TOKEN"` 登录，`apifox whoami` 验证身份。
4. `apifox project list` 定位项目，后续命令统一带 `--project <projectId>`。

业务系统的登录 Token（JWT、Cookie 等）不属于 `APIFOX_TOKEN`；它们配置在 Apifox 项目的环境变量或用例参数中，由 Apifox 执行时注入，不经过本 Skill。

## 接口发现

1. `apifox endpoint list --project <projectId>` 列出接口，支持过滤参数：`--method`（HTTP 方法）、`--path-contains`（路径包含）、`--name-contains`（名称包含）、`--tag`（标签）、`--folder-id`（目录）、`--page`/`--page-size`（分页，不传默认全量）。每页只保留接口 ID、method、path、标题、标签等索引字段。
2. 用 PRD 业务词、模块、路径、HTTP method 和标签组合过滤候选接口。
3. 当候选过多时分批处理，不把完整接口目录复制到报告。

## 契约读取

对每个候选接口执行 `apifox endpoint get <endpointId> --project <projectId>`，读取：

- path 参数、query 参数、header、body schema 和必填规则；
- 认证方式和环境依赖；
- 成功/失败响应结构、业务码和状态转换；
- 示例值、枚举、分页和幂等字段。

如果契约和 PRD 的业务语义冲突，先记录冲突并阻塞依赖该语义的用例，不自行修改接口契约。

## 环境解析

1. `apifox environment list --project <projectId>` 列出项目环境，按用户给出的环境标识精确匹配显示名或 ID。
2. 不把 `staging`、`test`、`local` 等相近名称默认视为同一环境。
3. 将选定的环境 ID 用于 `apifox run -e <environmentId>`，并与 dbhub 侧解析的环境做一致性核对。

## 测试用例写入

写入 Apifox 项目属于写操作，默认在 AI 分支上进行（见「分支隔离」）。

1. `apifox cli-schema get test-case-create` 获取用例 JSON 的真实结构。
2. 按契约和测试计划在本地生成用例 JSON：请求参数从契约填充，业务数据来自测试计划和 dbhub 数据计划；断言使用 `responseJson` 上的状态码/业务码/字段条件。
3. `apifox cli-schema validate test-case-create --file ./case.json` 本地校验，通过后再执行 `apifox test-case create --project <projectId> --file ./case.json`。
4. 失败的校验不允许重试写入；先修正本地文件再重新校验。
5. 记录用例 ID 与标准化用例 ID 的映射，写入 `test-plan.yaml`。

## 测试场景编排

1. `apifox cli-schema get test-scenario-update` 获取场景结构。
2. 业务流用例链按执行顺序串联多个 `caseId`（例如 创建 → 支付 → 查询）；单接口用例包装为单步骤场景，或归入同一场景目录后用 `apifox run -f <场景目录ID>` 批量执行。
3. `apifox cli-schema validate test-scenario-update --file ./scenario.json` 校验后 `apifox test-scenario create --project <projectId> --file ./scenario.json`。
4. 记录场景 ID 与业务流程的映射。

单接口测试用例没有独立的 run 命令；任何用例要执行都必须落到场景或场景目录。

## 执行

1. 执行命令：`apifox run -t <场景ID> -e <环境ID> -r cli,json,junit --out-dir ./apifox-reports --project <projectId>`。
2. 只读场景可自动执行；写入、状态变更类场景先过确认门禁，获准后逐条执行。
3. 写入、删除和状态变更不得自动重试，防止重复副作用；只读网络失败最多重试一次并标记 retry。
4. 可用参数：`-n` 循环次数、`--env-var` 覆盖变量、`--on-error` 错误策略、`--upload-report` 上传云端报告。按测试计划需要使用，不默认上传。

## 分支隔离

1. 写入前 `apifox branch create --type ai` 创建 AI 分支（建议命名 `ai/<日期>-from-<主分支>-<主题>`），场景与用例在该分支创建。
2. 执行与报告都在该分支上进行。
3. 仅当用户明确同意后 `apifox branch merge` 合并回主分支；用户拒绝时保留分支并记录，不自动合并、不自动删除。

## 报告证据

每次执行只记录：

- case ID、场景 ID、method、path、执行状态；
- HTTP 状态码和业务码；
- 必要响应字段是否存在、数量和状态摘要；
- 脱敏后的短错误摘要；
- 对应 dbhub 校验结果。

CLI 的 json/junit 报告文件保留原始产物；对话与汇总报告只引用脱敏摘要。不要记录完整响应体、认证头、Cookie、密码、预约码、原始 SQL 参数或未经脱敏的错误堆栈。
