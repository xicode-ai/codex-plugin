# 源码驱动的业务知识文档

source-business-knowledge 是独立插件：分析一个或多个代码仓库、可选数据库及现有资料，生成供业务人员阅读和知识库问答使用的 Markdown。原 source-business-prd 保留现状 PRD 分析能力，本插件不依赖它运行。

## 正文只讲业务

知识正文回答“谁在什么场景做什么、有哪些条件、流程如何推进、失败怎样处理、结果是什么”。每份文档有明确标题、二级/三级业务小节和空行分开的说明段落，按需要辅以步骤或表格，不能用逐接口摘要、源码调用链或同一长句模板代替解释。

knowledge/ 中不含代码、程序类/方法、接口路径、SQL、原始状态常量、源码链接、行号、commit、证据 ID 或每段审计括注。所有源码证据、来源快照、结论映射和维护 ID 保存在 analysis/，不导入业务知识库。业务术语和系统名可以正常使用，状态需解释为已确认的业务名称。

这是对 0.1.0 写法的纠正：原规范要求公开源码引用，会污染业务知识正文；0.2.0 删除这一要求并增加正文残留与基础结构检查。旧版生成的文件应重新核实并编写，不能只删除引用后当作业务质量已经合格。

## 使用

在宿主加载 Skill 后发送：

```text
使用 source-business-knowledge 分析以下系统：
系统：订单履约
仓库：/workspace/oms、/workspace/wms、/workspace/bms
读者：产品经理、运营和业务问答用户
范围：审核、下发、仓库履约、取消及结算，包含异常和人工处理
数据库：仅使用已经明确授权的数据源，没有连接时只做静态分析
输出：/workspace/business-docs
知识库只按段落或整句切分 Markdown，不自动补父级上下文。
请按系统总览、模块说明、业务主题与必要的跨系统场景组织文档。
知识正文只讲业务，使用业务小节和解释段落，不含代码、技术细节或源码引用。
调查证据和不确定问题单独保存在 analysis/，不得据接口声明或枚举推测完整闭环。
```

支持单仓库、多个仓库和没有数据库的任务。继续任务时指定输出目录中的 analysis/analysis.json，先检查源码变化再复核受影响知识。

## 输出与导入

```text
business-docs/
  README.md
  knowledge/
    system-overview.md
    glossary.md
    modules/<业务模块>/overview.md
    modules/<业务模块>/<业务主题>.md
    cross-system/<协作场景>.md
  analysis/
    analysis.json
    knowledge-manifest.json
    questions-and-conflicts.md
    diagrams/
```

只导入 knowledge/ 下的 Markdown；其余文件供调查、审计和维护，不入业务知识库。一个业务主题一份文件，段落保留对象、条件、结果及重要限制，整句切分时关键规则也可独立理解，不在每句重复系统背景和版本。图源留在 analysis/，正文以文字为主，可附业务表格或已渲染图片。

规范见 [业务文档写作指南](skills/source-business-knowledge/references/knowledge-document-guide.md)，风格示例见 [订单取消](skills/source-business-knowledge/examples/order-cancellation-knowledge.md)。示例是虚构场景，不可当作真实业务规则。

## 通用宿主接入

宿主按能力适配，不限于任何几个产品。可移植单元是完整 skills/source-business-knowledge/ 目录，包含指令、参考资料和脚本；支持 Agent Skills 的宿主原生加载，其他可读取指令/文件的宿主可显式读取 SKILL.md。代码语义引擎、数据库和执行能力缺失时按实际情况降级并说明。

Codex 市场条目、Claude Code 清单和 Antigravity 加载路径只是分发示例，详见 [通用适配协议](skills/source-business-knowledge/references/platform-adapters.md)。生成插件不隐含安装或修改宿主配置。

## 验证与维护

附带脚本使用 Python 3.10+ 标准库：

```bash
python3 <skill-dir>/scripts/validate_analysis.py <output>/analysis/analysis.json --check-sources --docs-root <output>
python3 <skill-dir>/scripts/export_knowledge.py <output>/analysis/analysis.json --docs-root <output> --check-sources
```

基础脚本检查分析结构、私有引用及可选源码快照；导出器另检查文档层次、映射、基本小节/说明段落及明显代码/审计残留。成功 stdout 清单另存 analysis/knowledge-manifest.json。它们不能证明业务事实、内容深度或知识库检索效果；交付还需按业务读者视角和实际片段抽查。无脚本能力时人工核对并明确未执行自动检查。

维护清单保留稳定 ID、哈希和来源版本；更新时确认替换与旧知识撤回范围。插件不连接 PG、不生成 embedding、不自动上传或删除知识库数据。实际验证与限制见 [评估记录](evals/results.md)。仓库根回归命令：python3 -m unittest discover -s tests。
