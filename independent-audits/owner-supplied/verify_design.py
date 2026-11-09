"""Independent text/package/PDF checks after all-page visual review of the redesign."""
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E
from collections import Counter
from datetime import datetime, timezone
import hashlib, json, re, unicodedata
import pdfplumber
from pypdf import PdfReader

HERE=Path(__file__).resolve().parent
CLAUDE=Path('C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026')
REF=CLAUDE/'submission-template/Copy of Apart Research hackathon submission template.docx'
SOURCE=CLAUDE/'paper/final-e1/manuscript.md'
PDF=CLAUDE/'paper/final-e1/e1-submission-v4.pdf'
DOCX=PDF.with_suffix('.docx')
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def tight(s):return re.sub(r'\s+','',unicodedata.normalize('NFKC',s))
def plain(s):
    s=re.sub(r'<!--.*?-->','',s,flags=re.S)
    s=re.sub(r'!\[[^\]]*\]\([^)]*\)','',s)
    s=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',s)
    s=re.sub(r'^#{1,6}\s+','',s,flags=re.M)
    return s.replace('**','').replace('*','').replace('`','')
def txt(node):return ''.join(node.xpath('.//w:t/text()',namespaces=NS))
markdown=SOURCE.read_text(encoding='utf-8')
assert sha(SOURCE)=='186a3dbd2525fad70c00cb95bdfca708ec8a1b44315a6f108bf2bed2248e7080'
assert sha(REF)=='f6cb61cbfe9d76180d1b29ab51d3189b8926f30ba4f586fdf6d53ab6892fe909'
body_source=markdown[markdown.index('## 1. Introduction'):]
lines=[];expected_cells=[]
for line in body_source.splitlines():
    if line.startswith('|'):
        cells=[v.strip() for v in line.strip().strip('|').split('|')]
        if all(re.fullmatch(r':?-+:?',c) for c in cells):continue
        expected_cells.extend(plain(c) for c in cells)
        lines.append(' '.join(cells))
    else:lines.append(line)
expected_body=tight(plain('\n'.join(lines)))
with ZipFile(REF) as zr,ZipFile(DOCX) as zd:
    root=E.fromstring(zd.read('word/document.xml')); body=root.find('w:body',NS)
    children=list(body);start=next(i for i,n in enumerate(children) if txt(n)=='1. Introduction')
    actual_body=tight(' '.join(txt(n) for n in children[start:]))
    assert actual_body==expected_body,'Complete main/appendix visible text differs'
    original_names=zr.namelist();changed=[n for n in original_names if zr.read(n)!=zd.read(n)]
    assert set(changed)=={'word/document.xml','word/_rels/document.xml.rels','word/settings.xml','[Content_Types].xml'}
    preserved=[n for n in original_names if n not in changed]
    sections=root.xpath('//w:sectPr',namespaces=NS);assert len(sections)==1
    section=sections[0]
    for key in ('top','bottom','left','right'):assert section.find('w:pgMar',NS).get('{'+NS['w']+'}'+key)=='1440'
    tables=body.findall('w:tbl',NS)
    actual_cells=[plain(txt(c)) for table in tables[1:] for c in table.xpath('.//w:tc',namespaces=NS)]
    assert actual_cells==expected_cells and len(tables)==5
    assert len(root.xpath('//w:drawing',namespaces=NS))==2
    assert len(root.xpath('//w:footnoteReference',namespaces=NS))==1
    abstract=re.search(r'## Abstract\s*\n(.*?)\n## 1\.',markdown,re.S).group(1).strip()
    assert tight(abstract) in tight(txt(tables[0]))
    figure_records=[]
    for i,name in enumerate(('study2_false_accusations.png','study2_template_profile.png'),1):
        source=SOURCE.parent/'figures'/name
        assert zd.read(f'word/media/e1_figure_{i}.png')==source.read_bytes()
        figure_records.append({'path':str(source),'sha256':sha(source)})
    assert not re.search(r'Author name \d|PROJECT TITLE|Summarize your project|How to use this template',txt(root))
