E1 PDF and figure tools
=======================

These new tools never edit manuscript.md or scientific source/raw artifacts.
They use ReportLab directly, without a browser, network, installation, model,
or provider call. The first PDF-operation marker was already invoked by the
primary; do not repeat it for iterative builds.

From the repository root, use the Codex bundled Python executable for PDFs:

```powershell
& 'C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1104-2876-0_26.1104.2876.0_x64__3k8sg7r9htsxt/dependencies/python/python.exe' -B paper/final-e1/tools/build_pdf.py paper/final-e1/manuscript.md paper/final-e1/tmp/e1-working-draft003.pdf --draft --render-dir paper/final-e1/tmp/e1-working-draft003-pages
```

The default layout is US Letter, 10.5pt embedded DejaVu Serif body, 13.4pt
leading, serif headings, 9.2pt table/caption text, and a page-number footer.
`--draft` adds `WORKING DRAFT - Study 4 pending` on every page. Omit it only
when the primary approves final content. Output files refuse replacement by
default; explicit `--replace` permits an iterative PDF build. Use a new render
directory for every build.

Markdown supports headings, paragraphs, bold/italic/code, hyperlinks, numbered
and bullet lists, blockquotes, tables with repeated headers, fenced code, and
local PNG/JPEG figures. Images must exist; remote image URLs are rejected.
`<!-- pagebreak -->` creates a page break. Other HTML comments, including the
primary's Study 4 replacement markers, remain invisible. References and the
first Appendix automatically start fresh pages if no explicit marker exists.
Later appendix headings flow normally. Figure and table captions beginning
`Figure N.` or `Table N.` receive readable caption formatting and are kept
with their visual when practical. Local image paths resolve from manuscript
directory. Inline Markdown footnotes render, but internal link targets are
shown as text rather than active PDF cross-links.

Each build saves a .qa.json containing PDF/source hashes, all extracted page
text, page and main-text-end counts, section pages, font sizes, image hashes,
dependency versions/font hashes, placeholder count, glyph/frame diagnostics,
and every Poppler page PNG path. A final non-draft build rejects TODO/TBD/[[ ]]
placeholders. Glyph coverage is checked before generation. Machine checks do
not replace visual inspection of every latest rendered page.

Installed dependencies actually used:

- Bundled Python 3.12.14, ReportLab 4.4.9, pdfplumber 0.11.9, pypdf 6.10.0.
- Python-Markdown 3.10.3 is a pure Python package available through the existing
  experiments/.venv's configured system Python 3.13 installation. The renderer
  locates this existing package from pyvenv.cfg; `--markdown-site` overrides its
  site-packages directory. Core PDF modules remain the bundled versions.
- Existing experiments/.venv Python 3.13.12 supplies matplotlib 3.11.1 and
  NumPy for figure creation. No packages were installed.
- Bundled Poppler pdftoppm renders every page at 110dpi for review. Embedded
  fonts are the existing C:/Windows/Fonts/DejaVuSerif*.ttf and DejaVuSansMono.ttf.

Regenerate the two source-bound figures using the existing experiments venv:

```powershell
experiments/.venv/Scripts/python.exe -B paper/final-e1/tools/make_figures.py --repo-root . --output-dir paper/final-e1/figures
```

Figure 1 is `figures/study2_false_accusations.png` (also vector .pdf): counts
from actual analysis/out/study2_results/numbers_study2.json, P3_orig X/B fields,
n=30. Figure 2 is `figures/study2_template_profile.png` (also vector .pdf): the
literal Sonnet T1 monitor_results.jsonl records filtered to model sonnet,
arm p3orig, repeat_idx 0, world_type X, five bases per template. Each figure
has a .data.json recording exact source paths/hashes, keys/filters, selected
counts or records, and output hashes. Both are explicitly synthetic workflow
diagnostics, not deployment estimates. The tool refuses fake/simulated modes
and reads no Study 4 forecast file.

Run targeted semantic checks with bundled Python:

```powershell
python -B -m unittest discover -s paper/final-e1/tools -p test_renderer.py -v
```

For production, substitute the bundled executable shown above for `python`.
