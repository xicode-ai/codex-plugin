# Apifox API Testing

这是一个通用的 Codex 插件，用于根据 PRD 文档在指定环境设计并执行全场景接口测试。

## 前置条件

插件运行时需要：

- Apifox CLI：发现接口、读取接口契约、创建测试用例与测试场景、在指定环境执行并生成报告。插件会在预检时检查 `apifox` 命令是否可用（缺失时经用户同意执行 `npm install -g apifox-cli`），并检查环境变量 `APIFOX_TOKEN`（Apifox 账号的 API 访问令牌，`APS-` 开头）完成 `apifox login`。
- dbhub MCP：查询数据库、准备测试数据、校验 API 结果和清理测试数据。

插件不依赖 Apifox MCP；不携带或保存 Apifox Token、数据库账号密码、Cookie、固定项目 ID 或固定环境配置。

## 使用方式

调用插件时提供一份 PRD 和环境标识，例如：

```text
请根据以下 PRD 和已有测试用例，在测试环境 staging-oms 设计集成测试计划：
<PRD 文档>

已有测试用例：
<可选的 Markdown 表格、YAML、JSON、编号列表或自然语言测试用例>
```

插件会先校验 Apifox CLI（安装、`APIFOX_TOKEN` 登录）和 dbhub MCP 是否可用，并确认环境标识能够映射到同一个 Apifox/dbhub 测试环境。

## 安全规则

- 只读和校验场景可以自动执行。
- API 新增、修改、取消、删除，以及数据库写入和清理，必须逐条确认。
- 环境、前置数据或清理边界不明确时会标记为阻塞，不会猜测执行。
- 报告只保留脱敏摘要，不保存 Token、Cookie、密码、预约码、完整响应体或完整 SQL 参数。

## 输出

一次执行默认生成：

- `test-report.md`：标准测试报告。
- `test-plan.yaml`：需求、接口、数据和断言的可追踪测试计划。
- `execution-summary.json`：不包含敏感值的机器可读汇总。

`scripts/` 下的脚本只处理本地计划和报告，不直接连接 Apifox 或数据库。

用户提供的测试用例会作为高优先级基线，与 PRD、Apifox 契约和 dbhub 数据证据合并。相同语义的用例标记为 `merged` 并补充 API/DB 断言；语义冲突标记为 `conflict`，在确认前不会自动执行。

## 从 GitHub 安装

仓库内置 marketplace 配置。将仓库发布到 GitHub 后，可使用以下命令添加 marketplace 并安装插件：

```bash
codex plugin marketplace add https://github.com/xicode-ai/codex-plugin.git
codex plugin add apifox-api-testing --marketplace codex-plugin-local
```

安装后请新建 Codex 线程，使新的 Skill 配置生效。
