#!/usr/bin/env python3
"""Render scoped Dataview index blocks and verify them through official Obsidian CLI.

Does not write notes, enable plugins, modify settings, refresh caches or read note bodies.
"""
import argparse
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys
import time
import uuid

START = '<!-- obsidian-kb-maintenance:dataview:start -->'
END = '<!-- obsidian-kb-maintenance:dataview:end -->'
IGNORED_SEGMENTS = r'(^|/)(\.[^/]+|assets|imgs|prompts|node_modules|backups?|修改前备份)(/|$)'


def quoted(value):
    return json.dumps(value, ensure_ascii=False)


def checked_path(value):
    if not isinstance(value, str) or not value or re.search(r'[\x00-\x1f\x7f\\]', value):
        raise ValueError('Invalid vault-relative path')
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ('', '.', '..') or part.startswith('.') for part in value.split('/')):
        raise ValueError('Unsafe vault-relative path')
    return value


def build_plan(config):
    if not isinstance(config, dict) or set(config) != {'indexes'} or not isinstance(config['indexes'], list) or not config['indexes']:
        raise ValueError('Config requires a nonempty indexes list')
    plans, roots, indexes = [], set(), set()
    for item in config['indexes']:
        if not isinstance(item, dict) or set(item) - {'root', 'index', 'exclude_folders', 'exclude_files', 'property_audit'}:
            raise ValueError('Invalid index configuration')
        root = checked_path(item.get('root'))
        index = checked_path(item.get('index'))
        if '/' in root or not re.fullmatch(r'0[0-6]-.+', root):
            raise ValueError('Choose an explicit numbered knowledge root (00..06)')
        if not index.startswith(root + '/') or not index.endswith('.md'):
            raise ValueError('Index must be a Markdown note inside its root')
        if root in roots or index in indexes:
            raise ValueError('Duplicate root or index path')
        roots.add(root)
        indexes.add(index)
        folders, files = [], []
        for key, output in [('exclude_folders', folders), ('exclude_files', files)]:
            values = item.get(key, [])
            if not isinstance(values, list):
                raise ValueError('Exclusions must be lists')
            for value in values:
                value = checked_path(value)
                if not value.startswith(root + '/'):
                    raise ValueError('Exclusion must stay inside its root')
                if value not in output:
                    output.append(value)
        if not isinstance(item.get('property_audit', False), bool):
            raise ValueError('property_audit must be boolean')
        source = 'FROM ' + quoted(root) + ''.join(' AND -' + quoted(folder) for folder in folders)
        filters = ['file.path != ' + quoted(index), '!regextest(' + quoted(IGNORED_SEGMENTS) + ', file.path)']
        filters += ['file.path != ' + quoted(file) for file in files]
        scope = source + '\nWHERE ' + '\n  AND '.join(filters)
        queries = [
            {'name': '全部笔记（按目录）', 'dql': 'TABLE WITHOUT ID key AS "目录", rows.file.link AS "笔记"\n' + scope + '\nSORT file.path ASC\nGROUP BY file.folder\nSORT key ASC'},
            {'name': '最近文件改动', 'dql': 'TABLE type AS "类型", status AS "状态", file.mtime AS "文件修改时间"\n' + scope + '\nSORT file.mtime DESC\nLIMIT 20'},
        ]
        if root[:2] in ('01', '02'):
            queries.append({'name': '按已有状态查看', 'dql': 'TABLE WITHOUT ID key AS "状态", rows.file.link AS "笔记"\n' + scope + '\nSORT file.path ASC\nGROUP BY default(status, "未标注")\nSORT key ASC'})
        if root[:2] == '03':
            queries.append({'name': '来源说明与完整性', 'dql': 'TABLE status AS "整理状态", content_complete AS "正文完整", attachments_complete AS "附件完整"\n' + scope + '\n  AND endswith(file.name, "来源说明")\nSORT file.path ASC'})
        if root[:2] == '06':
            queries.append({'name': '按已有领域查看', 'dql': 'TABLE domains AS "领域", type AS "类型"\n' + scope + '\nSORT file.folder ASC, file.name ASC'})
        if item.get('property_audit', False):
            queries.append({'name': '属性缺口', 'dql': 'TABLE title AS "标题属性", type AS "类型属性"\n' + scope + '\n  AND (!title OR !type)\nSORT file.path ASC'})
        block = START + '\n## 自动笔记索引\n\n'
        block += '以下目录由 Dataview 按文件和已有属性动态生成；保留上方人工导航。最近文件改动仅指文件修改时间，不代表当天新增或知识已核验。未填写的状态、日期和完整性保持空缺，不推断完成。\n\n'
        for query in queries:
            block += '### ' + query['name'] + '\n\n```dataview\n' + query['dql'] + '\n```\n\n'
        block += '属性缺口和查询结果仅是维护线索，不能代替语义核验，也不授予修改原稿、原文或受保护页面的权限。\n' + END
        plans.append({'root': root, 'index': index, 'queries': queries, 'block': block,
                      'scope_query': 'LIST\n' + scope + '\nSORT file.path ASC'})
    return plans


def merge_block(existing, block):
    starts, ends = existing.count(START), existing.count(END)
    if starts == ends == 0:
        if re.search(r'(?m)^\s*```dataview(?:js)?\s*$', existing):
            raise ValueError('Unmanaged Dataview block exists; reconcile existing queries first')
        return existing + ('\n\n' if not existing.endswith('\n') else '\n') + block + '\n'
    if starts != 1 or ends != 1 or existing.index(START) > existing.index(END):
        raise ValueError('Malformed or duplicate managed index markers')
    before, after = existing.split(START, 1)
    _, suffix = after.split(END, 1)
    return before + block + suffix


