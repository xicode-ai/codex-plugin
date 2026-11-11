# codex-plugin

## Outline Intent 插件

| 插件 | 用途 | 授权边界 |
|---|---|---|
| [outline-intent-sync](plugins/outline-intent-sync/README.md) | 模板及内容校验后同步本地 Trellis intent 到用户指定目录 | 明确同步请求覆盖指定目标 |
| [outline-intent-review](plugins/outline-intent-review/README.md) | 校验他人 intent，结合代码及可选产品 PRD 进行定位评审 | 明确评审评论请求覆盖该文档评论，不改作者正文 |
| [outline-intent-revise](plugins/outline-intent-revise/README.md) | 复核已有评论，授权后对齐 PRD/design/implement 并同步和回复 | 先计划与回复草稿，授权后执行 |

三个插件独立打包、共享同一可校验的模板与运行逻辑；缺 Outline 地址或 token 时提示补充，不向版本库保存凭据。配置、快照、模板、写入/恢复边界详见各 Skill。

共享源在 `tools/outline_intent/`；改动后执行 `python3 tools/bundle_outline_intent.py`，`--check` 检查打包漂移。离线验证：`python3 -m unittest discover -s tests -p 'test_outline_intent*.py'`；全量回归：`python3 -m unittest discover -s tests`。真实 Outline 部署的 API 兼容性和权限需独立验证。
