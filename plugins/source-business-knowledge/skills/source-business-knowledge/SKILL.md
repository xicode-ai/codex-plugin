---
name: source-business-knowledge
description: Generate business-only Markdown knowledge documents from one or multiple code repositories for later knowledge-base Q&A. Use when the user wants system, module, workflow and business-rule explanations in natural language for paragraph or sentence based ingestion. 源码只用于取证，知识正文不含代码、实现细节或源码引用；普通现状 PRD 分析使用独立的 source-business-prd 插件。
---

# 从源码还原业务知识

交付给产品经理、运营和业务问答使用的知识文档：系统做什么、谁在什么场景使用、流程怎样推进、规则和异常是什么。源码是调查材料，不能成为知识正文。source-business-knowledge 与原 source-business-prd 分别分发；本 Skill 自带所需资源，宿主按能力适配，不限定产品。

## 输出契约

- `knowledge/`：只有业务语言的 Markdown。使用一级标题、按业务问题组织的二级/三级小节、空行分开的说明段落；流程步骤和表格按需辅助。一个主题一份文件，不能把逐条源码摘要加上空行当成业务文档。
- `analysis/`：源码路径、类/方法、接口、字段、原始状态值、SQL、证据与场景 ID、commit/哈希、版本、产品假设、未解决问题、调查进度、图源和清单。证据通过 analysis.json 的文档映射关联，不写进 knowledge/，也不藏进知识正文的注释。
- 知识正文不含代码片段、代码围栏、反引号代码、源码链接、技术调用链或审计括注；实现状态翻译成经证据确认的业务名称。系统业务名称及业务编号可使用，不能把程序符号当成业务术语。图源只留 analysis/，正文以文字解释；有渲染图片时可附图。

## 执行

1. 明确系统、仓库、读者、分析范围和输出目录，默认中文。读取 [platform-adapters.md](references/platform-adapters.md)，发现宿主读取、检索、产物写出和执行能力，再读取仓库规则。记录快照但不在知识正文重复。数据库可选，不自动安装、建索引、启动业务或修改被分析系统。
2. 先读 [analysis-method.md](references/analysis-method.md) 和 [evidence-contract.md](references/evidence-contract.md)，以业务场景分批追踪入口、条件、状态、交接和结果。多仓库/数据库任务再读 [multi-repo-and-db.md](references/multi-repo-and-db.md)。接口声明、枚举、注释和方法名是线索，不能独自证明规则已执行、先后顺序、外部结果或闭环。
3. 将事实、数据观察、文档意图、假设和问题记录在 analysis.json，保留可定位的真实证据及反证。缺少证据的产品动机不当作知识答案；执行范围不完整时保留草稿。跨系统只看到一端就明确交接边界，不编造另一端的处理结果。
4. 阅读 [knowledge-document-guide.md](references/knowledge-document-guide.md)，先形成系统—模块—主题目录与待回答的业务问题，再综合已核实证据写有层次、有解释的段落。系统总览讲定位/范围/协作，模块讲职责/能力/上下游，主题讲场景/流程/规则/异常/结果；不要生成重复的平铺 PRD 或审计报告冒充知识文档。图表按 [document-and-diagram-guide.md](references/document-and-diagram-guide.md) 处理。
5. 按目标切分方式抽样：脱离标题和前文的规则段落能否说明业务对象、条件、结果和重要限制？整句模式的关键规则句也要能理解，但不为凑槽位把每句拉成长模板，不重复版本/来源。按产品读者视角检查结构、内容深度和业务真实性；未知部分用必要的业务边界说明，详细调查问题留 analysis/。
6. 有 Python 3.10+ 时运行本 Skill 自带脚本：

   ```bash
   python3 <skill-dir>/scripts/validate_analysis.py <output>/analysis/analysis.json --check-sources --docs-root <output>
   python3 <skill-dir>/scripts/export_knowledge.py <output>/analysis/analysis.json --docs-root <output> --check-sources
   ```

   无本地源码时省略 --check-sources 并披露；无执行能力时人工核对并标明脚本未执行。导出器要求约定范围 complete，检查私有映射、基础 Markdown 结构和明显代码/审计残留，不能证明业务语义、深度、完整覆盖或检索效果。成功 stdout 清单另存 analysis/knowledge-manifest.json，失败时不覆盖旧清单。

## 交付与更新

只将 knowledge/ 下通过检查的 Markdown 交给用户现有知识库，analysis/ 不入库。保留稳定知识 ID、快照和文件哈希用于后续更新，ID 仅存在于维护资料。恢复分析先核对源文件变更并复核受影响结论；旧知识被替换/撤回时列出相应 ID，不自动改 PG。保留用户已有文档，不以机械删除代码词代替重新核实业务。只报告实际范围、限制和验证，不把结构通过称作业务正确。
