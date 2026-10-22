"""Apply artifact-authored cells while retaining unrelated native XLSX parts.

The artifact exporter does not promise byte preservation of native Excel objects.
This packaging adapter copies only its authored cell content, retains original style IDs, and verifies
that every untargeted ZIP entry is byte-for-byte intact. It creates no values or formulas (those come from the
artifact export); the one derived edit is sync_tables: a table's column names follow its header cells.
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
            filled,tables=sync_tables(src,srcpaths[sheet],data,rows);replacements.update(tables);addresses=addresses|filled;data[:]=sorted(data,key=lambda r:int(r.get('r')))
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
    # MELANGE_XLSX_WRITER=openpyxl authors the changed cells in process (D-178); the default runs the Node helper.
    from xlsx_openpyxl_authors import AUTHOR_MODULE,author_cell_sync,xlsx_writer
    import roster
    first,last,roster_n=roster.first_row('Rank & Allocation'),roster.last_row('Rank & Allocation',root=root),roster.count(root=root)
    writer=xlsx_writer()
    config=json.loads((root/'data/engine/artifact_tool_runtime.json').read_text(encoding='utf-8')) if writer=='artifact' else None
    digest=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
    before=digest(workbook)
    with nullcontext(root/'data/engine/workbook_measurement_staging') as tmp:
        tmp=Path(tmp);plan=tmp/'plan.json';authored=tmp/'authored.xlsx';merged=tmp/'merged.xlsx'
        tmp.mkdir(parents=True,exist_ok=True)
        ranks=json.loads((root/'ranks_python.json').read_text(encoding='utf-8'))
        import openpyxl
        from engine.live_legislative_pillar import nonlegislative_inputs
        source=openpyxl.load_workbook(workbook,data_only=False)
        members=[row[0] for row in source['Rank & Allocation'].iter_rows(min_row=first,max_row=last,values_only=True)]
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
        context=[{'row':i+first,'press':ranks['press_pillar'][n],'influence':influence[n],'demographic':demographic[n]} for i,n in enumerate(members)]
        plan.write_text(json.dumps({'input':str(workbook),'root':str(root),'output':str(authored),'changes':changes,
                                   'views':views,'formats':formats,'context':context,'scores':ranks['score'],'ranks':ranks['rank'],
                                   'first_row':first,'last_row':last,'roster_count':roster_n}),encoding='utf-8')
        env={**os.environ,'ARTIFACT_TOOL_NODE_MODULES':config['node_modules']} if config else None
        helper=root/'fec/stance/workbook_cell_sync.cjs' if writer=='artifact' else AUTHOR_MODULE
        proof_path=Path(str(authored)+'.report.json')
        def valid_proof():
            if not authored.exists() or not proof_path.exists():return False
            try:proof=json.loads(proof_path.read_text(encoding='utf-8'))
            except (ValueError,OSError):return False
            return (proof.get('schema')=='workbook-cell-author-v1' and proof.get('status')=='PASS'
                    and (proof.get('scores_and_ranks_verified')==roster_n if writer=='artifact' else proof.get('engine')=='openpyxl')
                    and proof.get('plan_sha256')==digest(plan) and proof.get('helper_sha256')==digest(helper)
                    and proof.get('output_sha256')==digest(authored))
        if valid_proof():exit_code=None
        elif writer=='artifact':
            try:
                result=subprocess.run([config['node'],str(helper),str(plan)],env=env,timeout=1800)
                exit_code=result.returncode
            except subprocess.TimeoutExpired:exit_code='timeout'
        else:
            author_cell_sync(plan);exit_code=None
        # The bundled Windows runtime can fail during teardown after a successful
        # export. Only a fresh, fully hash-bound full-roster PASS permits promotion.
        if not valid_proof():raise RuntimeError('Artifact workbook sync lacks a verified PASS export')
        parts=merge_cells(workbook,authored,changes,merged)
        if digest(workbook)!=before:raise RuntimeError('Workbook changed during synchronization')
        os.replace(merged,workbook)
    (root/'data/engine/workbook_measurement_sync.json').write_text(json.dumps({
        'schema':'workbook-measurement-sync-v1','status':'PASS','cells':len(changes),
        'before_sha256':before,'after_sha256':digest(workbook),'changed_parts':parts,
        'author_process_exit':exit_code,'author_report':str(proof_path),
        'preservation':'All untargeted ZIP entries retained byte-for-byte; table column names follow their header cells; original styles retained, with category colors reassigned to current labels.'},indent=2),encoding='utf-8')

# Table parts. Excel refuses a table (Workbooks.Open fails with 0x800A03EC) whose tableColumn names differ from its header
# cells, or whose header cell is empty. merge_cells keeps every untargeted part byte-for-byte, so a header edit left the table
# part stale (a re-ordered category list, retired tier names, a title text) and a clear that emptied a header cell broke it.
# The cells are the authority; the table follows them. Only the name attributes of changed columns are rewritten.

def cell_ref(address):
    letters,digits=re.fullmatch(r'([A-Z]+)(\d+)',address).groups();n=0
    for ch in letters:n=n*26+ord(ch)-64
    return n,int(digits)

def col_letters(n):
    letters=''
    while n:n,rem=divmod(n-1,26);letters=chr(65+rem)+letters
    return letters

def header_text(cell,shared):
    """The text of a table header cell; None when the cell is missing or empty. A header can only be text."""
    if cell is None:return None
    kind=cell.get('t')
    if kind=='inlineStr':
        inline=cell.find(Q+'is');text=''.join(t.text or '' for t in inline.iter(Q+'t')) if inline is not None else ''
    else:
        value=cell.find(Q+'v')
        if value is None or not value.text:return None
        if kind=='s':text=shared[int(value.text)]
        elif kind=='str':text=value.text
        else:raise ValueError('Table header cell %s is not text'%cell.get('r'))
    return text or None

def attribute(text):
    for raw,ref in (('&','&amp;'),('<','&lt;'),('>','&gt;'),('"','&quot;'),('\n','&#10;'),('\r','&#13;'),('\t','&#9;')):text=text.replace(raw,ref)
    return text

def write_header(data,rows,address,text):
    """Put a text cell at `address` of sheetData `data`, keeping an existing cell's style; the caller re-sorts the rows of `data`."""
    ri=cell_ref(address)[1];row=rows.get(ri)
    if row is None:row=ET.Element(Q+'row',r=str(ri));data.append(row);rows[ri]=row
    new=ET.Element(Q+'c',r=address,t='inlineStr')
    old=next((c for c in row if c.get('r')==address),None)
    if old is not None:
        if old.get('s') is not None:new.set('s',old.get('s'))
        row.remove(old)
    cell=ET.SubElement(ET.SubElement(new,Q+'is'),Q+'t');cell.text=text
    if text!=text.strip():cell.set('{http://www.w3.org/XML/1998/namespace}space','preserve')
    row.append(new);row[:]=sorted(row,key=lambda c:cell_ref(c.get('r'))[0])