pages=[];problems=[];body_pdf=[];section_pages=[]
headings=[plain(x) for x in re.findall(r'^## (.+)$',markdown,re.M)]
with pdfplumber.open(PDF) as parsed:
    for i,page in enumerate(parsed.pages,1):
        text=page.extract_text() or ''
        content=page.crop((0,0,page.width,730)).extract_text() or ''
        body_pdf.append(content)
        bad=[c for c in page.chars if c.get('text') in ('\ufffd','\u25a0')]
        outside=[c for c in page.chars if c['x0']<-1 or c['x1']>page.width+1 or c['top']<-1 or c['bottom']>page.height+1]
        # Word emits invisible trailing space glyph boxes beyond the justified
        # line end. The ink/layout check excludes whitespace, never letters.
        frame=[c for c in page.chars if c.get('text','').strip() and c['top']<730 and float(c.get('size',0))>=9 and (c['x0']<70 or c['x1']>542 or c['bottom']>722)]
        if bad or outside or frame:problems.append({'page':i,'bad_glyphs':len(bad),'off_page':len(outside),'outside_body_frame':len(frame)})
        if i>1:assert text.strip().splitlines()[-1]==str(i),'Page number incorrect'
        assert page.width==612 and page.height==792
        for heading in headings:
            if tight(heading) in tight(content) and not any(r['heading']==heading for r in section_pages):section_pages.append({'heading':heading,'page':i})
        pages.append({'page':i,'width':page.width,'height':page.height,'words':len(text.split()),'images':len(page.images),'text':text,'font_sizes':dict(Counter(round(float(c['size']),1) for c in page.chars))})
full=tight('\n'.join(body_pdf))
prose_checks=[]
for chunk in re.split(r'\n\s*\n',body_source):
    if chunk.strip().startswith('|') or chunk.strip().startswith('<!--') or chunk.strip().startswith('!['):continue
    expected=tight(plain(chunk))
    if expected:prose_checks.append({'prefix':plain(chunk)[:90],'pass':expected in full})
assert all(row['pass'] for row in prose_checks),[r for r in prose_checks if not r['pass']]
assert tight(abstract) in full
ref_page=next(r['page'] for r in section_pages if r['heading']=='References')
assert ref_page-1<=8 and 150<=len(abstract.split())<=250 and PDF.stat().st_size<25_000_000
assert not problems,problems
pngs=sorted(HERE.glob('e1-template-v3-page-*.png'));assert len(pngs)==len(pages)==12
build=json.loads((HERE/'e1-template-v3.build.json').read_text(encoding='utf-8'))
assert sha(DOCX)==build['docx_sha256']
assert sha(PDF)==sha(HERE/'e1-template-v3.pdf')
qa={'schema':'e1-word-template-build-qa-v1','created_at_utc':datetime.now(timezone.utc).isoformat(),
    'pdf':str(PDF),'pdf_sha256':sha(PDF),'source_sha256':sha(SOURCE),'docx_sha256':sha(DOCX),
    'reference_docx_sha256':sha(REF),'draft':False,'page_count':len(pages),'main_text_end_page':ref_page-1,
    'abstract_word_count':len(abstract.split()),'pages':pages,'section_pages':section_pages,'figure_sources':figure_records,
    'tables':4,'complete_OOXML_visible_body_matches_source':True,'table_cells_checked':len(actual_cells),
    'PDF_prose_checks':prose_checks,'PDF_abstract_preserved':True,'preserve_only_parts_byte_identical':preserved,
    'changed_template_parts':changed,'source_and_reference_unchanged':True,'rendered_pages':list(map(str,pngs)),
    'machine_layout_pass':True,'problems':problems,'visual_inspection_required':True,
    'renderer':'Microsoft Word 2024 read-only export; bundled Poppler 110dpi PNGs',
    'packaged_renderer_limit':'render_docx.py attempted; LibreOffice unavailable in Windows bundle. Native Word used instead.',
    'presentation_only_difference':'Sprint attribution uses the exact original template footnote; whitespace/punctuation differ from its former inline placement.',
    'source_content_changed':False}
visual={'schema':'e1-all-page-visual-review-v1','source_sha256':sha(SOURCE),'pdf_sha256':sha(PDF),
        'page_count':12,'main_text_end_page':7,'all_pages_visually_inspected':True,'visual_layout_pass':True,
        'rendered_pages':[{'path':str(p),'sha256':sha(p)} for p in pngs], 'findings':[],
        'notes':['Primary inspected all12 final pages and all5 Word template reference pages.',
                 'Actual retained template controls opening title/author/abstract component and one-inch page geometry.',
                 'Clean section starts; opening paragraphs stay whole; figures/tables stay with context/captions.',
                 'Main pages1-7; references8; appendices/disclosure9-12. Table header words fit without clipping.',
                 'Original11 preserve-only OOXML parts including embedded fonts/footnote/styles remain byte-identical.']}
for suffix,data in (('.qa.json',qa),('.visual-review.json',visual)):
    with (HERE/('e1-submission-v4'+suffix)).open('x',encoding='utf-8') as f:json.dump(data,f,indent=2,ensure_ascii=False);f.write('\n')
print(json.dumps({'pass':True,'pdf_sha256':qa['pdf_sha256'],'docx_sha256':qa['docx_sha256'],'pages':len(pages),'main_pages':7,
                  'PDF_prose_blocks_checked':len(prose_checks),'table_cells_checked':len(actual_cells),'source_unchanged':True}))
