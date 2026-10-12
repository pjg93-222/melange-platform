from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from stance.workbook_cell_sync import merge_cells,Q

def package(path,sheet,extra=None):
    with ZipFile(path,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Main" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml',sheet)
        for name,value in (extra or {}).items():z.writestr(name,value)

def test_changed_cells_preserve_native_parts_styles_and_untargeted_formulas(tmp_path):
    start='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:C1"/><sheetData>'
    end='</sheetData><sheetProtection sheet="1"/></worksheet>'
    source=tmp_path/'source.xlsx';authored=tmp_path/'authored.xlsx';out=tmp_path/'out.xlsx'
    package(source,start+'<row r="1"><c r="A1" s="9"><v>3</v></c><c r="B1" s="2"><f>A1*2</f><v>6</v></c><c r="C1"><v>7</v></c></row>'+end,
            {'xl/styles.xml':'native styles','xl/charts/chart1.xml':'native chart'})
    package(authored,start+'<row r="1"><c r="A1" s="1" t="s"><v>0</v></c><c r="B1"><v>999</v></c></row><row r="2"><c r="D2"><v>4</v></c></row>'+end,
            {'xl/sharedStrings.xml':'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Champion</t></si></sst>'})
    merge_cells(source,authored,[{'sheet':'Main','cell':a} for a in ('A1','C1','D2')],out)
    with ZipFile(out) as z:
        assert z.read('xl/charts/chart1.xml')==b'native chart'
        assert z.read('xl/styles.xml')==b'native styles'
        tree=ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
        cells={c.get('r'):c for c in tree.iter(Q+'c')}
        assert cells['A1'].get('s')=='9'
        assert cells['A1'].find(Q+'is').find(Q+'t').text=='Champion'
        assert cells['B1'].find(Q+'f').text=='A1*2'
        assert cells['B1'].find(Q+'v').text=='6'
        assert cells['C1'].find(Q+'v') is None
        assert cells['D2'].find(Q+'v').text=='4'
        assert tree.find(Q+'dimension').get('ref')=='A1:D2'
        assert tree.find(Q+'sheetProtection').get('sheet')=='1'
