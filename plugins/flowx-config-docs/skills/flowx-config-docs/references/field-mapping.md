# FlowX 字段映射规则

查询结果必须先归一化，再交给 Markdown 渲染器。渲染器只接受以下固定字段。

## 数据字典

```text
operation, dict_code, dict_key, dict_value, en, ja, ko, es, pt
```

常见语义映射：

- 字典类型、字典编码、type、code → `dict_code`；
- 字典 key、value key、option code → `dict_key`；
- 中文字典值、label、name → `dict_value`；
- 语言列或语言-值行中的对应文本 → `en`、`ja`、`ko`、`es`、`pt`。

`operation` 只接受用户需求或数据库明确提供的操作类型；没有可靠来源时留空。

## 菜单权限

```text
level1, level2, button_or_button_menu, menu_code, route,
en, ja, ko, es, pt, sort, authorization, resource, operation_type
```

父子关系优先使用 parent id、层级字段或可验证的菜单路径。仅凭名称相似不能推断层级。`authorization` 是菜单授权标识，不等于凭据字段，必须保持原值；只有 Bearer 或明显凭据模式才进入敏感值检查。

## 国际化

```text
operation_type, level1_key, level2_key, zh, en, ja, ko, es, pt
```

列式存储直接按语言列映射。行式存储按一级 key、二级 key 和语言代码聚合。例如：

```text
level1=inboundOrder, level2=pendingShelving, lang=zh, value=待上架{unit}
level1=inboundOrder, level2=pendingShelving, lang=en, value=Pending Putaway {unit}
```

应聚合为一个内部 row，而不是生成两个国际化行。相同 key 和语言出现多个不同值时，保留一个冲突说明并暂停自动覆盖。

## 不确定字段

字段名相似但语义不能证实时，保持目标字段为空，并把表名、字段名和不确定原因写入章节说明。未识别字段不进入最终 Markdown。
