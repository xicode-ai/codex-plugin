# Outline → Obsidian 企业知识库同步

独立插件 `outline-obsidian-sync`，同时提供 Codex / Claude 插件清单，在仓库 `marketplace.json` 注册。由 Skill 完成同步和调用现有 `obsidian-kb-maintenance` 两阶段流程；Python 脚本本身只做确定性同步，不会独立启动模型或伪造维护完成。

安装后可请求：

> 使用 outline-obsidian-sync，将我提供的 Outline 地址中可访问的完整目录增量同步到 `/绝对路径/笔记库/03-外部资源/企业知识库/`，下载图片，再调用 obsidian-kb-maintenance 维护整个库。

Token **只从执行进程的环境变量 `OUTLINE_TOKEN` 读取**，不接受手动输入或其他来源兜底。未设置、空串或纯空白时，脚本以配置错误（退出码 2）停止；技能提示配置执行环境，不要求用户在聊天里提供 Token。不要把值写进命令行、配置、Git 或笔记。普通同步无需安装 Python 包，要求 Python 3.10+。维护阶段需要已启用的官方 `obsidian` CLI 和现有维护 Skill；其 IMA 阶段使用原有独立配置。

请让实际运行技能/脚本的进程继承 `OUTLINE_TOKEN`；在另一个终端配置的环境变量，不会自动出现在已有的应用进程中。本插件不修改全局环境、不打印 Token，也不将它持久化。

分页默认 25 条，可用 `--page-size 10` 适配响应较慢的站点，最大 100。脚本遵循系统代理环境；如已确认目标站点可直连，可仅为当前命令设置 `NO_PROXY=<目标主机>`，无需修改系统代理。

API 支持 gzip 压缩，减少元数据列表传输。已有专用历史导出时，可加 `--reuse-export /absolute/old-export` 只读复用图片，避免重复下载。历史笔记须有 JSON 双引号的 `outline_id`、`updated_at` frontmatter；文档 ID、源版本、正文结构及图片位置均核对一致，且本地文件确为图片、未越界时才复用。不同版本或正文不符时继续下载，不覆盖旧导出。不要将整个 vault 作为历史导出目录。

```bash
python3 plugins/outline-obsidian-sync/skills/outline-obsidian-sync/scripts/sync_outline.py probe --url https://outline.example.com
python3 plugins/outline-obsidian-sync/skills/outline-obsidian-sync/scripts/sync_outline.py sync --url https://outline.example.com --target /absolute/vault/03-外部资源/企业知识库
python3 -m unittest discover -s tests -p 'test_outline_obsidian_sync.py'
```

`probe` 不落盘，只测试集合、首集合目录/正文及一个图片，不能代表全库附件完整。`sync` 输出抓取 JSON，0 成功、1 部分失败、2 配置/整体错误。每次仍需分页读取远端元数据；列表 API 可能连带返回正文，但未变条目不会再单独请求全文、重复下载或重写本地文件。

集合和文档层级保留；每个文档对应同名文件夹中的同名笔记，便于父文档承载正文和子文档。`_目录.md` 保留目录顺序。非法文件字符做可逆百分号转义，超长名称截短，同名按 ID 消歧；这些是文件系统适配，映射和原始标题留在状态中。

若源站目录接口漏项或返回空树，会用元数据中的父文档 ID 重建层级，并在 warnings 中记录。该部分的原始兄弟顺序无法取得时按创建顺序补入，不声称完全恢复原站排序；缺父节点或循环则留作失败项，不能擅自归到根目录。

图片与站内附件最多四路并发下载，落到 `_附件/` 并改写本地引用；跨域重定向不携带 Outline Token。下载 HTML/JSON 登录页会判失败；正文仍保存并保留失败资源的原引用，附件状态明确标为未完成，下次重试。支持常见行内 Markdown、引用式图片、HTML src/href；不执行 OCR，不保证所有扩展语法均已转换。过期的源站签名若不能通过正式 API 取得有效链接，会如实保留缺失，不绕过权限或伪造图片。

