"""Outline snapshots and native Document Comments serialization."""
import hashlib
import html
import json
import re
import urllib.parse

from outline_client import OutlineError
from comment_anchors import infer_placement


RENDER_VERSION = 3


def normalize_comments(rows):
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'] or not isinstance(row.get('documentId'), str) or not row['documentId'] or row['id'] in seen:
            raise OutlineError('Invalid or duplicate comment')
        seen.add(row['id'])
        data = row.get('data')
        if not isinstance(data, dict):
            raise OutlineError('Comment has no rich text body')
        if any(row.get(key) is not None and not isinstance(row[key], str) for key in ('parentCommentId', 'createdAt', 'updatedAt', 'resolvedAt')):
            raise OutlineError('Invalid comment metadata')
        author = row.get('createdBy') or {}
        if not isinstance(author, dict) or (author.get('name') is not None and not isinstance(author['name'], str)):
            raise OutlineError('Invalid comment author')
        result.append({**{key: row.get(key) for key in (
            'id', 'documentId', 'parentCommentId', 'createdAt', 'updatedAt', 'resolvedAt')},
            'author': author.get('name') or '未知作者',
            'data': data})
    return sorted(result, key=lambda row: (row.get('createdAt') or '', row['id']))


def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def plain(node):
    if node.get('type') == 'text':
        return node.get('text', '')
    if node.get('type') in ('hard_break', 'hardBreak'):
        return '\n'
    if node.get('type') in ('mention', 'emoji'):
        return str((node.get('attrs') or {}).get('label') or (node.get('attrs') or {}).get('name') or '')
    children = node.get('content') or []
    sep = '\n' if node.get('type') in ('doc', 'bullet_list', 'ordered_list') else ''
    return sep.join(plain(child) for child in children)


def comment_markdown(node, replace=None):
    """Convert comment rich text to Markdown supported inside native threads."""
    kind, attrs = node.get('type'), node.get('attrs') or {}
    def safe_url(url, image=False):
        if not isinstance(url, str):
            return ''
        try:
            if urllib.parse.urlsplit(url).scheme.lower() not in ('', 'https', 'http'):
                return ''
        except ValueError:
            return ''
        return (replace(url, image) if replace else url).replace('<', '%3C').replace('>', '%3E').replace(' ', '%20')
    if kind == 'text':
        text = re.sub(r'([\\`*_[\]<>])', r'\\\1', node.get('text', ''))
        for mark in node.get('marks') or []:
            token = {'strong': '**', 'em': '*', 'strike': '~~'}.get(mark.get('type'))
            if token:
                text = token + text + token
            elif mark.get('type') == 'code_inline':
                raw = node.get('text', '')
                runs = re.findall(r'`+', raw)
                fence = '`' * (1 + max(map(len, runs), default=0))
                text = fence + ' ' + raw + ' ' + fence
            elif mark.get('type') == 'link':
                url = safe_url((mark.get('attrs') or {}).get('href'))
                if url:
                    text = '[' + text + '](<' + url + '>)'
        return text
    if kind in ('image', 'attachment'):
        url = safe_url(attrs.get('src') or attrs.get('href'), kind == 'image')
        label = re.sub(r'([\\[\]<>])', r'\\\1', str(attrs.get('alt') or attrs.get('title') or '附件'))
        return ('!' if kind == 'image' else '') + '[' + label + '](<' + url + '>)' if url else label
    if kind in ('hard_break', 'hardBreak'):
        return '\n'
    if kind in ('mention', 'emoji'):
        return html.escape(plain(node))
    if kind == 'code_block':
        code = plain(node)
        fence = '`' * max(3, 1 + max(map(len, re.findall(r'`+',code)), default=0))
        return fence + '\n' + code + '\n' + fence
    children = [comment_markdown(child, replace) for child in node.get('content') or []]
    if kind in ('bullet_list', 'ordered_list'):
        return '\n'.join(('- ' if kind == 'bullet_list' else str(i + 1) + '. ') + child for i,child in enumerate(children))
    if kind == 'blockquote':
        return '\n'.join('> ' + line for line in '\n'.join(children).splitlines())
    return ('\n\n' if kind in ('doc', 'list_item') else '').join(children)


