"""Rebuild owned comment annotations from local snapshots; no network or credentials."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import urllib.parse
from zoneinfo import ZoneInfo

from comments import RENDER_VERSION, fingerprint, group_comments, native_id, plan_comments, render_comments
from outline_client import OutlineError, origin
from markdown_links import rewrite
from sync_outline import atomic_write, contained, digest, layout, locked, managed_write, read_hash


def strip_owned(markdown, roots, native_roots):
    """Remove only our frames and recover proven native ranges in the clean body."""
    ids={native_id(root):root for root in roots}
    for header in ('\n\n## 文档评论（无原文定位）\n\n','\n\n## 文档级评论\n\n'):
        at=markdown.rfind(header)
        if at>=0:
            tail=markdown[at+len(header):]
            tail_ids=set(re.findall(r'<!--co:([A-Za-z0-9]+)\b',tail))
            if tail_ids and tail_ids<=ids.keys():markdown=markdown[:at]
    pattern=r'<!--co:(?:'+ '|'.join(map(re.escape,ids))+r')\b[^\n]*\n[\s\S]*?-->'
    bodies=list(re.finditer(pattern,markdown))
    groups=[]
    for m in bodies:
        if groups and markdown[groups[-1][1]:m.start()]=='\n':groups[-1]=(groups[-1][0],m.end())
        else:groups.append(m.span())
    for a,b in reversed(groups):
        if markdown[a-1:a]!='\n' or markdown[b:b+1]!='\n':raise OutlineError('Unrecognized managed comment layout')
        markdown=markdown[:a-1]+markdown[b+1:]
    markers=re.compile(r'<!--(?P<close>/)?c:(?P<id>'+ '|'.join(map(re.escape,ids))+r')-->')
    parts=[];cursor=0;size=0;starts={};ranges={}
    for m in markers.finditer(markdown):
        chunk=markdown[cursor:m.start()]
        if not m['close'] and chunk.endswith('\u200b'):
            # Several threads may start at the same position. Earlier markers
            # must not prevent removal of the next renderer-inserted spacer.
            prefix=(''.join(parts)+chunk[:-1]).rsplit('\n',1)[-1]
            if re.fullmatch(r'(?:[ \t]*>[ \t]?)*[ \t]*(?:(?:[-+*]|[0-9]+[.)]) (?:\[[ xX]\] )?)?',prefix):chunk=chunk[:-1]
        parts.append(chunk);size+=len(chunk);root=ids[m['id']]
        if m['close']:
            if root in starts and root in native_roots:ranges[root]=(starts[root],size)
        else:starts[root]=size
        cursor=m.end()
    parts.append(markdown[cursor:])
    return ''.join(parts),ranges


def reannotate(target, *, dry_run=False):
    root=Path(target).absolute();result={'saved':0,'skipped':0,'conflicts':0,'errors':[],
        'inferred':0,'outline':0,'document':0,'started_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
    with locked(root):
        state_path=contained(root,'.outline-sync/state.json')
        if not state_path.is_file():raise OutlineError('Sync state is required')
        initial=state_path.read_bytes()
        state=json.loads(initial)
        if state.get('schema')!=1:raise OutlineError('Unsupported sync state')
        if not dry_run:
            backup=contained(root,'.outline-sync/backups/reannotation-state-'+digest(initial)+'.json')
            if backup.exists() and backup.read_bytes()!=initial:raise OutlineError('State backup conflict')
            if not backup.exists():atomic_write(backup,initial)
        def checkpoint():
            if not dry_run:atomic_write(state_path,(json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode())
        collections=[x['collection'] for x in state['tree']]
        trees={x['collection']['id']:x['documents'] for x in state['tree']}
        paths,nodes,_=layout(collections,trees);url_paths={}
        for id,path in paths.items():
            for url in (state['documents'].get(id,{}).get('source_url'),nodes[id].get('url')):
                if url:
                    absolute=urllib.parse.urljoin(state['source'],url)
                    if origin(absolute)==origin(state['source']):url_paths[urllib.parse.urlsplit(absolute).path.rstrip('/')]=path
            url_paths['/doc/'+id]=path
        layout_hash=digest(json.dumps(url_paths,sort_keys=True,ensure_ascii=False).encode())
        for id,old in state['documents'].items():
            snapshot=old.get('comments_snapshot')
            if not snapshot:continue
            try:
                rows=json.loads(contained(root,snapshot).read_text())
                if fingerprint(rows)!=old.get('comments_hash') or Path(snapshot).stem!=old.get('comments_hash'):
                    raise OutlineError('Comment snapshot hash mismatch')
                if not rows:
                    old.update(comment_render_version=RENDER_VERSION,comment_placements={})
                    continue
                current=contained(root,old['path']);expected=old['sha256']
                actual=read_hash(current)
                if actual!=expected:
                    pending=state.get('pending',{}).get(old['path'])
                    if actual is None or actual!=pending:raise OutlineError('Local edit conflict')
                    expected=actual
                if old.get('layout_hash')!=layout_hash:raise OutlineError('Cached directory layout differs; run sync first')
                original=contained(root,old['original']);raw=original.read_bytes()
                if digest(raw)!=original.stem:raise OutlineError('Original snapshot hash mismatch')
                def replace(url,image):
                    absolute=urllib.parse.urljoin(state['source']+'/',url.replace('\\)',')').replace('\\(','('))
                    parsed=urllib.parse.urlsplit(absolute)
                    local=url_paths.get(parsed.path.rstrip('/')) if origin(absolute)==origin(state['source']) else None
                    if local and not image:return urllib.parse.quote(os.path.relpath(local,Path(old['path']).parent),safe='/')+('#'+parsed.fragment if parsed.fragment else '')
                    attachment=origin(absolute)==origin(state['source']) and parsed.path in ('/api/attachments.redirect','/api/files.get')
                    asset=state['assets'].get(digest(absolute.encode())) if image or attachment else None
                    if asset:return urllib.parse.quote(os.path.relpath(asset['path'],Path(old['path']).parent),safe='/')
                    return url
                base=rewrite(raw.decode(),replace)
                roots=group_comments(rows)
                native_roots=({r for r in roots if r not in old.get('unmapped_comments',[])} if old.get('comment_render_version')==2 else
                              {r for r,p in old.get('comment_placements',{}).items() if p['kind']=='outline'})
                clean,native=strip_owned(current.read_text(),roots,native_roots)
                if clean!=base:raise OutlineError('Cached body reconstruction differs; run sync first')
                if native_roots-set(native):raise OutlineError('Cannot preserve an existing Outline anchor')
                placements=plan_comments(base,None,rows,native_ranges=native)
                rendered,document_ids=render_comments(base,None,rows,replace,placements=placements)
                body=rendered.encode()
                for p in placements.values():result[p['kind']]+=1
                if digest(body)==expected:
                    result['skipped']+=1
                    old['sha256']=expected
                    state.get('pending',{}).pop(old['path'],None)
                else:
                    if not dry_run:old['sha256']=managed_write(root,old['path'],body,expected,state,checkpoint)
                    result['saved']+=1
                old.update(comment_render_version=RENDER_VERSION,comment_placements=placements,unmapped_comments=document_ids)
                checkpoint()
            except (OutlineError,OSError,UnicodeError,ValueError) as e:
                reason=str(e) if isinstance(e,OutlineError) else type(e).__name__
                if 'conflict' in reason.lower():result['conflicts']+=1
                result['errors'].append({'document_id':id,'reason':reason})
        result['finished_at']=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
        state['annotation_run']=result;checkpoint()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--target',required=True,type=Path);parser.add_argument('--dry-run',action='store_true');args=parser.parse_args()
    try:
        result=reannotate(args.target,dry_run=args.dry_run);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(1 if result['errors'] else 0)
    except (OutlineError,OSError,ValueError,KeyError) as e:
        print(str(e) if isinstance(e,OutlineError) else type(e).__name__);raise SystemExit(2)
