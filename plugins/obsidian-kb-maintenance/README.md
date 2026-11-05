# Obsidian 知识库维护

安装本仓库市场中的 `obsidian-kb-maintenance` 插件，在新会话请求“使用 obsidian-kb-maintenance 维护指定 Obsidian 知识库”。需要已启用的官方 `obsidian` CLI；IMA 收集还依赖已配置的 ima-skill 和可用的官方接口。目标库选择不明确时会询问。

维护规则见 `skills/obsidian-kb-maintenance/references/maintenance-policy.md`。`scripts/maintenance.py` 只提供 Markdown 清单与日期资格辅助判断，锁、备份、并发检查、失败恢复、链接及证据核验由执行 Skill 的代理按照规则处理，不是脚本的自动保障。

本插件不会自动创建定时任务。测试使用 `python3 -m unittest discover -s tests`。

## 笔记属性维护（0.2.0）

完整维护现在包括属性覆盖检查与增量补齐；也可以单独请求“检查并补齐可写笔记的属性”，该模式不额外收集 IMA、改写知识正文或生成阅读导读。字段与执行规则见 [笔记属性](skills/obsidian-kb-maintenance/references/note-properties.md)。

- 每份可写笔记维护有依据的标题和类型；标签、别名、领域、来源、状态与日期按笔记类别和实际证据维护，已有等价字段直接沿用。
- 保留已有 YAML、自定义属性、注释与取值；仅补缺失或有明确依据的字段，合规则跳过。旧笔记未知创建日期不补为今天，仅属性修改不刷新正文更新时间。
- 默认覆盖 Wiki、来源说明、收集记录、本轮新建导读与唯一维护状态；原文、同步镜像、个人原稿及受保护页面继续遵守现有边界。全库检查不代表有权改写所有笔记；缺口、冲突和未完成批次保存在已有状态中。
- 可用官方 Obsidian CLI 的 `properties`、`property:read`、`property:set` 读取和更新；每次写入仍需备份、哈希核对和写后验证，不声称 CLI 提供原子并发保护。

此能力由执行 Skill 的代理按规则完成，不是 Python 辅助脚本新增的自动批处理器。更新仓库插件不会自动批量修改既有笔记，也不会同步修改另一份已安装的 Skill。

## Dataview 动态目录（0.3.0）

支持在七个已确认的根目录（包含 05-Skills）维护动态笔记索引：保留既有手工导航，在专用标记区块中加入按子目录、最近文件改动、已有状态、来源完整性与 Wiki 属性缺口的 DQL 查询。文件或属性变化后由 Dataview 更新显示；每次维护只检查查询与导航，文本无变化则跳过写入。

通过官方 `obsidian vault="<库名>" eval` 调用 Dataview API 验证查询，不使用第三方 `obsidian-cli`；不要求启用 DataviewJS、不安装/启用插件或固定清缓存。插件不可用、查询失败和视觉渲染未验证分别报告。目录页不是知识证据，修改时间也不等于当天新增。

详见 [目录索引规则](skills/obsidian-kb-maintenance/references/dataview-indexes.md) 和 [七目录配置示例](skills/obsidian-kb-maintenance/references/dataview-indexes.example.json)。示例中的入口与旧 Outline 导出排除项必须对实际库核实；配置不授予写入权。只请求目录维护时，不额外启动 IMA 或正文整理。

```bash
python3 plugins/obsidian-kb-maintenance/scripts/dataview_indexes.py render /absolute/confirmed-indexes.json
python3 plugins/obsidian-kb-maintenance/scripts/dataview_indexes.py verify /absolute/confirmed-indexes.json --vault "库名"
```

辅助脚本只生成区块、校验并合并文本、执行作用域受限的查询；笔记写入、备份和并发检查由代理依规则完成。查询结果只返回计数与校验状态，不输出来源正文。单独测试：`python3 -m unittest discover -s tests -p test_dataview_indexes.py`。
