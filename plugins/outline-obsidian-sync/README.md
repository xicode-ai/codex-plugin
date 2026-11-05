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
