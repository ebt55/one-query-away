"""ReportLab Markdown report builder; offline, never edits Markdown or science.

Run with bundled Python; pure Python-Markdown may be supplied with
--markdown-site PATH. All-page Poppler renders and machine QA are mandatory
unless --no-render is explicitly requested for a local unit check.
"""
from __future__ import annotations
import argparse
from collections import Counter
from html import escape
from html.parser import HTMLParser
import hashlib
import importlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape as xml_escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Spacer, PageBreak, Table, TableStyle, Image, ListFlowable, ListItem, Preformatted,
    KeepTogether)
from reportlab.lib.utils import ImageReader
import pdfplumber
from pypdf import PdfReader

PAGE_W,PAGE_H=letter
LEFT,RIGHT,TOP,BOTTOM=54,54,48,49
WIDTH=PAGE_W-LEFT-RIGHT
DASHES=str.maketrans({c:"-" for c in "‐‑‒–—−"}|{"\u00a0":" ","\u202f":" ","\u2009":" "})
CLAUDE_ROOT=Path("C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026")


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def normalize(text):return text.translate(DASHES)
def load_markdown(site=None):
    try:return importlib.import_module("markdown")
    except ImportError:
        candidates=[Path(site)] if site else [Path(__file__).resolve().parents[3]/"experiments/.venv/Lib/site-packages",CLAUDE_ROOT/"experiments/.venv/Lib/site-packages"]
        if not site:
            config=CLAUDE_ROOT/"experiments/.venv/pyvenv.cfg"
            if config.is_file():
                home=re.search(r"^home\s*=\s*(.+)$",config.read_text(),re.M)
                if home:candidates.append(Path(home.group(1).strip())/"Lib/site-packages")
        for candidate in candidates:
            if (candidate/"markdown/__init__.py").is_file():
                sys.path.append(str(candidate));return importlib.import_module("markdown")
        raise RuntimeError("Python-Markdown missing; supply existing --markdown-site (no installation/download is attempted)")
def fonts(font_dir=None):
    base=Path(font_dir or "C:/Windows/Fonts")
    family={"E1Serif":"DejaVuSerif.ttf","E1Serif-Bold":"DejaVuSerif-Bold.ttf","E1Serif-Italic":"DejaVuSerif-Italic.ttf","E1Serif-BoldItalic":"DejaVuSerif-BoldItalic.ttf","E1Mono":"DejaVuSansMono.ttf"}
    for name,file in family.items():pdfmetrics.registerFont(TTFont(name,str(base/file)))
    pdfmetrics.registerFontFamily("E1Serif",normal="E1Serif",bold="E1Serif-Bold",italic="E1Serif-Italic",boldItalic="E1Serif-BoldItalic")
    return {name:digest(base/file) for name,file in family.items()}


class Node:
    def __init__(self,tag="root",attrs=()):self.tag=tag;self.attrs=dict(attrs);self.children=[]
    def text(self):return "".join(c.text() if isinstance(c,Node) else c for c in self.children)
class Tree(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.root=Node();self.stack=[self.root]
    def handle_starttag(self,tag,attrs):
        n=Node(tag,attrs);self.stack[-1].children.append(n)
        if tag not in ("img","br","hr","meta","input"):self.stack.append(n)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs)
        if self.stack[-1].tag==tag:self.stack.pop()
    def handle_endtag(self,tag):
        for k in range(len(self.stack)-1,0,-1):
            if self.stack[k].tag==tag:self.stack=self.stack[:k];return
    def handle_data(self,data):self.stack[-1].children.append(data)


def inline(value):
    if isinstance(value,str):return xml_escape(normalize(value))
    inner="".join(inline(c) for c in value.children);tag=value.tag
    if tag in ("b","strong"):return "<b>"+inner+"</b>"
    if tag in ("em","i"):return "<i>"+inner+"</i>"
    if tag in ("sup","sub"):return f"<{tag}>{inner}</{tag}>"
    if tag=="code":return '<font name="E1Mono" size="8.8">'+inner+"</font>"
    if tag=="br":return "<br/>"
    if tag=="a":
        href=value.attrs.get("href","")
        if href.startswith(("https://","http://","mailto:")):return '<a href="'+escape(href,quote=True)+'" color="#18465c">'+inner+"</a>"
        return inner
    if tag=="img":return ""
    return inner


