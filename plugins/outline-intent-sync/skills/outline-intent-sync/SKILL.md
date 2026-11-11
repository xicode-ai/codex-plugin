---
name: outline-intent-sync
description: Use when the user asks to upload or synchronize a local Trellis prd.md or intent.md into a specified Outline directory. 适用于校验本地 intent 模板后上传、更新 Outline；不用于评审他人方案或复核评论。
---

# 同步 Trellis Intent 到 Outline

将经过模板和内容校验的本地 intent 发布到用户指定目录，保留文档归属及可复核版本。先读取 [通用契约](references/workflow-contract.md) 的配置、模板、写入与恢复部分。脚本位于本 Skill 的 `scripts/outline_intent.py`，Python 3.10+，先看 `--help`；使用已授权连接器时也执行等价校验。

## 输入与校验

脚本参数、计划JSON及部署能力确认见[运行用法](references/runtime-usage.md)。

1. 运行 `preflight` 检查 Outline URL/token。缺项请求用户安全输入，不显示已有密钥；无凭据不上传。确认源文件与用户给出的 Outline collection/父文档目录链接；缺目录链接就询问，不能从标题猜目录。
2. 对本地 PRD/intent 运行 `validate`，使用打包的 [intent 模板](assets/intent-template.md)。八节齐全不等于合格：空白字段、裸“需求1”及“Given ... When ... Then ...”均应补齐；不擅自虚构内容或为了通过改写产品意图。校验失败先列具体问题，停止上传。
3. 读取同任务 design/implement 和关键引用，核对术语、AC与范围。纯技术任务产品 PRD 可明确不适用。不上传设计与执行计划互相矛盾的稿件；模板通过不代表语义已核实。

## 准备、同步与回执

4. 准备具体计划：源文件/哈希、目标目录 ID/链接、创建或明确 ID 更新、标题与完整待同步正文。更新须读取现有版本和全部评论，保护作者修改、富文本/锚点；引用中关键设计要内联或改成读者可访问链接，不把本机绝对路径和仓库相对链接当在线证据。不得编造未提交文件的 commit 链接。
5. 用户已明确要求同步该文件到该目录，可在此范围执行计划；仅要求预览则保持预览。脚本 `prepare` 固定快照和 digest，`apply` 只执行已授权的同一计划；新增目标、替换范围或富文本损失风险需先展示变化。计划摘要/操作权限按实际对话记录，digest 不等于用户授权。
6. 写入前再校验本地与远端快照；冲突停下重整计划。按动作账本和 ID 写后回读，验证目录、标题和关键正文，记录远端 revision 与源哈希。创建超时先查询既定 ID，不盲重发；部分成功列出已完成和待恢复，不宣布全部同步。

输出实际创建/更新链接、模板与语义核对结果、版本/哈希、未完成项。不评论、解决评审线程，不启动 Trellis 实施、提交或部署。
