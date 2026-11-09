from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E
import hashlib, json

ROOT = Path(__file__).resolve().parent
REF = Path('C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026/submission-template/Copy of Apart Research hackathon submission template.docx')
NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
def xml(node): return E.tostring(node, encoding='unicode') if node is not None else None
def txt(node): return ''.join(node.xpath('.//w:t/text()', namespaces=NS))
def prop(node, tag): return xml(node.find('w:'+tag, NS))
with ZipFile(REF) as z:
    document = E.fromstring(z.read('word/document.xml'))
    body = document.find('w:body', NS)
    items = []
    for i, node in enumerate(body):
        row = {'index': i, 'tag': E.QName(node).localname, 'text': txt(node)}
        if row['tag'] == 'p':
            row['pPr'] = prop(node, 'pPr')
            row['runs'] = [{'text':txt(r), 'rPr':prop(r, 'rPr')} for r in node.findall('w:r', NS)]
        elif row['tag'] == 'tbl':
            row['properties'] = prop(node, 'tblPr')
            row['grid'] = prop(node, 'tblGrid')
            row['cells'] = [{'path':document.getroottree().getpath(c), 'text':txt(c), 'tcPr':prop(c, 'tcPr')} for c in node.xpath('.//w:tc', namespaces=NS)]
        items.append(row)
    styles=E.fromstring(z.read('word/styles.xml'))
    evidence = {'reference': str(REF), 'sha256':hashlib.sha256(REF.read_bytes()).hexdigest(),
                'parts':{n:{'bytes':len(z.read(n)), 'sha256':hashlib.sha256(z.read(n)).hexdigest()} for n in z.namelist()},
                'body':items, 'styles':{s.get('{'+NS['w']+'}styleId'):xml(s) for s in styles.findall('w:style',NS)},
                'sections':[xml(s) for s in document.xpath('.//w:sectPr',namespaces=NS)],
                'fields':document.xpath('//w:instrText/text()',namespaces=NS),
                'controls':len(document.xpath('//w:sdt',namespaces=NS)),
                'drawings':len(document.xpath('//w:drawing|//w:pict',namespaces=NS))}
    for name in z.namelist():
        if name in ('word/footnotes.xml','word/_rels/document.xml.rels','word/settings.xml') or name.startswith(('word/header','word/footer')):
            evidence[name]=z.read(name).decode('utf-8')
    (ROOT/'reference-evidence.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'sections':evidence['sections'], 'parts':list(evidence['parts']), 'fields':evidence['fields'],
                      'controls':evidence['controls'], 'drawings':evidence['drawings'],
                      'body':[{'index':r['index'],'tag':r['tag'],'text':r['text'][:95]} for r in items],
                      'styles':list(evidence['styles'])},indent=2,ensure_ascii=False))