def styles():
    body=ParagraphStyle("Body",fontName="E1Serif",fontSize=10.5,leading=13.4,alignment=TA_JUSTIFY,spaceAfter=6.2,splitLongWords=True,allowWidows=0,allowOrphans=0)
    s={"body":body}
    def add(name,**kwargs):s[name]=ParagraphStyle(name,parent=body,**kwargs)
    add("title",fontName="E1Serif-Bold",fontSize=18,leading=21.5,alignment=TA_CENTER,spaceBefore=0,spaceAfter=10,keepWithNext=True)
    add("h2",fontName="E1Serif-Bold",fontSize=12.4,leading=15,alignment=TA_LEFT,spaceBefore=9.5,spaceAfter=5,keepWithNext=True)
    add("h3",fontName="E1Serif-Bold",fontSize=10.8,leading=13.5,alignment=TA_LEFT,spaceBefore=6,spaceAfter=3.5,keepWithNext=True)
    add("caption",fontSize=9.2,leading=11.5,alignment=TA_LEFT,spaceAfter=8.5)
    add("table",fontSize=9.2,leading=11.4,alignment=TA_LEFT,spaceAfter=0)
    add("list",alignment=TA_LEFT,spaceAfter=2.5)
    add("quote",fontSize=10,leading=12.8,alignment=TA_LEFT,leftIndent=11,rightIndent=9,borderColor=colors.HexColor("#b8c6cc"),borderWidth=.5,borderPadding=6,backColor=colors.HexColor("#f4f6f7"),spaceBefore=4,spaceAfter=8)
    add("code",fontName="E1Mono",fontSize=8.3,leading=10,alignment=TA_LEFT,spaceAfter=6)
    return s


