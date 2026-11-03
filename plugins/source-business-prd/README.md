# 源码驱动业务知识与现状 PRD

`source-business-prd` 让宿主智能体结合一个或多个代码仓库、可选数据库与已有资料，整理业务能力、规则、对象生命周期、跨系统交接及现状 PRD。输出面向产品理解，关键陈述保留证据；产品动机缺乏依据时显式标为假设。

宿主范围按能力定义，不限于 Codex、Claude Code、Antigravity。支持加载本 Skill 指令并访问分析资料的其他智能体，也可使用相同方法、证据契约与产物；具体可执行范围取决于宿主实际提供的工具。

它由一份可移植 Skill、参考方法与 Python 结构校验脚本组成，不是独立自动扫描引擎。需要宿主能够读取目标源码；Serena、已有 CodeGraph 索引和 dbhub 都是可选增强。生成完整性取决于实际范围、工具能力和可访问证据，不保证仅凭源码恢复最初立项动机或线上全貌。

## 使用

在宿主加载 Skill 后发送：

```text
使用 source-business-prd 分析以下系统：
系统：订单履约
仓库：/workspace/oms、/workspace/wms、/workspace/bms
读者：产品经理和新加入研发
范围：审核、下发、仓库履约、取消及结算，包含异常和人工处理
数据库：已有授权的 dbhub 连接别名 orders-readonly，test 环境；其他库暂不查询
输出：/workspace/business-docs
请区分实现事实、数据观察、文档意图与产品假设，输出业务知识、现状 PRD、必要图表和缺口清单。
```

也支持单仓库、没有数据库的任务。可用“继续 `<output>/analysis/analysis.json` 对应分析，先检查源码是否变化”恢复长任务。数据库环境或映射不清楚时只暂停依赖它的查询。

产物通常包括阅读首页、能力地图、领域现状 PRD、业务闭环、跨系统协作、对象关系、问题清单与 `analysis/analysis.json`；按规模合并或拆分。按表达需求使用流程、时序、状态、C4、ER、数据流或决策表，默认 Markdown/Mermaid 并提供可读文字。

## 通用接入与分发示例

可移植单元是完整的 `skills/source-business-prd/` 目录，包含入口、参考资料、示例与校验脚本。支持 Agent Skills 的宿主使用其原生加载机制；没有原生 Skill 注册机制、但能读取外部指令及相关文件的宿主，可显式加载该目录下的 `SKILL.md`，按链接读取所需资源。不要只复制入口而遗漏参考资料与脚本。

接入新宿主时，先发现资料读取、检索、产物写出和命令执行能力，按 [通用适配协议](skills/source-business-prd/references/platform-adapters.md) 映射工具。语义分析、数据库、Python 校验和图表渲染分别按能力启用；缺失项记录降级或未执行状态。核心 Skill 不要求某个产品专有的命令、目录、MCP 前缀或子代理机制。

以下只是已有包装或加载方式的示例，不构成支持宿主白名单：

- **Codex**：本仓库根 `marketplace.json` 已有条目，插件清单位于 `.codex-plugin/plugin.json`。安装与新会话发现后使用。
- **Claude Code**：可本地加载 `claude --plugin-dir /path/to/plugins/source-business-prd`，或将完整 `skills/source-business-prd/` 复制到项目 `.claude/skills/source-business-prd/`。
- **Antigravity**：将完整 `skills/source-business-prd/` 放到项目 `.agents/skills/source-business-prd/`，再确认宿主发现列表。

这份说明不自动安装、不更改宿主配置，不给插件内置数据库连接或 MCP 服务。平台细节及官方依据见 [platform-adapters.md](skills/source-business-prd/references/platform-adapters.md)。各宿主实际验证状态见 [评估记录](evals/results.md)，格式支持不代表端到端效果已经验证。

## 检查

Python 3.10+，仅标准库：

```bash
python3 <skill-dir>/scripts/validate_analysis.py <output>/analysis/analysis.json --check-sources --docs-root <output>
python3 <skill-dir>/scripts/validate_analysis.py <skill-dir>/examples/minimal-analysis.json
```

Python 用于执行附带的自动校验器。宿主无法执行脚本时，可以按契约人工核对并保留产物及命令，明确“脚本校验未执行”；无法完成必要取证时保留草稿状态。

无本地源码可省略 `--check-sources`；省略即未检查源文件快照。跨电脑或操作系统检查历史产物时，先将仓库 root 映射到本机实际路径。契约和检查边界见 [evidence-contract.md](skills/source-business-prd/references/evidence-contract.md)。`VALID_STRUCTURAL` 只表示所执行的结构、引用、哈希、行范围和基础文档检查通过，不验证业务语义、全系统覆盖、SQL 安全或生产运行行为。随附示例为未取证草稿，不能冒充系统结论。

在仓库根运行回归测试：`python3 -m unittest discover -s tests`。受控试分析方法见 [evals/README.md](evals/README.md)。
