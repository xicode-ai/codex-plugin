# Obsidian 知识库维护

安装本仓库市场中的 `obsidian-kb-maintenance` 插件，在新会话请求“使用 obsidian-kb-maintenance 维护指定 Obsidian 知识库”。需要已启用的官方 `obsidian` CLI；IMA 收集还依赖已配置的 ima-skill 和可用的官方接口。目标库选择不明确时会询问。

维护规则见 `skills/obsidian-kb-maintenance/references/maintenance-policy.md`。`scripts/maintenance.py` 只提供 Markdown 清单与日期资格辅助判断，锁、备份、并发检查、失败恢复、链接及证据核验由执行 Skill 的代理按照规则处理，不是脚本的自动保障。

本插件不会自动创建定时任务。测试使用 `python3 -m unittest discover -s tests`。
