# Meeting Stone 日报与工时

插件 `meeting-stone-timesheet` 汇总当天 Claude、Codex、Zcode 会话生成日报，并在用户提供本次会话 Cookie 和逐项工时后，按任务匹配结果调用 Meeting Stone API 写入工时。

调用示例：`使用 $meeting-stone-timesheet 生成今日日报并填写 Meeting Stone 工时。`

入口：[SKILL.md](skills/meeting-stone-timesheet/SKILL.md)。缺少 Cookie 时只输出日报；任务无法唯一匹配、重复检查失败或工时不明确时不会猜测写入。凭据只供本次运行使用，不保存到插件或文件。插件可使用已安装的 daily-work-log 技能，也包含无该技能时的日报回退流程。

通过本仓库市场安装并启用插件；仓库更新后需刷新市场并按需重新安装。安装插件不会自动触发 API 调用。真实 Meeting Stone 接口兼容性需要在用户授权的运行中验证。