def native_id(comment_id):
    return 'ol' + hashlib.sha256(comment_id.encode()).hexdigest()[:24]


def safe_comment(text):
    return text.replace('-->', '--\u200b>')


def thread_line(row, replace, annotation=''):
    # Keep a physical line per entry, matching the plugin's backslash codec.
    author = re.sub(r'[\r\n:()]', ' ', safe_comment(row['author'])).strip() or '未知作者'
    timestamp = re.sub(r'\s+', '_', safe_comment(row.get('createdAt') or '')).replace(')', '_').replace('(', '_')
    text = comment_markdown(row['data'], replace)
    if annotation:
        text = annotation + '\n\n' + text
    if row.get('updatedAt') and row['updatedAt'] != row.get('createdAt'):
        text += '\n（编辑于 ' + row['updatedAt'] + '）'
    text = safe_comment(text).replace('\\', '\\\\').replace('\r', r'\r').replace('\n', r'\n')
    return author + (' (' + timestamp + ')' if timestamp else '') + ': ' + text


def anchor_spans(markdown, data, ids):
    """Require a unique full block and safe plain-text leaf positions; never guess."""
    spans = []
    protected = [m.span() for m in re.finditer(
        r'(?ms)^\s{0,3}(`{3,}|~{3,})[^\n]*\n.*?^\s{0,3}\1[^\n]*(?:\n|$)|`+[^`\n]*`+|!?\[[^\n]*?\]\([^\n]*?\)|<[^>]*>|^\s{0,3}\[[^\n]+\]:[^\n]*', markdown)]
    fence, fence_start, offset = None, None, 0
    for line in markdown.splitlines(keepends=True):
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if match:
            marker, suffix = match.groups()
            if fence is None:
                fence, fence_start = marker, offset
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not suffix.strip():
                protected.append((fence_start, offset + len(line)))
                fence = None
        offset += len(line)
    if fence:
        protected.append((fence_start, len(markdown)))
    protected.extend(m.span() for m in re.finditer(r'(?m)^(?: {4}|\t)[^\n]*(?:\n|$)', markdown))

    def walk(node):
        children = node.get('content') or []
        if node.get('type') in ('paragraph', 'heading') and children and all(c.get('type') == 'text' for c in children):
            block = ''.join(c.get('text', '') for c in children)
            if not block:
                return
            matches = list(re.finditer(re.escape(block), markdown))
            if len(matches) != 1:
                return
            start = matches[0].start()
            offset = 0
            for child in children:
                text = child.get('text', '')
                for mark in child.get('marks') or []:
                    attrs = mark.get('attrs') or {}
                    cid = attrs.get('id') or attrs.get('commentId')
                    a, b = start + offset, start + offset + len(text)
                    if mark.get('type') == 'comment' and cid in ids and a < b:
                        if not any(a < y and b > x for x,y in protected) and not re.search(r'[\n\r`*_[\]<>\\|]', text):
                            spans.append((a, b, cid))
                offset += len(text)
        else:
            for child in children:
                walk(child)
    if isinstance(data, dict):
        walk(data)
    return spans


def group_comments(rows):
    by_id = {row['id']: row for row in rows}
    groups = {}
    for row in rows:
        root, visited = row, set()
        while root.get('parentCommentId') in by_id and root['id'] not in visited:
            visited.add(root['id'])
            root = by_id[root['parentCommentId']]
        if root['id'] in visited:
            raise OutlineError('Cyclic comment replies')
        groups.setdefault(root['id'], []).append(row)
    groups = dict(sorted(groups.items(), key=lambda item: (by_id[item[0]].get('createdAt') or '', item[0])))
    for root, group in groups.items():
        group.sort(key=lambda row: (row['id'] != root, row.get('createdAt') or '', row['id']))
    return groups


def plan_comments(markdown, data, rows, native_ranges=None):
    groups=group_comments(rows)
    spans=anchor_spans(markdown,data,groups)
    ranges=dict(native_ranges or {})
    for root in groups:
        pieces=sorted({(a,b) for a,b,cid in spans if cid==root})
        if not pieces:continue
        a,b=pieces[0]
        for x,y in pieces[1:]:
            if x>b:break
            b=max(b,y)
        else:ranges[root]=(a,b)
    return {root:infer_placement(markdown,plain(group[0]['data']),native=ranges.get(root)) for root,group in groups.items()}


