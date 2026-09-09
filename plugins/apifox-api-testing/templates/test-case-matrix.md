# 测试用例矩阵

## 测试任务

- PRD：{{prd_id}}
- 环境：{{environment}}
- 计划版本：{{plan_version}}
- 生成时间：{{generated_at}}

## 用例矩阵

| 用例 ID | 来源 | 用户用例 ID | 需求引用 | 业务流程 | 分类 | 接口 | 前置条件 | 数据准备 | 期望结果 | API 断言 | DB 断言 | 风险 | 需确认 | 冲突/阻塞 | 执行状态 | 清理 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| API-001 | generated | - | REQ-001 | 示例业务流程 | normal | POST /example | 示例前置 | 示例 fixture | 状态成功 | 状态码/业务码 | 状态/关联记录 | low | no | - | planned | none |

## 用例详情模板

### {{case_id}} - {{business_flow}}

- 来源：{{source}}
- 用户用例 ID：{{user_case_id}}
- 原始意图摘要：{{original_intent}}
- 需求引用：{{requirement_refs}}
- 分类：{{category}}
- 接口：{{method}} {{path}}
- 前置条件：{{preconditions}}
- 数据准备：{{setup}}
- 调用前快照：{{before_snapshot}}
- API 断言：{{api_assertions}}
- DB 断言：{{database_assertions}}
- 期望结果：{{expected_result}}
- 风险：{{risk}}
- 是否需要确认：{{requires_confirmation}}
- 冲突/阻塞原因：{{conflict_reason}}
- 清理策略：{{cleanup_strategy}}
- 执行状态：{{execution_status}}
- 脱敏证据：{{evidence}}
