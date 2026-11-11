# 运行脚本与计划格式

Python 3.10+，仅标准库。`<skill-dir>`、`<repo>`、`<staging>`和URL/ID均须替换为核实后的实际输入。staging放被评审任务之外；输出不能覆盖源稿。脚本不进行业务语义判断，digest也不证明用户授权。

## 配置与读取

```bash
python3 <skill-dir>/scripts/outline_intent.py preflight
python3 <skill-dir>/scripts/outline_intent.py preflight --url https://outline.example --prompt
python3 <skill-dir>/scripts/outline_intent.py validate --file <repo>/.trellis/tasks/TASK/prd.md
python3 <skill-dir>/scripts/outline_intent.py snapshot --target-url https://outline.example/doc/intent-URLID --out <staging>/snapshot.json
```

`--prompt`询问缺失URL并用getpass输入token，密钥不进参数或输出。默认只在内存使用，用户明确要求保存才加`--save-config`（文件0600、父目录0700）。没有`--token`参数。支持`OUTLINE_URL`/`OUTLINE_API_TOKEN`；或`--config`/`OUTLINE_CONFIG_FILE`，默认`~/.config/outline-intent/config.json`，字段`url`/`token`。更换origin不复用其他部署的凭据/能力。preflight通过不代表远端认证或写权限已验证。

链接支持urlId/UUID及fragment。计划使用snapshot返回的规范文档ID，创建目标保留用户完整collection/父文档链接。快照保存富文本和全部分页评论，不能用失败后的空数组代替。`validate --template`可用于用户明确选择的其他检查，但上传计划仍校验打包模板，不能以此绕过规范。

## 原始计划字段

| 字段 | 约定 |
|---|---|
| plan_id | 本次具体方案的稳定唯一标识 |
| purpose | sync / review / revise，分别限定同步正文、新评审评论、复核修订 |
| source_files | `[{"path":"仓库相对路径"}]`，包含判断依赖的代码、PRD和规划资料，逐一固定哈希 |
| local_changes | `[{"path":".trellis/tasks/TASK/design.md","content":"完整待写内容"}]`，仅明确规划文件 |
| alignment | 有本地修改时，逐类记录人工语义核对及实际文件，见下文 |
| remote_actions | 正文同步在前、评论/回复在后，只有列明动作才可执行 |
| capabilities | 更新正文需`last_revision:true`及evidence，还必须有独立部署确认 |

alignment形状示例，填写本次真实证据，不代表已经完成核对：

```json
{
  "prd":{"status":"aligned","paths":[".trellis/tasks/TASK/prd.md"],"evidence":"具体需求/AC核对说明"},
  "design":{"status":"aligned","paths":[".trellis/tasks/TASK/design.md"],"evidence":"具体边界/契约核对说明"},
  "implement":{"status":"aligned","paths":[".trellis/tasks/TASK/implement.md"],"evidence":"切片/测试/验证核对说明"},
  "artifacts":[".trellis/tasks/TASK/prd.md",".trellis/tasks/TASK/design.md",".trellis/tasks/TASK/implement.md"]
}
```

aligned必须给可读paths，即使不改也纳入守卫。确属轻量任务无独立设计/实施稿，可用not_applicable并说明真实原因。不能把复杂任务称作轻量来省略对齐；声明和哈希不能证明语义正确，须读全文和diff。

- `document.create`：destination_url、title、text、source_path。仅sync创建到用户提供的目录；prepare解析实际归属。
- `document.update`：document_id、text、source_path、full_replacement_acknowledged:true。内置写入器采用完整Markdown替换，须展示富文本/锚点风险后取得授权；优先使用已验证的安全局部更新连接器。原评论内容/状态/锚点变化会阻止成功结论，不把线程仍在当作锚点保留。
- `comment.create`：document_id及text或ProseMirror data；可选anchorText/anchorPrefix/anchorSuffix，只有服务支持且能回读确认时使用，正文仍写章节/原句。
- `comment.reply`：另需从完整原快照取得的parent_comment_id，不能按文本猜父评论。review不能改正文或解决线程。