def render_comments(markdown, data, rows, replace=None, placements=None):
    if not rows:
        return markdown, []
    fence = None
    for line in markdown.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if match:
            marker, suffix = match.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not suffix.strip():
                fence = None
    if fence:
        raise OutlineError('Unclosed Markdown fence prevents safe comment serialization')
    groups=group_comments(rows)
    by_id={row['id']:row for row in rows}
    placements=plan_comments(markdown,data,rows) if placements is None else placements
    ranges={root:(p['start'],p['end']) for root,p in placements.items() if p['kind']!='document'}
    # Existing source markers must not collide with deterministic generated IDs.
    occupied = set(re.findall(r'<!--(?:c:|/c:|co:)([A-Za-z0-9]+)', markdown))
    if occupied.intersection(native_id(root) for root in groups):
        raise OutlineError('Document Comments marker collision in source')
    changes, blocks, fallback = {}, {}, []
    for root, group in groups.items():
        cid = native_id(root)
        if root in ranges:
            a,b = ranges[root]
            quote = markdown[a:b]
            # A leading HTML comment must not change the block's Markdown rendering.
            prefix = markdown[markdown.rfind('\n',0,a)+1:a]
            spacer = '\u200b' if re.fullmatch(r'(?:[ \t]*>[ \t]?)*[ \t]*(?:(?:[-+*]|[0-9]+[.)]) (?:\[[ xX]\] )?)?', prefix) else ''
            changes.setdefault(a, []).append((1, cid, spacer + '<!--c:' + cid + '-->'))
            changes.setdefault(b, []).append((0, cid, '<!--/c:' + cid + '-->'))
            end = markdown.find('\n',b)
            end = len(markdown) if end < 0 else end
        else:
            quote = '文档级评论'
            fallback.append('\u200b<!--c:' + cid + '-->' + quote + '<!--/c:' + cid + '-->')
            end = None
        author = re.sub(r'\s+', '_', safe_comment(by_id[root]['author'])).replace('"', "'")
        at = by_id[root].get('createdAt')
        metadata = ' by:' + author + (' at:' + re.sub(r'\s+', '_', safe_comment(at)) if at else '')
        metadata += ' status:' + ('resolved' if by_id[root].get('resolvedAt') else 'open')
        metadata += ' quote:"' + safe_comment(re.sub(r'\s+', ' ', quote).replace('"', "'")) + '"'
        placement=placements[root]
        metadata += ' placement:' + placement['kind']
        annotation=''
        if placement['kind']=='inferred':
            section=placement.get('section') or '正文'
            section=re.sub(r'[*_`\[\]<>\\]','',section)
            reason={'explicit_reference':'章节编号','quoted_text':'原文引用','content_relevance':'内容相关度','best_available':'最佳可用正文位置'}[placement['reason']]
            annotation='【同步器推断定位：'+section+'；依据：'+reason+'】'
            related=placement.get('related_sections') or []
            if related:
                annotation+='\n相关章节：'+'、'.join('['+re.sub(r'[\[\]<>\\]','',h)+'](<#'+urllib.parse.quote(h,safe='')+'>)' for h in related)
        block = '<!--co:' + cid + metadata + '\n' + '\n'.join(thread_line(row, replace, annotation if row['id']==root else '') for row in group) + '\n-->'
        if end is None:
            fallback.append(block + '\n')
        else:
            blocks.setdefault(end, []).append(block)
    for offset, values in blocks.items():
        changes.setdefault(offset, []).append((2, '', '\n' + '\n'.join(values) + '\n'))
    for offset, values in sorted(changes.items(), reverse=True):
        markdown = markdown[:offset] + ''.join(value for _,_,value in sorted(values)) + markdown[offset:]
    if fallback:
        markdown += '\n\n## 文档级评论\n\n' + '\n'.join(fallback)
    return markdown, sorted(set(groups) - set(ranges))
