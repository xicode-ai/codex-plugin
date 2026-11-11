# 复核并回复 Intent 评审

独立插件 `outline-intent-revise`，支持仓库现有的 Codex 与 Claude 插件包装。

调用：

```text
使用 $outline-intent-revise 复核我提供的 Outline intent 评论，先输出改进计划和回复草稿供我授权。
```

先提供目标 Outline 文档或目录链接。运行时检查 `OUTLINE_URL` / `OUTLINE_API_TOKEN` 或显式配置文件；未配置时提示安全输入，不输出密钥。产品 PRD 对纯技术任务可不适用，详细代码评审需要可访问代码目录。

入口：[SKILL.md](skills/outline-intent-revise/SKILL.md)。模板、流程契约与 Python 标准库运行脚本随包提供，不依赖其他插件安装或作者机器路径。

```bash
python3 <skill-dir>/scripts/outline_intent.py --help
```

创建本插件不代表已连接或修改真实 Outline。复核先给计划与回复草稿，用户授权后才改任务/正文及回复；成功后把原评论、已有回复及真实发布的新回复同步回同一本地PRD/intent，保留黄色原句、作者/时间/状态和完整线程。只有回复、不改正文的场景也必须提供本地映射；同步与初次评论依据该次明确请求。脚本 digest 绑定计划，不能替代真实用户授权。

维护者修改共享资源后执行 `python3 tools/bundle_outline_intent.py`，再运行 `--check` 及 `python3 -m unittest discover -s tests -p 'test_outline_intent*.py'`。

## 本地和 Outline 如何保持一致

本地文档包含正文和受管理的评论展示层。再次上传会校验并剥离这层展示，只发送干净正文，避免把原生评论重复粘进正文。手写批注保留；托管评论被改过、正文已漂移或锚点不唯一时明确处理，不猜位置或静默丢内容。

评论回写是完成条件：只有远端回复回读成功、最终完整快照及本地文档/元数据都验证后才 complete。超时、断页或本地写失败保持待完成状态，恢复不会重复回复。该机制对齐本次结束快照，不是实时订阅。

映射字段、预览、大小限制和恢复命令见[运行用法](skills/outline-intent-revise/references/runtime-usage.md)。