def sync_tables(src,sheet_part,data,rows):
    """Make every table on a worksheet agree with that sheet's header cells (`data` is its merged sheetData, `rows` its row index).

    A tableColumn takes the text of its header cell (Excel keeps names to 255 characters). A header cell that is missing or empty
    takes its tableColumn's current name, which is what Excel itself writes into a blank header. Returns (the header cells written,
    {table part: new bytes}) and lists only the tables that changed; a table that already agrees is not touched."""
    rels=posixpath.join(posixpath.dirname(sheet_part),'_rels',posixpath.basename(sheet_part)+'.rels')
    if rels not in src.namelist():return set(),{}
    shared=[''.join(t.text or '' for t in si.iter(Q+'t')) for si in ET.fromstring(src.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in src.namelist() else []
    filled,tables=set(),{}
    for rel in ET.fromstring(src.read(rels)):
        if not rel.get('Type','').endswith('/table'):continue
        target=rel.get('Target');part=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(sheet_part),target))
        xml=src.read(part);table=ET.fromstring(xml)
        if table.get('headerRowCount')=='0':continue
        first,top=cell_ref(table.get('ref').split(':')[0])
        cells={c.get('r'):c for c in rows.get(top,())}
        current,wanted,missing=[],[],{}
        for offset,column in enumerate(table.find(Q+'tableColumns')):
            address=col_letters(first+offset)+str(top);text=header_text(cells.get(address),shared)
            current.append(column.get('name'))
            if text is None:wanted.append(column.get('name'));missing[address]=column.get('name')
            else:wanted.append(text[:255])
        if wanted==current and not missing:continue
        if len({name.lower() for name in wanted})<len(wanted):raise ValueError('Table %s would get duplicate column names'%table.get('name'))
        pairs=iter(zip(current,wanted))
        def rename(match):
            was,now=next(pairs)
            return match.group(0) if was==now else re.sub(rb'(?<=\s)name="[^"]*"',lambda _:b'name="'+attribute(now).encode('utf-8')+b'"',match.group(0),count=1)
        fixed=re.sub(rb'<tableColumn\b[^>]*>',rename,xml)
        if [c.get('name') for c in ET.fromstring(fixed).find(Q+'tableColumns')]!=wanted:raise ValueError('Table %s: column names were not rewritten'%table.get('name'))
        for address,name in missing.items():write_header(data,rows,address,name);filled.add(address)
        tables[part]=fixed
    return filled,tables
