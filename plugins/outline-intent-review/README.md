# 评审 Outline Intent

独立插件 `outline-intent-review`，支持仓库现有的 Codex、Claude 与 ZCode 插件包装。

调用：

```text
使用 $outline-intent-review 评审我提供的 Outline intent，结合当前仓库并发表评论。
```

先提供目标 Outline 文档或目录链接。运行时检查 `OUTLINE_URL` / `OUTLINE_API_TOKEN` 或显式配置文件；未配置时提示安全输入，不输出密钥。产品 PRD 对纯技术任务可不适用，详细代码评审需要可访问代码目录。

入口：[SKILL.md](skills/outline-intent-review/SKILL.md)。模板、流程契约与 Python 标准库运行脚本随包提供，不依赖其他插件安装或作者机器路径。

```bash
python3 <skill-dir>/scripts/outline_intent.py --help
```

创建本插件不代表已连接或修改真实 Outline。复核先给计划与回复草稿，用户授权后才改任务/正文及回复；同步与初次评论依据该次明确请求。脚本 digest 绑定计划，不能替代真实用户授权。

维护者修改共享资源后执行 `python3 tools/bundle_outline_intent.py`，再运行 `--check` 及 `python3 -m unittest discover -s tests -p 'test_outline_intent*.py'`。

ZCode：在「设置 → 插件 → 创建 → 添加市场」选择本仓库目录，然后在个人市场安装并启用本插件；在 ZCode Agent 对话中使用 `$outline-intent-review` 调用。安装后如需获取仓库更新，请刷新市场并重新安装对应插件。
