"""Apply artifact-authored cells while retaining unrelated native XLSX parts.

The artifact exporter does not promise byte preservation of native Excel objects.
This packaging adapter copies only its authored cell content, retains original
style IDs, and verifies that every untargeted ZIP entry is byte-for-byte intact.
It does not create values or formulas; those come from the artifact export.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import nullcontext
from zipfile import ZipFile, ZIP_DEFLATED
import copy
import hashlib
import json
import os
import posixpath
import re
import subprocess
from xml.etree import ElementTree as ET

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
Q='{'+NS+'}'
ET.register_namespace('',NS)

def sheet_paths(z):
    rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    targets={r.get('Id'):posixpath.normpath(posixpath.join('xl',r.get('Target').lstrip('/'))) if not r.get('Target').startswith('/') else r.get('Target').lstrip('/') for r in rels}
    book=ET.fromstring(z.read('xl/workbook.xml'))
    return {s.get('name'):targets[s.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')] for s in book.find(Q+'sheets')}

def merge_cells(original,authored,changes,destination):
    grouped={}
    for c in changes:grouped.setdefault(c['sheet'],set()).add(c['cell'])
    styles={(c['sheet'],c['cell']):c['style_id'] for c in changes if 'style_id' in c}
    with ZipFile(original) as src,ZipFile(authored) as made:
        srcpaths,outpaths=sheet_paths(src),sheet_paths(made)
        strings=[]
        if 'xl/sharedStrings.xml' in made.namelist():
            strings=list(ET.fromstring(made.read('xl/sharedStrings.xml')))
        replacements={}
        for sheet,addresses in grouped.items():
            original_xml=src.read(srcpaths[sheet])
            tree=ET.fromstring(original_xml);data=tree.find(Q+'sheetData')
            rows={int(r.get('r')):r for r in data}
            authored_tree=ET.fromstring(made.read(outpaths[sheet]))
            cells={c.get('r'):c for c in authored_tree.iter(Q+'c')}
            for address in addresses:
                ri=int(re.search(r'\d+',address).group());row=rows.get(ri)
                if row is None:
                    row=ET.Element(Q+'row',r=str(ri));data.append(row);rows[ri]=row
                old=next((c for c in row if c.get('r')==address),None)
                new=copy.deepcopy(cells[address]) if address in cells else ET.Element(Q+'c',r=address)
                if (sheet,address) in styles:new.set('s',str(styles[(sheet,address)]))
                elif old is not None and old.get('s') is not None:new.set('s',old.get('s'))
                else:new.attrib.pop('s',None)
                if new.get('t')=='s':
                    value=new.find(Q+'v');si=strings[int(value.text)];new.remove(value)
                    inline=ET.SubElement(new,Q+'is')
                    for child in si:inline.append(copy.deepcopy(child))
                    new.set('t','inlineStr')
                if old is not None:row.remove(old);row.append(new)
                else:row.append(new)
                def col(c):
                    n=0
                    for ch in re.match('[A-Z]+',c.get('r')).group():n=n*26+ord(ch)-64
                    return n
                row[:]=sorted(row,key=col)
            data[:]=sorted(data,key=lambda r:int(r.get('r')))
            replacement=ET.tostring(data,encoding='utf-8')
            replacements[srcpaths[sheet]]=re.sub(rb'<(?:\w+:)?sheetData\b[^>]*>.*?</(?:\w+:)?sheetData>',
                                                  lambda _:replacement,original_xml,count=1,flags=re.S)
            dimension=tree.find(Q+'dimension')
            if dimension is not None:
                def coordinates(address):
                    letters,digits=re.fullmatch(r'([A-Z]+)(\d+)',address).groups();number=0
                    for ch in letters:number=number*26+ord(ch)-64
                    return number,int(digits)
                first=dimension.get('ref').split(':')[0]
                last=dimension.get('ref').split(':')[-1]
                points=[coordinates(last)]+[coordinates(a) for a in addresses]
                ci=max(p[0] for p in points);ri=max(p[1] for p in points);letters=''
                while ci:ci,rem=divmod(ci-1,26);letters=chr(65+rem)+letters
                dimension.set('ref',first+':'+letters+str(ri))
                replacements[srcpaths[sheet]]=re.sub(rb'<(?:\w+:)?dimension\b[^>]*/>',
                    lambda _:ET.tostring(dimension,encoding='utf-8'),replacements[srcpaths[sheet]],count=1)
            if replacements[srcpaths[sheet]]==original_xml:
                raise ValueError('Missing or unchanged sheetData package boundary: '+sheet)
        with ZipFile(destination,'w',ZIP_DEFLATED) as output:
            for info in src.infolist():output.writestr(copy.copy(info),replacements.get(info.filename,src.read(info.filename)))
        with ZipFile(destination) as output:
            for info in src.infolist():
                if info.filename not in replacements and src.read(info.filename)!=output.read(info.filename):
                    raise ValueError('Unrelated workbook part changed: '+info.filename)
    return sorted(replacements)

def publish_cells(workbook,changes,root):
    if not changes:return
    root=Path(root);workbook=Path(workbook)
    config=json.loads((root/'data/engine/artifact_tool_runtime.json').read_text(encoding='utf-8'))
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    before=digest(workbook)
    with nullcontext(root/'data/engine/workbook_measurement_staging') as tmp:
        tmp=Path(tmp);plan=tmp/'plan.json';authored=tmp/'authored.xlsx';merged=tmp/'merged.xlsx'
        tmp.mkdir(parents=True,exist_ok=True)
        ranks=json.loads((root/'ranks_python.json').read_text(encoding='utf-8'))
        import openpyxl
        from engine.live_legislative_pillar import nonlegislative_inputs
        source=openpyxl.load_workbook(workbook,data_only=False)
        members=[row[0] for row in source['Rank & Allocation'].iter_rows(min_row=3,max_row=535,values_only=True)]
        influence,demographic=nonlegislative_inputs(source,members)
        views=[];formats={}
        for title in ('Rank & Allocation','Blended Model','Category Stance','Coalition Profiles'):
            sheet=source[title];rows=list(sheet.iter_rows())
            views.append({'name':title,'values':[[c.value for c in row] for row in rows],
                          'styles':[[c.style_id for c in row] for row in rows],
                          'widths':{k:v.width for k,v in sheet.column_dimensions.items()},
                          'heights':{k:v.height for k,v in sheet.row_dimensions.items() if v.height}})
            for row in rows:
                for c in row:
                    if c.style_id in formats:continue
                    color=lambda x:('#'+x.rgb[-6:]) if x is not None and x.type=='rgb' else None
                    font={'name':c.font.name or 'Calibri','size':c.font.sz or 11,'bold':bool(c.font.bold),'italic':bool(c.font.italic)}
                    if color(c.font.color):font['color']=color(c.font.color)
                    fmt={'font':font,'wrapText':bool(c.alignment.wrap_text)}
                    if c.fill.patternType=='solid' and color(c.fill.fgColor):fmt['fill']=color(c.fill.fgColor)
                    if c.alignment.horizontal in ('left','center','right'):fmt['horizontalAlignment']=c.alignment.horizontal
                    formats[c.style_id]=fmt
        source.close()
        context=[{'row':i+3,'press':ranks['press_pillar'][n],'influence':influence[n],'demographic':demographic[n]} for i,n in enumerate(members)]
        plan.write_text(json.dumps({'input':str(workbook),'root':str(root),'output':str(authored),'changes':changes,
                                   'views':views,'formats':formats,'context':context,'scores':ranks['score'],'ranks':ranks['rank']}),encoding='utf-8')
        env={**os.environ,'ARTIFACT_TOOL_NODE_MODULES':config['node_modules']}
        helper=root/'fec/stance/workbook_cell_sync.cjs';proof_path=Path(str(authored)+'.report.json')
        def valid_proof():
            if not authored.exists() or not proof_path.exists():return False
            proof=json.loads(proof_path.read_text(encoding='utf-8'))
            return (proof.get('schema')=='workbook-cell-author-v1' and proof.get('status')=='PASS'
                    and proof.get('scores_and_ranks_verified')==533
                    and proof.get('plan_sha256')==digest(plan) and proof.get('helper_sha256')==digest(helper)
                    and proof.get('output_sha256')==digest(authored))
        if valid_proof():exit_code=None
        else:
            result=subprocess.run([config['node'],str(helper),str(plan)],env=env)
            exit_code=result.returncode
        # The bundled Windows runtime can fail during teardown after a successful
        # export. Only a fresh, fully hash-bound 533-member PASS permits promotion.
        if not valid_proof():raise RuntimeError('Artifact workbook sync lacks a verified PASS export')
        parts=merge_cells(workbook,authored,changes,merged)
        if digest(workbook)!=before:raise RuntimeError('Workbook changed during synchronization')
        os.replace(merged,workbook)
    (root/'data/engine/workbook_measurement_sync.json').write_text(json.dumps({
        'schema':'workbook-measurement-sync-v1','status':'PASS','cells':len(changes),
        'before_sha256':before,'after_sha256':digest(workbook),'changed_parts':parts,
        'author_process_exit':exit_code,'author_report':str(proof_path),
        'preservation':'All untargeted ZIP entries retained byte-for-byte; original styles retained, with category colors reassigned to current labels.'},indent=2),encoding='utf-8')