class Report(BaseDocTemplate):
    def __init__(self,path,draft=False,**kwargs):
        super().__init__(str(path),pagesize=letter,leftMargin=LEFT,rightMargin=RIGHT,topMargin=TOP,bottomMargin=BOTTOM,**kwargs)
        self.draft=draft;self.section_pages=[]
        self.addPageTemplates(PageTemplate("Report",frames=[Frame(LEFT,BOTTOM,WIDTH,PAGE_H-TOP-BOTTOM,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=self.decorate))
    def decorate(self,canvas,doc):
        canvas.saveState();canvas.setFont("E1Serif",7.5);canvas.setFillColor(colors.HexColor("#55616a"))
        canvas.drawString(LEFT,29,"Swarm Alignment Hackathon | November 2026")
        canvas.drawRightString(PAGE_W-RIGHT,29,str(doc.page))
        if self.draft:
            canvas.setFont("E1Serif-Bold",8);canvas.setFillColor(colors.HexColor("#9a3a28"))
            canvas.drawCentredString(PAGE_W/2,PAGE_H-24,"WORKING DRAFT - Study 4 pending")
        canvas.restoreState()
    def afterFlowable(self,flowable):
        if isinstance(flowable,Paragraph) and flowable.style.name in ("h2","title"):
            self.section_pages.append({"heading":flowable.getPlainText(),"page":self.page})


class Converter:
    def __init__(self,base):self.base=Path(base);self.s=styles();self.images=[];self.tables=0;self.headings=[];self.appendix_seen=False
    def para(self,node,style="body"):return Paragraph(inline(node),self.s[style])
    def image(self,node):
        src=node.attrs.get("src","")
        if re.match(r"[a-z]+://",src,re.I):raise ValueError("image must be an existing local figure; no remote media/download")
        path=(self.base/src).resolve()
        if not path.is_file():raise ValueError(f"missing local image: {path}")
        w,h=ImageReader(str(path)).getSize();scale=min(WIDTH/w,230/h)
        self.images.append({"path":str(path),"sha256":digest(path),"pixels":[w,h],"display_points":[w*scale,h*scale]})
        image=Image(str(path),width=w*scale,height=h*scale);image.hAlign="CENTER"
        image.keepWithNext=True;after=Spacer(1,4);after.keepWithNext=True
        return [Spacer(1,5),image,after]
    def list_items(self,node):
        items=[]
        for li in node.children:
            if not isinstance(li,Node) or li.tag!="li":continue
            children=[];buf=[]
            def flush():
                if buf:children.append(Paragraph("".join(inline(c) for c in buf),self.s["list"]));buf.clear()
            for c in li.children:
                if isinstance(c,Node) and c.tag in ("ul","ol","p"):
                    flush();children.extend([self.para(c,"list")] if c.tag=="p" else [self.list_items(c)])
                else:buf.append(c)
            flush();items.append(ListItem(children or [Paragraph(" ",self.s["list"])]))
        return ListFlowable(items,bulletType="1" if node.tag=="ol" else "bullet",start=int(node.attrs.get("start",1)) if node.tag=="ol" else None,leftIndent=17,bulletFontName="E1Serif",bulletFontSize=10.5,bulletFormat="%s." if node.tag=="ol" else None,spaceAfter=5)
    def table(self,node):
        rows=[]
        def walk(n):
            if not isinstance(n,Node):return
            if n.tag=="tr":rows.append([c for c in n.children if isinstance(c,Node) and c.tag in ("th","td")])
            else:
                for c in n.children:walk(c)
        walk(node)
        if not rows:return []
        count=max(len(r) for r in rows)
        if any(len(r)!=count for r in rows):raise ValueError("ragged Markdown table")
        weight=[max(6,min(42,max(len(c.text()) for c in [r[k] for r in rows]))) for k in range(count)]
        widths=[WIDTH*w/sum(weight) for w in weight]
        headers=[c.text().strip() for c in rows[0]]
        # Dense decomposition table: preserve whole verdict words at the
        # existing type size rather than splitting Compliant/Abstain.
        if headers==["Monitor / family","Accuse, no D","Accuse, after D","Compliant","Abstain","Transport (pp)","After D (pp)"]:
            widths=[126,67,65,64,54,65,63]
        data=[[Paragraph(("<b>"+inline(c)+"</b>") if c.tag=="th" else inline(c),self.s["table"]) for c in r] for r in rows]
        t=Table(data,colWidths=widths,repeatRows=1,hAlign="LEFT")
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eaf0f3")),("LINEABOVE",(0,0),(-1,0),.7,colors.HexColor("#6b7b85")),("LINEBELOW",(0,0),(-1,0),.5,colors.HexColor("#6b7b85")),("LINEBELOW",(0,-1),(-1,-1),.6,colors.HexColor("#6b7b85")),("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white,colors.HexColor("#f8f9fa")]),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
        # Keep a following caption with the table's final part; this prevents
        # a table caption becoming the first unrelated item on the next page.
        t.keepWithNext=True;after=Spacer(1,7);after.keepWithNext=True
        self.tables+=1;return [Spacer(1,4),t,after]
    def blocks(self,nodes):
        out=[]
        for n in nodes:
            if isinstance(n,str):
                if n.strip():out.append(Paragraph(inline(n),self.s["body"]))
                continue
            tag=n.tag
            if tag in ("h1","h2","h3","h4","h5","h6"):
                title=normalize(n.text());self.headings.append(title)
                break_before=tag=="h2" and (title.lower()=="references" or (title.lower().startswith("appendix") and not self.appendix_seen))
                if tag=="h2" and title.lower().startswith("appendix"):self.appendix_seen=True
                if break_before and out and not isinstance(out[-1],PageBreak):out.append(PageBreak())
                out.append(self.para(n,"title" if tag=="h1" else "h2" if tag=="h2" else "h3"))
            elif tag=="p":
                imgs=[c for c in n.children if isinstance(c,Node) and c.tag=="img"]
                if imgs:
                    for img in imgs:out.extend(self.image(img))
                    if n.text().strip():out.append(self.para(n,"caption"))
                else:out.append(self.para(n,"caption" if re.match(r"^(Figure|Table)\s+\d+[.:]",n.text().strip()) else "body"))
            elif tag=="img":out.extend(self.image(n))
            elif tag in ("ol","ul"):out.append(self.list_items(n))
            elif tag=="table":out.extend(self.table(n))
            elif tag=="blockquote":
                out.append(Paragraph("<br/>".join(inline(c) for c in n.children if not isinstance(c,str) or c.strip()),self.s["quote"]))
            elif tag=="pre":out.append(Preformatted(normalize(n.text()),self.s["code"],maxLineLength=95))
            elif tag=="div" and (n.attrs.get("data-pagebreak")=="1" or "pagebreak" in n.attrs.get("class","")):
                if not out or not isinstance(out[-1],PageBreak):out.append(PageBreak())
            elif tag=="hr":out.append(Spacer(1,8))
            else:out.extend(self.blocks(n.children))
        return out


def quality(pdf,source,doc,converter,draft,render_dir,no_render=False):
    reader=PdfReader(str(pdf));pages=[];problems=[]
    with pdfplumber.open(pdf) as parsed:
        for n,page in enumerate(parsed.pages,1):
            text=page.extract_text() or "";chars=page.chars
            bad=[c for c in chars if c.get("text") in ("\ufffd","\u25a0")]
            clipped=[c for c in chars if c["x0"] < -1 or c["x1"] > page.width+1 or c["top"] < -1 or c["bottom"] > page.height+1]
            outside_frame=[c for c in chars if float(c["size"])>=9 and (c["x0"]<LEFT-2 or c["x1"]>PAGE_W-RIGHT+2 or c["bottom"]>PAGE_H-BOTTOM+3)]
            if bad or clipped or outside_frame:problems.append({"page":n,"replacement_or_square_glyphs":len(bad),"off_page_chars":len(clipped),"outside_body_frame_chars":len(outside_frame)})
            if not text.strip():problems.append({"page":n,"blank_page":True})
            if draft and "WORKING DRAFT - Study 4 pending" not in text:problems.append({"page":n,"missing_draft_banner":True})
            pages.append({"page":n,"width":page.width,"height":page.height,"words":len(text.split()),"chars":len(chars),"font_sizes":dict(Counter(round(float(c["size"]),1) for c in chars)),"images":len(page.images),"text":text})
    if any(abs(float(p.mediabox.width)-612)>.1 or abs(float(p.mediabox.height)-792)>.1 for p in reader.pages):problems.append({"non_Letter_pages":True})
    render_files=[]
    if not no_render:
        binary=shutil.which("pdftoppm")
        if not binary:raise RuntimeError("Poppler pdftoppm is required for all-page review")
        render_dir.mkdir(parents=True,exist_ok=False)
        subprocess.run([binary,"-png","-r","110",str(pdf),str(render_dir/"page")],check=True,capture_output=True,timeout=90)
        render_files=sorted(render_dir.glob("page-*.png"))
        if len(render_files)!=len(pages):problems.append({"render_page_count":len(render_files),"pdf_pages":len(pages)})
    reference_page=next((r["page"] for r in doc.section_pages if r["heading"].lower()=="references"),None)
    return {"schema":"e1-reportlab-build-qa-v1","pdf":str(pdf),"pdf_sha256":digest(pdf),"source_sha256":digest(source),"draft":draft,"draft_banner_all_pages":all("WORKING DRAFT - Study 4 pending" in p["text"] for p in pages) if draft else None,"page_count":len(pages),"main_text_end_page":reference_page-1 if reference_page else len(pages),"pages":pages,"section_pages":doc.section_pages,"figure_sources":converter.images,"tables":converter.tables,"rendered_pages":[str(p) for p in render_files],"machine_layout_pass":not problems,"problems":problems,"visual_inspection_required":True,"notes":["Machine diagnostics supplement, not replace, inspection of every rendered page.","No research sources or Markdown are changed by the builder."]}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("manuscript",type=Path);p.add_argument("output",type=Path);p.add_argument("--draft",action="store_true");p.add_argument("--render-dir",type=Path);p.add_argument("--markdown-site");p.add_argument("--font-dir");p.add_argument("--replace",action="store_true");p.add_argument("--no-render",action="store_true")
    a=p.parse_args();source=a.manuscript.resolve();output=a.output.resolve()
    if source==output or output.suffix.lower()!=".pdf":p.error("distinct PDF output required")
    if output.exists() and not a.replace:p.error("output exists; use explicit --replace for iterative PDF builds")
    output.parent.mkdir(parents=True,exist_ok=True)
    src=source.read_text(encoding="utf-8");before=digest(source)
    placeholders=re.findall(r"\[\[[^\]]+\]\]|\bTODO\b|\bTBD\b|\bPENDING:|\bWorking draft\.",src)
    if placeholders and not a.draft:raise ValueError("unresolved placeholder in non-draft manuscript")
    md=load_markdown(a.markdown_site);font_hashes=fonts(a.font_dir)
    html=md.markdown(normalize(src).replace("<!-- pagebreak -->",'<div data-pagebreak="1"></div>'),extensions=["tables","fenced_code","sane_lists","footnotes"],output_format="html5")
    tree=Tree();tree.feed(html)
    visible=tree.root.text()
    missing=sorted({c for c in visible if ord(c)>32 and ord(c) not in pdfmetrics.getFont("E1Serif").face.charToGlyph})
    if missing:raise ValueError("unsupported visible glyph code points: "+str([hex(ord(c)) for c in missing]))
    converter=Converter(source.parent);story=converter.blocks(tree.root.children)
    if not story:raise ValueError("empty manuscript")
    doc=Report(output,draft=a.draft,title=next((n.text() for n in tree.root.children if isinstance(n,Node) and n.tag=="h1"),"Research report"),author="Author information in manuscript")
    doc.build(story)
    render_dir=a.render_dir.resolve() if a.render_dir else output.parent/(output.stem+"-pages")
    result=quality(output,source,doc,converter,a.draft,render_dir,a.no_render)
    result["dependencies"]={"python":sys.version,"reportlab":reportlab.Version,"markdown":md.__version__,"pdfplumber":pdfplumber.__version__,"pypdf":importlib.import_module("pypdf").__version__,"fonts_sha256":font_hashes,"pdftoppm":shutil.which("pdftoppm")}
    result["placeholder_count"]=len(placeholders);result["manuscript_unchanged"]=digest(source)==before
    if not result["manuscript_unchanged"]:raise RuntimeError("manuscript changed during build; rerun against current content")
    qa=output.with_suffix(".qa.json");qa.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"pdf":str(output),"qa":str(qa),"pages":len(result["pages"]),"machine_layout_pass":result["machine_layout_pass"],"draft":a.draft,"figures":len(converter.images)}))
    return 0 if result["machine_layout_pass"] else 2


if __name__=="__main__":sys.exit(main())