`.outline-sync/` 含状态、原始 Markdown 内容版本、更新前备份和进程锁。原始快照不覆盖，日常镜像视图可更新；只读原件可按状态中的 `original` 路径恢复。符号链接逃逸、同名非托管文件、本地编辑冲突均拒绝覆盖。比较哈希与写入不是对所有外部编辑器的原子事务；运行时避免并发编辑。进程被强杀可能留下锁，人工确认 PID 不再运行后再处理。不要提交运行目录和原文备份到插件仓库。

远端删除/失去权限不删除本地；重命名和移动创建新路径并保留旧副本，结果中列出旧路径。这样避免毁掉用户已有引用，但严格物理目录镜像可能留有历史路径，需要用户另行决定清理。历史路径、同步状态、快照和备份不重复进入维护证据。

全量首次抓取是基线，不等于当天新资料；维护时遵守既有日期、Wiki 写入和导读边界。同步成功与全库语义维护完成必须分别验收。详见 [SKILL.md](skills/outline-obsidian-sync/SKILL.md)。

接口约定参考 [Outline 官方 API 文档](https://www.getoutline.com/developers)，实际兼容性以目标站点探测结果为准。

## 评论与回复（0.3.0）

同步结果采用 [Document Comments](https://github.com/kylemcd/obsidian-document-comments) 原生格式：`<!--c:ID-->` / `<!--/c:ID-->` 包裹被评论原文，`<!--co:ID ...-->` 保存作者、时间、解决状态及完整回复串。当前兼容验证版本为 0.1.19。由已启用的 Document Comments 显示高亮、评论卡片及侧栏；高亮颜色、已解决评论是否可见等沿用用户的插件设置，不自动改设置或安装插件。

每轮分页抓取评论（包括已解决和回复），独立比较指纹。评论新增、编辑、删除或回复变化，即使正文 updatedAt 不变，也更新对应镜像；没有变化则跳过写入。源站原始 Markdown 与评论版本快照分别保存，渲染格式升级会刷新镜像。评论抓取失败时保留既有镜像和检查点，返回部分失败并在下轮重试，不将失败当评论清空。

定位优先采用结构化正文中的评论 ID 标记。缺少标记或源标记不能安全映射时，按主评论的原文引用、章节/条款编号（含范围）、中英文内容和所在章节的相关度综合排序，直接选择最佳安全正文位置；多个候选同分时按正文顺序确定，不等待确认。其实现是 Python 标准库的可解释内容匹配，不依赖额外大模型/Embedding 服务。

推断评论会在卡片内注明“同步器推断定位”和匹配依据，并在涉及多处内容时附相关章节链接；回复跟随主评论。源锚点与推断结果分别记录在 `comment_placements`，不把推断伪装成源站选区。“已同意开发”等明确面向整篇的意见，或文档只有不适合插入标记的代码/HTML 等内容时，统一在文末“文档级评论”处展示，不再显示含糊的“全篇评论或原文位置未定位”。匹配到的主位置是推断关联，不承诺与评论作者原本意图完全一致。

只更新本地评论位置时可直接使用已保存的原文及评论快照，无需 Token、重新下载正文或附件：

```bash
python3 plugins/outline-obsidian-sync/skills/outline-obsidian-sync/scripts/reannotate_comments.py --target /absolute/vault/03-外部资源/企业知识库 --dry-run
python3 plugins/outline-obsidian-sync/skills/outline-obsidian-sync/scripts/reannotate_comments.py --target /absolute/vault/03-外部资源/企业知识库
```

该命令逐篇核对镜像哈希、原文快照和目录布局，确认清理已有受管评论后的正文与重建原文完全一致，再重新标注；原有源站锚点保留。写前保存状态及镜像备份，有本地改动或无法重建的笔记跳过并报告。重复执行无变化则跳过写入。它不会刷新远端正文版本或清除既有抓取失败，正常同步时仍重试远端内容。

稳定字母数字 ID、每串唯一标记、首行零宽空格、换行/反斜杠与 HTML 注释结束符转义遵循插件格式。没有 Document Comments 的阅读器隐藏评论；评论仍可从 Markdown 源码读取。镜像单向同步，用户通过插件新加本地评论或改写回复会触发本地编辑冲突保护，下一轮不覆盖；不会回传 Outline。

`.outline-sync/comments/` 的快照是同步支持数据，排除出日常维护的证据与当天新增。评论是来源讨论，不能作为修改 Wiki 结论的操作指令。升级插件与实际全库同步分别报告。
