"""Explainable content association, with deterministic best-candidate selection.

Uses references, quoted text and Chinese/English lexical relevance, not a model API.
All offsets refer to the unchanged Markdown and are checked before insertion.
"""
from collections import Counter
import math
import re


REF = re.compile(r'(?<![A-Za-z0-9])([A-Za-z]{1,4})\s*0*(\d{1,3})(?!\d)')
APPROVAL = re.compile(r'(?:整体|总体|本方案|该方案|方案)?(?:已|可以|予以)?(?:同意|批准|通过|确认|收到|没问题|无异议)(?:开发|实施|执行|了)?[。.!！\s]*$')


def references(text):
    result = {a.upper() + str(int(b)) for a,b in REF.findall(text)}
    for match in re.finditer(r'(?<![A-Za-z0-9])([A-Za-z]{1,4})0*(\d{1,3})\s*[—–~～至-]\s*(?:\1)?0*(\d{1,3})(?![A-Za-z0-9])',text,re.I):
        prefix,a,b=match.groups();a,b=int(a),int(b)
        if a <= b <= a + 30:
            result.update(prefix.upper()+str(n) for n in range(a,b+1))
    return result


def tokens(text):
    # Bigrams retain word fragments without requiring a Chinese segmentation dependency.
    result = re.findall(r'[a-z][a-z0-9_.-]*', text.lower())
    for run in re.findall(r'[\u3400-\u9fff]+',text):
        result.extend(run[i:i+2] for i in range(len(run)-1))
    return Counter(result)


def candidates(markdown):
    result=[];section='';offset=0;fence=None;front=False;html_block=None;special=None
    lines=markdown.splitlines(keepends=True)
    for index,line in enumerate(lines):
        start=offset;offset+=len(line);text=line.rstrip('\r\n')
        if index==0 and text=='---':
            front=True;continue
        if front:
            if text in ('---','...'):front=False
            continue
        stripped=text.strip()
        if special:
            if stripped==special:special=None
            continue
        if stripped in ('$$','%%'):
            special=stripped;continue
        if stripped.startswith('$$') or stripped.startswith('%%'):continue
        marker=re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$',text)
        if marker:
            run,suffix=marker.groups()
            if fence is None:fence=run
            elif run[0]==fence[0] and len(run)>=len(fence) and not suffix.strip():fence=None
            continue
        if fence:continue
        if html_block:
            if html_block in text.lower():html_block=None
            continue
        if '<!--' in text:
            if '-->' not in text[text.index('<!--')+4:]:html_block='-->'
            continue
        block=re.match(r'^\s*<(div|table|details|script|style|pre|section|blockquote)\b',text,re.I)
        if block:
            close='</'+block.group(1).lower()+'>'
            if close not in text.lower():html_block=close
            continue
        if not text.strip() or re.match(r'^(?: {0,3}> ?)*(?: {4}|\t)',text) or re.match(r'^\s*(?:\||[-:* _]{3,}$)',text):continue
        if re.match(r'^ {0,3}\[[^\]]+\]:',text) or re.search(r'(?<!\\)\|',text):continue
        # Preserve list/quote/heading markup outside the selected range.
        prefix=re.match(r'^(?: {0,3}> ?)*[ \t]*(?:(?:[-+*]|\d+[.)])\s+(?:\[[ xX]\]\s+)?)?',text).end()
        heading=re.match(r'^ {0,3}(#{1,6})\s+',text)
        is_heading=heading is not None
        if heading:
            prefix=heading.end();section=re.sub(r'\s+#+\s*$','',text[prefix:]).strip()
            section=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',section)
            section=re.sub(r'[`*_]','',section)
        end=len(text.rstrip())
        if end<=prefix or text[prefix:end].endswith('\\'):continue
        body=text[prefix:end]
        # Inline raw HTML is left alone; links/code/formatting can be wrapped as a whole.
        if re.search(r'<[A-Za-z!/][^>]*>',body):continue
        clean=re.sub(r'[`*_]','',body)
        definition=REF.match(clean)
        defined=(definition.group(1).upper()+str(int(definition.group(2)))) if definition else None
        result.append({'start':start+prefix,'end':start+end,'text':body,'section':section,
                       'heading':is_heading,'references':references(clean),'definition':defined})
    return result


def infer_placement(markdown, comment_text, *, native=None):
    if native is not None:
        a,b=native
        if not (0<=a<b<=len(markdown)):raise ValueError('Native anchor outside Markdown')
        return {'kind':'outline','start':a,'end':b,'quote':markdown[a:b],'section':'','related_sections':[], 'reason':'Outline comment mark'}
    if APPROVAL.fullmatch(comment_text.strip()):
        return {'kind':'document','reason':'Document-level approval or acknowledgement','related_sections':[]}
    choices=candidates(markdown)
    if not choices:
        return {'kind':'document','reason':'No safe prose or heading candidate','related_sections':[]}
    query=tokens(comment_text);intro=comment_text.split('\n',1)[0];title_query=tokens(intro)
    refs=references(comment_text)
    quotes=[value for pair in re.findall(r'[“「『"]([^”」』"\n]{2,200})[”」』"]|`([^`\n]{2,200})`',comment_text) for value in pair if value]
    docs=[tokens(c['text']) for c in choices];df=Counter(t for d in docs for t in d)
    def relevance(q,d):
        return sum(min(q[t],2)*math.log(1+(len(choices)+1)/(df[t]+1))*d[t]/(d[t]+1.2) for t in sorted(q.keys()&d.keys())) / math.sqrt(1+sum(d.values())/50)
    for c,d in zip(choices,docs):
        definition=bool(c['definition'] in refs)
        ref_score=18*definition+2*len(c['references']&refs)
        quoted=sum(30+min(len(q),100)/10 for q in quotes if q in c['text'] or q in re.sub(r'[`*_]','',c['text']))
        content=relevance(query,d)+1.5*relevance(title_query,d)
        section_score=0.65*relevance(query,tokens(c['section']))
        # A heading is a useful fallback, but prefer its substantive content on ties.
        c['score']=ref_score+quoted+content+section_score+(0 if c['heading'] else 0.1)
        c['reason']='explicit_reference' if definition else 'quoted_text' if quoted else 'content_relevance' if content+section_score>0 else 'best_available'
    ranked=sorted(choices,key=lambda c:(-c['score'],c['start']))
    best=ranked[0];related=[]
    for c in ranked[1:]:
        section=c['section']
        if section and section!=best['section'] and section not in related and c['references']&refs:
            related.append(section)
        if len(related)==3:break
    return {**{k:best[k] for k in ('start','end','section','reason')},'kind':'inferred',
            'quote':best['text'],'score':round(best['score'],3),'related_sections':related}