每个正文动作的source_path指向本次可读源稿或local_changes暂存稿。默认比较源稿和在线稿，允许首H1移入标题和常见Markdown排版差异；不能让两份不同但分别合格的intent悄悄同步。确需转换本地引用、内联附件或调整批注呈现，加`presentation:{"transformations":["逐项具体转换"],"evidence":"人工核对语义/AC不变的依据"}`。prepare固定源稿/在线稿双份哈希；这只记录经过审阅的呈现转换，不授权业务含义变化。

## 部署确认与授权执行

内置写入器默认不假设服务支持lastRevision；计划自填true不能开启更新。操作者核实实际版本或完成已授权隔离验证后，在本机配置记录`verified_capabilities:{"last_revision":true,"evidence":"真实部署版本或验证记录引用"}`，默认绑定配置url，可用verified_capabilities_origin明确绑定。执行再次核对origin和能力记录。此为操作者确认，不是脚本自动证明服务端实现；不得为绕门禁臆填true，也不向生产intent发测试写入探测。未知时更新阻塞，校验/读取/评论仍可进行。

```bash
python3 <skill-dir>/scripts/outline_intent.py prepare --plan <staging>/raw-plan.json --repo-root <repo> --out <staging>/prepared-plan.json
python3 <skill-dir>/scripts/outline_intent.py apply --plan <staging>/prepared-plan.json --repo-root <repo> --approved-digest <prepared-digest> --journal <staging>/apply-journal.json
```

两条命令之间必须满足该操作的实际对话授权；复核流程必须等待用户批准具体草稿/计划。prepare固定源哈希、完整远端快照、origin/能力、动作UUID和digest，不改任务稿。apply在实际本地写入后和每个远端动作前重检来源；默认备份在`<repo>/.outline-intent-backups/<digest>/`。不改产品代码或任务生命周期；task.json仅可明确计划的元信息变更。

每步回读正文、目录和评论父子/锚点；重放也复核已完成动作，不能只信日志verified。日志不能把批准基线重设为新编辑；文件及可用POSIX目录fsync，但跨本地/远端仍非原子事务。结果不明时复用同一prepared plan和journal只按固定ID回读，不自动重发；无法确认则停在未知阶段供人工核对。不自动删除仍活动的锁、不远端删除/回滚，不把规划修正说成代码或生产已修复。

离线测试不能代替实际Outline版本、权限、代理、Markdown规范化和原生锚点兼容性验证。

## revise 的本地评论回写（0.2）

revise 成功必须把原评论、已有回复和实际发布的新回复写回对应本地 intent。原句可安全唯一定位时用黄色背景，线程紧邻展示；无法定位时明确列在现有 References 下。作者、时间、状态和父子关系来自完整快照，草稿不得作为已发布回复。

`comment_mirrors` 与其他字段一起进入 prepare 的 digest。例如：

```json
{
  "comment_mirrors": [{
    "document_id": "实际文档ID",
    "path": ".trellis/tasks/TASK/prd.md",
    "metadata_path": ".trellis/tasks/TASK/research/outline-comments/document.json",
    "placements": {"实际根评论ID": {"quote": "正文中唯一的原始Markdown原句"}}
  }]
}
```

每份参与 revise 的文档必须恰好对应一个本地 intent。正文 update 默认使用 source_path；只有回复、不改正文时必须显式提供映射。省略 metadata_path 时 prepare 在该文档所在目录的 research/outline-comments 下确定文件名并冻结，不在 apply 临时猜路径。可选 max_bytes 限制最终 UTF-8 字节数，按实际文档限制填写；不设值表示不增加此项上限，不会自动截断内容。

prepare 校验现有托管展示并生成实际线程 preview；draft_preview 明确标记未发布草稿，仅供审阅。评论只读改写也核对本地与远端正文，不同正文须先修正或声明经过审阅的 presentation 转换。正文、design、implement 等对齐要求继续有效。

apply 先保留已确认的原线程展示；远端动作及回读成功后，再以最终完整快照生成本地评论和元数据。读回并验证这两个输出后才 complete。再次上传仅剥离校验通过的托管展示层，手写正文和批注保留；托管层被人工修改会报冲突。

最终快照不完整、大小超限、本地写失败或来源变化时保留未完成状态。使用同一 prepared plan 和 journal 恢复，按固定ID回读，不能清理日志后盲目重发。此为本次结束快照对齐，不是实时订阅；跨本地文件和 Outline 也不是原子事务。
