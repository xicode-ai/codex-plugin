---
name: flowx-config-docs
description: Use when a user requests FlowX data dictionary, menu permission, or internationalization configuration documentation from a specified database through dbhub.
---

# FlowX 配置文档

当用户提出 FlowX 数据字典、菜单权限、国际化、多语言配置或固定模板 Markdown 文档需求时使用本 Skill。它是独立插件能力，不修改 `apifox-api-testing`。

## 输入

输入必须包含目标数据库或 dbhub 数据源标识，以及配置需求或业务范围：

```text
数据库：oms_test
配置需求：预约单相关的数据字典、菜单权限、国际化
```

不要求也不接受语言选择参数。每次固定处理英文、日文、韩文、西班牙语和葡萄牙语五种语言。

数据库或配置需求缺失时，在调用 dbhub 前要求用户补齐。不要要求用户提供 Token、密码、Cookie、数据库连接串或其他凭据。

## 执行顺序

严格按以下顺序执行：

1. 解析数据库标识和配置范围。
2. 在执行具体 dbhub 调用前，读取当前 Codex 会话实际暴露的 MCP 工具目录，动态匹配目标数据源确认、Schema/表/字段发现和只读查询能力。不要凭空发明工具名，也不要假设记忆中的别名一定存在。
3. 使用 dbhub 只读确认目标数据库和非敏感数据源摘要。只保留数据库名、Schema 名、表名等非敏感标识，不记录账号或连接详情。
4. 先读取 Schema、表名和字段名，再查询配置记录。默认不导出整个数据库。
5. 根据表名、字段名、配置编码、字典 key/value、菜单父子关系、菜单名称、路由、权限字段、国际化一级/二级 key、语言字段和用户业务关键词匹配候选配置表。
6. 候选唯一时，只查询配置需求覆盖的记录；多个候选时列出表名、字段摘要和匹配原因，等待用户确认；没有候选时仍保留三张空模板，并注明未查询到匹配配置或无法识别配置表。
7. dbhub 不可用、目标库无法解析、无权限、Schema 无法读取或查询失败时，标记为查询受阻。不要把查询受阻写成空结果，也不要继续伪造配置记录。
8. 将结果转换为固定内部字段模型，再执行翻译和校验。

## 固定内部模型

数据字典字段顺序：

```text
operation, dict_code, dict_key, dict_value, en, ja, ko, es, pt
```

菜单权限字段顺序：

```text
level1, level2, button_or_button_menu, menu_code, route,
en, ja, ko, es, pt, sort, authorization, resource, operation_type
```

国际化字段顺序：

```text
operation_type, level1_key, level2_key, zh, en, ja, ko, es, pt
```

字段无法可靠映射时保持为空并增加字段映射说明；不要把相似但未经证实的字段静默填入错误列。缺少操作字段时保持为空，不推断新增、修改或删除动作。

## 翻译门禁

- 数据库已有非空翻译优先，禁止覆盖。
- 仅翻译自然语言字段缺失的值。
- 不翻译一级/二级 key、字典编码、字典 key/value、菜单编码、路由、排序、授权标识、授权资源或操作类型代码。
- 翻译前提取占位符，翻译后校验集合完全一致。
- 必须原样保护 `{unit}`、`${name}`、`{{count}}`、`:id` 等占位符，并保留 Markdown、HTML 和换行语义。
- 校验失败时不静默写入，写入“翻译待人工确认”说明。

## 生成和输出

始终生成三个章节，顺序固定为：数据字典、菜单权限、国际化。每个章节即使没有数据也保留完整表头和分隔线，并在表格前写入：

```text
> 未查询到匹配配置。
```

固定表头如下：

```text
操作 | 字典编码 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语
一级菜单 | 二级菜单 | 按钮菜单 or 按钮 | 菜单编码 | 路由 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 | 排序 | 授权标识 | 授权资源 | 操作类型
操作类型 | 一级key | 二级key | 中文 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语
```

将脱敏的 normalized payload 写入临时 JSON 后，先运行：

```bash
python3 scripts/validate_config_data.py payload.json
```

校验通过后运行：

```bash
python3 scripts/render_config_doc.py payload.json --output flowx-config-{数据库名}-{日期}.md
```

输出文件写入当前工作区，文件名使用当前本地日期。报告最终绝对路径，并区分 complete、empty、ambiguous、unresolved、blocked 和 translation-pending 状态。

本 Skill 不调用 INSERT、UPDATE、DELETE 或清理动作；本地脚本不调用 dbhub、不调用外部翻译服务。任何错误说明都不得包含密码、Token、Cookie、连接串、完整 SQL 参数或完整原始响应。
