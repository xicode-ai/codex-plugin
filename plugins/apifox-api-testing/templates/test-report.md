# 接口测试报告：{{task_name}}

> 总体结论：**{{conclusion_status}}** ｜ 报告版本：{{report_version}}

## 0. 文档控制

| 项 | 值 |
| --- | --- |
| 报告版本 | {{report_version}} |
| 生成时间 | {{execution_time}} |
| 生成方 | {{generated_by}} |
| 评审状态 | {{review_status}} |
| 分发范围 | {{distribution_scope}} |

## 1. 执行摘要

{{executive_summary}}

关键指标：

| 指标 | 数值 |
| --- | ---: |
| 通过率 | {{pass_rate}} |
| 总用例 | {{total}} |
| 通过 / 失败 | {{passed}} / {{failed}} |
| 阻塞 / 跳过 / 错误 | {{blocked}} / {{skipped}} / {{errors}} |
| 断言通过总数 | {{assertions_passed_total}} |
| 断言失败总数 | {{assertions_failed_total}} |
| 自动重试次数 | {{retries}} |
| 执行时长 | {{execution_duration}} |

### 1.1 输入来源

- PRD：{{prd_input_summary}}
- 用户测试用例数：{{provided_case_count}}
- 自动生成用例数：{{generated_case_count}}
- 合并用例数：{{merged_case_count}}
- 冲突用例数：{{conflict_case_count}}
- 来源统计：
{{source_counts}}

## 2. 测试基本信息

| 项 | 值 |
| --- | --- |
| 测试任务 | {{task_name}} |
| PRD 标识/版本 | {{prd_id}} |
| 业务项目 | {{project_name}} |
| 测试环境 | {{environment}} |
| 执行时间 | {{execution_time}} |
| 执行时长 | {{execution_duration}} |
| Apifox 项目 ID | {{apifox_project_id}} |
| 执行分支 | {{apifox_branch}} |
| Apifox CLI 预检 | {{apifox_preflight}} |
| dbhub MCP 预检 | {{dbhub_preflight}} |

## 3. 工具链版本

| 工具 | 版本/说明 |
| --- | --- |
| Apifox CLI | {{apifox_cli_version}} |
| 本插件 | {{plugin_version}} |
| 测试规则版本 | {{rules_version}} |
| 工具调用摘要 | {{tool_summary}} |

## 4. 需求追踪矩阵

需求覆盖率：{{requirement_coverage}}

| 需求编号 | 业务目标 | 覆盖用例 | 覆盖状态 |
| --- | --- | --- | --- |
{{requirements_table}}

## 5. 用例执行明细

{{case_details}}

### 5.1 用户用例追踪

| 用户用例 ID | 标准用例 ID | 来源 | 需求引用 | 接口 | 状态 |
| --- | --- | --- | --- | --- | --- |
{{user_case_traceability}}

## 6. 缺陷与风险

{{defects}}

## 7. 阻塞、冲突与跳过项

### 7.1 阻塞项

{{blockers}}

### 7.2 冲突与待确认项

{{conflicts}}

## 8. 数据清理与环境影响

{{cleanup}}

## 9. 测试结论与建议

总体结论：**{{conclusion_status}}**

结论说明：{{conclusion_reason}}

{{recommendations}}

## 10. 附录

- 接口索引：{{endpoint_index}}
- 数据库对象索引：{{database_index}}
- Apifox CLI 报告产物：
{{apifox_report_files}}
- 状态定义：`PASS` API 与适用的 DB 断言全部通过；`FAIL` 可复现的业务断言失败、数据不一致或状态机异常；`BLOCKED` 工具、环境、前置数据、权限或安全边界不满足；`SKIPPED` 用户拒绝确认或范围被排除；`ERROR` 工具、网络或数据库异常导致无法判断。

> 本报告只反映本次指定环境中的实际执行证据，不代表生产环境、发布就绪或完整质量门禁已经通过。