def verification_code(plans):
    # JSON values are data in a fixed, read-only JavaScript program; no note code is executed.
    payload = [{key: plan[key] for key in ('root', 'index', 'queries', 'scope_query')} for plan in plans]
    return r'''(async () => {
const plans = __PLANS__;
const plugin = app.plugins.plugins.dataview;
const api = plugin?.api;
if (!api || typeof api.query !== "function") return JSON.stringify({ready:false, reason:"dataview_unavailable"});
if (plugin.index?.initialized === false) return JSON.stringify({ready:false, reason:"index_initializing"});
const checks = [];
const links = value => {
  if (Array.isArray(value)) return value.flatMap(links);
  if (value && typeof value === "object" && typeof value.path === "string" && value.type === "file") return [value.path];
  return [];
};
for (const plan of plans) {
  const scope = await api.query(plan.scope_query, plan.index);
  if (!scope.successful) { checks.push({root:plan.root,index:plan.index,successful:false,reason:"scope_query_failed",error:String(scope.error)}); continue; }
  const paths = scope.value.values.map(link => link.path);
  const permitted = new Set(paths);
  const scoped = paths.every(path => path.startsWith(plan.root + "/") && path !== plan.index);
  for (const query of plan.queries) {
    const result = await api.query(query.dql, plan.index);
    if (!result.successful) { checks.push({root:plan.root,index:plan.index,view:query.name,successful:false,error:String(result.error)}); continue; }
    const found = links(result.value.values);
    const contained = scoped && found.every(path => permitted.has(path));
    const catalogComplete = query.name !== "全部笔记（按目录）" || (new Set(found).size === permitted.size && found.length === permitted.size);
    checks.push({root:plan.root,index:plan.index,view:query.name,successful:contained && catalogComplete,type:result.value.type,rows:result.value.values.length,scopePages:permitted.size,links:found.length,contained,catalogComplete});
  }
}
return JSON.stringify({ready:true,version:plugin.manifest?.version,checks});
})()'''.replace('__PLANS__', json.dumps(payload, ensure_ascii=False))


def verify(vault, plans, *, runner=subprocess.run):
    combined = {'ready': True, 'checks': []}

    def evaluate(code):
        for attempt in range(5):
            result = runner(['obsidian', 'vault=' + vault, 'eval', 'code=' + code],
                            capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise ValueError('Official Obsidian CLI query verification failed')
            for line in reversed(result.stdout.splitlines()):
                value = line.removeprefix('=> ').strip()
                if value.startswith('{'):
                    try:
                        candidate = json.loads(value)
                    except ValueError:
                        continue
                    if isinstance(candidate, dict):
                        return candidate
            if attempt < 4:
                time.sleep(0.2)
        raise ValueError('No structured result returned by Obsidian CLI')

    # CLI eval may return before an async query settles. Store only the compact result
    # temporarily in application memory, poll synchronously, then remove it.
    batches = [dict(plan, queries=[query]) for plan in plans for query in plan['queries']]
    for plan in batches:
        key = quoted('__obsidianKbMaintenanceDataview_' + uuid.uuid4().hex)
        program = verification_code([plan])
        start = '(()=>{const k=' + key + ';if(globalThis[k])return JSON.stringify({started:true});globalThis[k]={state:"running"};setTimeout(()=>{if(!globalThis[k])return;void ' + program + '.then(value=>{if(globalThis[k])globalThis[k]={state:"done",result:JSON.parse(value)}}).catch(error=>{if(globalThis[k])globalThis[k]={state:"failed",reason:String(error)}})},0);return JSON.stringify({started:true});})()'
        parsed = None
        try:
            if not evaluate(start).get('started'):
                raise ValueError('Dataview verification did not start')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    polled = evaluate('JSON.stringify(globalThis[' + key + '] || {state:"missing"})')
                except ValueError as error:
                    if str(error) != 'No structured result returned by Obsidian CLI':
                        raise
                    time.sleep(0.2)
                    continue
                if polled.get('state') == 'done':
                    parsed = polled.get('result')
                    break
                if polled.get('state') != 'running':
                    raise ValueError('Dataview query failed or verification state disappeared')
                time.sleep(0.2)
        finally:
            # A late Promise must not recreate the slot after timeout.
            cleanup = '(()=>{const k=' + key + ';if(globalThis[k]){delete globalThis[k]};return JSON.stringify({deleted:true});})()'
            try:
                evaluate(cleanup)
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
        if not isinstance(parsed, dict) or not isinstance(parsed.get('ready'), bool):
            raise ValueError('Dataview verification timed out or returned invalid data')
        if not parsed['ready']:
            return parsed
        if len(parsed.get('checks', [])) != 1:
            raise ValueError('Dataview returned incomplete verification checks')
        combined['checks'].extend(parsed.get('checks', []))
        if 'version' in parsed:
            combined['version'] = parsed['version']
    return combined


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('render', 'verify'))
    parser.add_argument('config', help='Explicit root/index/exclusion JSON configuration')
    parser.add_argument('--vault', help='Confirmed Obsidian vault name; required for verify')
    args = parser.parse_args()
    if args.command == 'verify' and not args.vault:
        parser.error('--vault is required for verify')
    try:
        with open(args.config, encoding='utf-8') as source:
            plans = build_plan(json.load(source))
        output = plans if args.command == 'render' else verify(args.vault, plans)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command == 'verify' and (not output['ready'] or not all(check.get('successful') for check in output.get('checks', []))):
            return 1
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error) if isinstance(error, ValueError) else type(error).__name__, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
