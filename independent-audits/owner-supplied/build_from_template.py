"""Populate the retained Apart DOCX without rewriting preserve-only package parts."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from copy import deepcopy
from lxml import etree as E
from PIL import Image
import argparse, hashlib, json, re

HERE = Path(__file__).resolve().parent
CLAUDE = Path('C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026')
REFERENCE = CLAUDE / 'submission-template/Copy of Apart Research hackathon submission template.docx'
SOURCE = CLAUDE / 'paper/final-e1/manuscript.md'
REFERENCE_SHA = 'f6cb61cbfe9d76180d1b29ab51d3189b8926f30ba4f586fdf6d53ab6892fe909'
SOURCE_SHA = '186a3dbd2525fad70c00cb95bdfca708ec8a1b44315a6f108bf2bed2248e7080'
NS = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
      'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
      'pic':'http://schemas.openxmlformats.org/drawingml/2006/picture'}
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
FONT = 'Old Standard TT'

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def q(name):
    prefix, local = name.split(':')
    return '{'+NS[prefix]+'}'+local
def el(name, attrs=None, parent=None):
    node = E.Element(q(name))
    for key,value in (attrs or {}).items(): node.set(q(key) if ':' in key else key, str(value))
    if parent is not None: parent.append(node)
    return node
def xml(root): return E.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
def normalized(s): return ' '.join(s.split())
def text_of(node):
    parts=[]
    for n in node.iter():
        if n.tag==q('w:t'): parts.append(n.text or '')
        elif n.tag in (q('w:br'), q('w:tab')): parts.append(' ')
    return ''.join(parts)

def parse_blocks(text):
    lines=text.splitlines(); blocks=[]; i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line or line.startswith('<!--'): i+=1; continue
        match=re.match(r'^(#{1,6})\s+(.*)',line)
        if match:
            blocks.append({'kind':'heading','level':len(match[1]),'text':match[2]}); i+=1; continue
        match=re.fullmatch(r'!\[([^\]]*)\]\(([^)]+)\)',line)
        if match:
            blocks.append({'kind':'image','alt':match[1],'src':match[2]}); i+=1; continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                row=[s.strip() for s in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?',s) for s in row): rows.append(row)
                i+=1
            assert len({len(r) for r in rows})==1
            blocks.append({'kind':'table','rows':rows}); continue
        match=re.match(r'^(\d+)\.\s+(.*)',line)
        if match:
            blocks.append({'kind':'list','number':int(match[1]),'text':match[2]}); i+=1; continue
        paragraph=[line]; i+=1
        while i<len(lines) and lines[i].strip() and not lines[i].strip().startswith('<!--'):
            paragraph.append(lines[i].strip()); i+=1
        blocks.append({'kind':'paragraph','text':' '.join(paragraph)})
    return blocks

class Builder:
    def __init__(self, reference, size=11, after=6):
        self.parts={n:reference.read(n) for n in reference.namelist()}
        self.document=E.fromstring(self.parts['word/document.xml'])
        self.body=self.document.find('w:body',NS)
        self.front=deepcopy(self.body.find('w:tbl',NS))
        self.section=deepcopy(self.body.find('w:sectPr',NS))
        self.relationships=E.fromstring(self.parts['word/_rels/document.xml.rels'])
        self.content_types=E.fromstring(self.parts['[Content_Types].xml'])
        self.size=size; self.after=after; self.images=[]; self.blocks=[]
        self.next_id=1+max(int(n.get('Id')[3:]) for n in self.relationships if re.fullmatch(r'rId\d+',n.get('Id','')))
        self.media_count=0

    def relation(self, kind, target, external=False):
        rid=f'rId{self.next_id}'; self.next_id+=1
        attrs={'Id':rid,'Type':'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'+kind,'Target':target}
        if external:attrs['TargetMode']='External'
        E.SubElement(self.relationships,'{'+REL+'}Relationship',attrs)
        return rid

    def run(self,parent,text,size=None,bold=False,italic=False,code=False,color='000000'):
        r=el('w:r',parent=parent); rp=el('w:rPr',parent=r)
        font='Consolas' if code else FONT
        el('w:rFonts',{'w:ascii':font,'w:hAnsi':font,'w:cs':font,'w:eastAsia':font},rp)
        el('w:sz',{'w:val':round(2*(size or self.size))},rp)
        el('w:szCs',{'w:val':round(2*(size or self.size))},rp)
        el('w:color',{'w:val':color},rp)
        if bold:el('w:b',parent=rp)
        if italic:el('w:i',parent=rp)
        if text:
            t=el('w:t',parent=r); t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');t.text=text
        return r

    def inline(self,parent,text,size=None,bold=False,italic=False):
        pattern=r'(\[[^\]]+\]\([^\s)]+\)|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)'
        for token in re.split(pattern,text):
            if not token:continue
            link=re.fullmatch(r'\[([^\]]+)\]\(([^)]+)\)',token)
            if link:
                h=el('w:hyperlink',{'r:id':self.relation('hyperlink',link[2],True)},parent)
                self.run(h,link[1],size,bold,italic,color='1F4B64')
            elif token.startswith('**') and token.endswith('**'):
                self.inline(parent,token[2:-2],size,True,italic)
            elif token.startswith('*') and token.endswith('*'):
                self.inline(parent,token[1:-1],size,bold,True)
            elif token.startswith('`'):
                self.run(parent,token[1:-1],min(size or self.size,10),bold,italic,True)
            else:self.run(parent,token,size,bold,italic)

    def paragraph(self,text='',style='Normal',size=None,bold=False,italic=False,align='both',before=0,after=None,keep=False,pagebreak=False,indent=None,line=276):
        p=el('w:p'); pp=el('w:pPr',parent=p)
        el('w:pStyle',{'w:val':style},pp)
        el('w:widowControl',parent=pp)
        if keep:el('w:keepNext',parent=pp)
        if pagebreak:el('w:pageBreakBefore',parent=pp)
        el('w:spacing',{'w:before':round(before*20),'w:after':round((self.after if after is None else after)*20),'w:line':line,'w:lineRule':'auto'},pp)
        el('w:jc',{'w:val':align},pp)
        if indent:el('w:ind',indent,pp)
        self.inline(p,text,size,bold,italic)
        return p

    def opening(self,title,abstract):
        rows=self.front.findall('w:tr',NS)
        first=rows[0].find('w:tc',NS); foot=deepcopy(first.xpath('.//w:r[w:footnoteReference]',namespaces=NS)[0])
        for c in list(first):
            if c.tag!=q('w:tcPr'):first.remove(c)
        p=self.paragraph('',style='Title',size=20,bold=True,align='center',before=18,after=18,keep=True,line=240)
        pieces=title.split(' Exculpatory ',1)
        self.run(p,pieces[0],20,True)
        if len(pieces)==2:
            el('w:br',parent=el('w:r',parent=p));self.run(p,'Exculpatory '+pieces[1],20,True)
        p.append(foot);first.append(p)
        second=rows[1].find('w:tc',NS)
        for c in list(second):
            if c.tag!=q('w:tcPr'):second.remove(c)
        second.append(self.paragraph('Ebin Babu Thomas',size=12,bold=True,align='center',before=23,after=3,keep=True))
        second.append(self.paragraph('Independent AI Engineer & Researcher',size=11,align='center',after=14,keep=True))
        p=self.paragraph('**With**',size=11,align='center',after=0,keep=True)
        el('w:br',parent=el('w:r',parent=p));self.run(p,'Apart Research',11);second.append(p)
        second.append(self.paragraph('Abstract',size=14,bold=True,align='center',before=24,after=12,keep=True))
        second.append(self.paragraph(abstract,size=11,align='both',after=12,indent={'w:left':'1080','w:right':'1080'},line=276))
        for row in rows:
            rp=row.find('w:trPr',NS)
            if rp is None:rp=el('w:trPr');row.insert(0,rp)
            cs=rp.find('w:cantSplit',NS)
            if cs is None:el('w:cantSplit',parent=rp)
            else:cs.set(q('w:val'),'1')
        self.body.append(self.front)

    def image(self,block):
        path=(SOURCE.parent/block['src']).resolve()
        assert path.is_relative_to(SOURCE.parent)
        self.media_count+=1; name=f'e1_figure_{self.media_count}.png'
        data=path.read_bytes(); self.parts['word/media/'+name]=data
        rid=self.relation('image','media/'+name)
        with Image.open(path) as im:w,h=im.size
        cx=5943600;cy=round(cx*h/w)
        p=self.paragraph(align='center',after=5,before=7,keep=True)
        drawing=el('w:drawing',parent=el('w:r',parent=p))
        inline=el('wp:inline',{'distT':'0','distB':'0','distL':'0','distR':'0'},drawing)
        el('wp:extent',{'cx':cx,'cy':cy},inline)
        el('wp:docPr',{'id':self.media_count,'name':block['alt'],'descr':block['alt']},inline)
        g=el('a:graphic',parent=inline);gd=el('a:graphicData',{'uri':NS['pic']},g);pic=el('pic:pic',parent=gd)
        nv=el('pic:nvPicPr',parent=pic);el('pic:cNvPr',{'id':'0','name':name},nv);el('pic:cNvPicPr',parent=nv)
        fill=el('pic:blipFill',parent=pic);el('a:blip',{'r:embed':rid},fill);el('a:fillRect',parent=el('a:stretch',parent=fill))
        sp=el('pic:spPr',parent=pic);xf=el('a:xfrm',parent=sp);el('a:off',{'x':'0','y':'0'},xf);el('a:ext',{'cx':cx,'cy':cy},xf)
        el('a:avLst',parent=el('a:prstGeom',{'prst':'rect'},sp))
        self.body.append(p)
        self.images.append({'source':str(path),'member':'word/media/'+name,'sha256':hashlib.sha256(data).hexdigest(),'dimensions_emu':[cx,cy]})

    def table(self,rows):
        n=len(rows[0]); width_map={5:[144,81,81,81,81],6:[147,60,65,57,76,63],7:[126,58,60,65,55,57,47]}
        widths=width_map[n]
        if rows[0][1]=='Forecast' and n==5:widths=[178,68,65,88,69]
        assert sum(widths)==468
        t=el('w:tbl');pr=el('w:tblPr',parent=t)
        el('w:tblStyle',{'w:val':'Table1'},pr);el('w:tblW',{'w:w':'9360','w:type':'dxa'},pr);el('w:jc',{'w:val':'center'},pr)
        borders=el('w:tblBorders',parent=pr)
        for edge in ('top','left','bottom','right','insideH','insideV'):
            el('w:'+edge,{'w:val':'single','w:sz':'4','w:color':'D9D9D9'},borders)
        el('w:tblLayout',{'w:type':'fixed'},pr)
        margins=el('w:tblCellMar',parent=pr)
        for edge in ('top','left','bottom','right'):el('w:'+edge,{'w:w':'100','w:type':'dxa'},margins)
        grid=el('w:tblGrid',parent=t)
        for width in widths:el('w:gridCol',{'w:w':width*20},grid)
        for i,row in enumerate(rows):
            tr=el('w:tr',parent=t);trp=el('w:trPr',parent=tr);el('w:cantSplit',parent=trp)
            if i==0:el('w:tblHeader',parent=trp)
            for j,cell in enumerate(row):
                tc=el('w:tc',parent=tr);tcp=el('w:tcPr',parent=tc)
                el('w:tcW',{'w:w':widths[j]*20,'w:type':'dxa'},tcp);el('w:vAlign',{'w:val':'center'},tcp)
                el('w:shd',{'w:val':'clear','w:fill':'ECEEEF' if i==0 else ('F8F8F8' if i%2==0 else 'FFFFFF')},tcp)
                tc.append(self.paragraph(cell,size=10,bold=(i==0),align='left' if j==0 else 'center',after=0,keep=True,line=240))
        self.body.append(t)

    def populate(self,blocks):
        title=blocks[0]['text'];abstract_index=next(i for i,b in enumerate(blocks) if b['kind']=='heading' and b['text']=='Abstract')
        abstract=blocks[abstract_index+1]['text']
        assert 150<=len(abstract.split())<=250
        start=next(i for i,b in enumerate(blocks) if b['kind']=='heading' and b['text']=='1. Introduction')
        for c in list(self.body):self.body.remove(c)
        self.opening(title,abstract)
        appendix=False;references=False
        article_blocks=blocks[start:]
        for block_index,block in enumerate(article_blocks):
            kind=block['kind'];before=len(self.body)
            if kind=='heading':
                heading=block['text'];h2=block['level']==2
                br=heading in ('1. Introduction','3. Methods','References') or (heading.startswith('Appendix') and not appendix)
                if heading.startswith('Appendix'):appendix=True;references=False
                if heading=='References':references=True
                p=self.paragraph(heading,style='Heading2' if h2 else 'Heading3',size=14 if h2 else 12.5,bold=True,align='left',before=12 if h2 else 9,after=5,keep=True,pagebreak=br,line=276)
                self.body.append(p)
            elif kind=='image':self.image(block)
            elif kind=='table':self.table(block['rows'])
            elif kind=='list':
                p=self.paragraph(f"{block['number']}. {block['text']}",size=10.5 if references else self.size,align='left',after=8 if references else 4,indent={'w:left':'360','w:hanging':'360'})
                self.body.append(p)
            else:
                caption=bool(re.match(r'^\*?(Figure|Table)\s+(?:[A-Z]\d+|\d+)[.:]',block['text']))
                next_kind=article_blocks[block_index+1]['kind'] if block_index+1<len(article_blocks) else None
                follows_heading=block_index>0 and article_blocks[block_index-1]['kind']=='heading'
                keep=block['text']=='**Our main contributions are:**' or (not caption and next_kind in ('table','image'))
                p=self.paragraph(block['text'],size=10 if caption else self.size,align='left' if caption else 'both',before=6 if caption else 0,after=10 if caption else self.after,keep=keep)
                if (follows_heading and not caption) or block['text'].startswith('In this evaluation there were no accusations'):
                    el('w:keepLines',parent=p.find('w:pPr',NS))
                self.body.append(p)
            created=list(self.body)[before:]
            if kind not in ('image','table'):
                expected=plain(block['text'])
                actual=normalized(' '.join(text_of(c) for c in created))
                if kind=='list':expected=f"{block['number']}. "+expected
                assert actual==expected,(kind,actual,expected)
            elif kind=='table':
                actual=[normalized(text_of(c)) for c in created[0].xpath('.//w:tc',namespaces=NS)]
                assert actual==[plain(c) for row in block['rows'] for c in row]
            self.blocks.append({'kind':kind,'text':block.get('text'), 'body_start_index':before,'normalized_text_verified':kind!='image'})
        footer=E.Element(q('w:ftr'),nsmap={'w':NS['w']})
        p=self.paragraph(align='center',after=0)
        for tag,attrs,value in [('w:fldChar',{'w:fldCharType':'begin'},None),('w:instrText',{},' PAGE '),('w:fldChar',{'w:fldCharType':'separate'},None),('w:t',{},'1'),('w:fldChar',{'w:fldCharType':'end'},None)]:
            r=self.run(p,'',9,color='666666');n=el(tag,attrs,r)
            if value:n.text=value
        footer.append(p);self.parts['word/footerE1.xml']=xml(footer)
        footerid=self.relation('footer','footerE1.xml')
        self.section.insert(0,el('w:footerReference',{'w:type':'default','r:id':footerid}))
        el('w:titlePg',parent=self.section)
        self.body.append(self.section)
        if not self.content_types.xpath('./ct:Default[@Extension="png"]',namespaces={'ct':CT}):E.SubElement(self.content_types,'{'+CT+'}Default',Extension='png',ContentType='image/png')
        E.SubElement(self.content_types,'{'+CT+'}Override',PartName='/word/footerE1.xml',ContentType='application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml')
        settings=E.fromstring(self.parts['word/settings.xml'])
        update=settings.find('w:updateFields',NS)
        if update is None:update=el('w:updateFields',parent=settings)
        update.set(q('w:val'),'true')
        self.parts.update({'word/document.xml':xml(self.document), 'word/_rels/document.xml.rels':xml(self.relationships),
                           '[Content_Types].xml':xml(self.content_types),'word/settings.xml':xml(settings)})
        return len(abstract.split())

def plain(text):
    text=re.sub(r'\[([^\]]+)\]\(([^)]+)\)',r'\1',text)
    return normalized(text.replace('**','').replace('*','').replace('`',''))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--body-size',type=float,default=11);parser.add_argument('--paragraph-after',type=float,default=6)
    args=parser.parse_args();assert sha(REFERENCE)==REFERENCE_SHA and sha(SOURCE)==SOURCE_SHA
    assert not args.output.exists()
    blocks=parse_blocks(SOURCE.read_text(encoding='utf-8'))
    with ZipFile(REFERENCE) as z:
        b=Builder(z,args.body_size,args.paragraph_after);words=b.populate(blocks)
        changed=[];preserved=[]
        for name in z.namelist():
            (preserved if b.parts[name]==z.read(name) else changed).append(name)
        assert set(changed)=={'word/document.xml','word/_rels/document.xml.rels','[Content_Types].xml','word/settings.xml'}
        with ZipFile(args.output,'x',compression=ZIP_DEFLATED) as out:
            for n,data in b.parts.items():out.writestr(n,data)
    assert sha(REFERENCE)==REFERENCE_SHA and sha(SOURCE)==SOURCE_SHA
    receipt={'schema':'e1-template-derived-docx-v1','reference_sha256':REFERENCE_SHA,'source_sha256':SOURCE_SHA,
             'docx':str(args.output.resolve()),'docx_sha256':sha(args.output),'abstract_words':words,
             'body_font_size':args.body_size,'paragraph_after_points':args.paragraph_after,
             'preserved_parts':preserved,'changed_reference_parts':changed,
             'added_parts':[n for n in b.parts if n not in preserved+changed],
             'original_footnotes_fonts_styles_numbering_unchanged':True,'blocks':b.blocks,'images':b.images,
             'source_and_reference_unchanged':True,'renderer_required':'Microsoft Word read-only export plus Poppler all-page review'}
    args.output.with_suffix('.build.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('docx','docx_sha256','abstract_words','preserved_parts','changed_reference_parts','added_parts')}))

if __name__=='__main__':main()
