# 标准接口测试报告

## 1. 测试基本信息

- 测试任务：{{task_name}}
- PRD 标识/版本：{{prd_id}}
- 测试环境：{{environment}}
- 执行时间：{{execution_time}}
- Apifox MCP 预检：{{apifox_preflight}}
- dbhub MCP 预检：{{dbhub_preflight}}

## 1.1 输入来源

- PRD：{{prd_input_summary}}
- 用户测试用例数：{{provided_case_count}}
- 自动生成用例数：{{generated_case_count}}
- 合并用例数：{{merged_case_count}}
- 冲突用例数：{{conflict_case_count}}
- 来源统计：
{{source_counts}}

## 2. 执行摘要

| 指标 | 数量 |
| --- | ---: |
| 总用例 | {{total}} |
| 通过 | {{passed}} |
| 失败 | {{failed}} |
| 阻塞 | {{blocked}} |
| 跳过 | {{skipped}} |
| 错误 | {{errors}} |
| API 断言通过 | {{api_assertions_passed}} |
| API 断言失败 | {{api_assertions_failed}} |
| DB 断言通过 | {{db_assertions_passed}} |
| DB 断言失败 | {{db_assertions_failed}} |

总体结论：**{{conclusion_status}}**

结论说明：{{conclusion_reason}}

## 3. 需求追踪矩阵

| 需求编号 | 业务目标 | 覆盖用例 | 覆盖状态 |
| --- | --- | --- | --- |
{{requirements_table}}

## 4. 用例执行明细

{{case_details}}

## 4.1 用户用例追踪

| 用户用例 ID | 标准用例 ID | 来源 | 需求引用 | 接口 | 状态 |
| --- | --- | --- | --- | --- | --- |
{{user_case_traceability}}

## 5. 缺陷与风险

{{defects}}

## 6. 阻塞与未执行项

{{blockers}}

## 6.1 冲突与待确认项

{{conflicts}}

## 7. 数据清理与环境影响

{{cleanup}}

## 8. 测试结论与建议

{{recommendations}}

## 9. 附录

- 工具调用摘要：{{tool_summary}}
- 接口索引：{{endpoint_index}}
- 数据库对象索引：{{database_index}}
- 规则版本：{{rules_version}}

> 本报告只反映本次指定环境中的实际执行证据，不代表生产环境、发布就绪或完整质量门禁已经通过。
