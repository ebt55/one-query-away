"""Targeted offline renderer semantics; no manuscript/science files are written."""
import unittest
from pathlib import Path
import build_pdf as B
from reportlab.platypus import PageBreak,Paragraph,ListFlowable,Table


class RendererChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):B.fonts();cls.md=B.load_markdown()
    def convert(self,source):
        html=self.md.markdown(source.replace("<!-- pagebreak -->",'<div data-pagebreak="1"></div>'),extensions=["tables","sane_lists","fenced_code"])
        tree=B.Tree();tree.feed(html);c=B.Converter(Path.cwd());return c,c.blocks(tree.root.children)
    def test_inline_formatting_links_and_comment_markers(self):
        tree=B.Tree();tree.feed('<p><strong>bold</strong>, <em>italic</em>, <a href="https://example.org/a?x=1&amp;y=2">link</a></p><!-- STUDY4_RESULTS_START -->')
        inline=B.inline(tree.root.children[0]);self.assertIn('<b>bold</b>',inline);self.assertIn('<i>italic</i>',inline);self.assertIn('href="https://example.org/a?x=1&amp;y=2"',inline);self.assertNotIn('STUDY4',tree.root.text())
    def test_tables_lists_blockquotes_and_pagebreaks(self):
        c,story=self.convert('## Methods\n\n1. **One**\n2. Two\n\n> Quoted evidence\n\n| A | B |\n|---|---|\n| x | 1 |\n\n<!-- pagebreak -->\n\n## References\n\nCitation\n\n<!-- pagebreak -->\n\n## Appendix A\n\nText\n\n## Appendix B\n\nMore')
        self.assertTrue(any(isinstance(f,ListFlowable) for f in story));self.assertTrue(any(isinstance(f,Table) for f in story));self.assertTrue(any(isinstance(f,Paragraph) and f.style.name=='quote' for f in story));self.assertEqual(sum(isinstance(f,PageBreak) for f in story),2)
    def test_ascii_dash_normalization(self):self.assertEqual(B.normalize('K1–K4 −3 no‑op'),'K1-K4 -3 no-op')
    def test_remote_images_cannot_trigger_download(self):
        node=B.Node('img',{'src':'https://example.org/plot.png'}.items())
        with self.assertRaises(ValueError):B.Converter(Path.cwd()).image(node)


if __name__=='__main__':unittest.main()
