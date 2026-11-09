# E1 template following contract

Reference: `C:/Users/ebin/claude-ground/apart-research/swarm-allignment-nov-2026/submission-template/Copy of Apart Research hackathon submission template.docx`, SHA256 `f6cb61cbfe9d76180d1b29ab51d3189b8926f30ba4f586fdf6d53ab6892fe909`.

All five reference pages were rendered read-only through Microsoft Word and inspected. The packaged renderer was attempted but its required LibreOffice is absent from the Windows bundle/PATH; Word is the installed native renderer. Reference PDF and all five PNGs are in this directory. `reference-evidence.json` records every package-part hash, body slots, styles, section geometry, fields and controls. The original is immutable.

## Source content

Use the accepted canonical `paper/final-e1/manuscript.md`, SHA256 `186a3dbd2525fad70c00cb95bdfca708ec8a1b44315a6f108bf2bed2248e7080`. Its scientific text, ordering, counts, tables, figures, citations and disclosure stay intact. The author requested a layout/design correction, explicitly approving the existing structure and content. No new research claim, headline or rewritten conclusion is allowed.

## Page and component system

- Letter portrait, one section, 8.5 by 11 inches. All four body margins 1 inch. Header/footer distances 0.5 inch. Single-column article, 6.5-inch text width.
- The template's opening is its actual first body table: a 6.5-inch title row with a black 3-point top rule and thin lower rule, then centered author/With Apart Research information and a narrower centered abstract. Preserve this component, adapt its six-author placeholders to the single actual author, remove empty author positions, and replace abstract guidance with the complete 205-word abstract. Preserve the actual title footnote and its linked sprint attribution. A title line break is presentational only.
- Old Standard TT is embedded in the source. Normal text is 11 points, 1.15 line spacing. Reuse this font and its original embedded regular/bold/italic parts; no external font installation or replacement. Remove placeholder italics from body prose. Normal paragraphs receive 6-point after spacing and widow/orphan protection.
- The original Heading2 component is the main section style: 14-point bold with 12 points before; Heading3 supplies 12.5-point subsections with 9 points before. Use the source style identities and monochrome family, with gray guidance-heading overrides made black for the completed article. Title uses the actual Word Title style and its centered black typography. These small hierarchy changes are part of the requested design refinement, not changes to manuscript text.
- Start the article on page 2 after the opening. Start Methods on a fresh page after Introduction/Related Work. Keep headings with their complete opening paragraph, figures with their preceding introduction and captions, and short tables with preceding context and captions. Captions have 6 points before and 10 points after. References and the first appendix begin on new pages so the main-text page count is unambiguous. Other appendices/disclosure flow naturally without unnecessary dedicated blank pages.
- Existing Figure 1/2 PNGs retain exact bytes. Scale to the 6.5-inch article measure without cropping; keep captions immediately below. Source figures are not edited.
- The template has no scientific data-table example. Add fixed-width data tables in its monochrome style: pale gray header, black type, light-gray 0.5-point cell borders, left-aligned labels and centered numeric columns, flexible row heights, repeating headers. Allocate widths to actual content; use 10-point text, never clip or silently abbreviate source text. Captions are 10-point italic and stay with their tables.
- The source has no running header/footer. The requested layout refinement adds an unobtrusive centered PAGE field only; no generic event banner. Word repagination/export supplies correct numbers. The editable copy sets updateFields so fields refresh on open.

## Slots and preservation

- `word/document.xml /w:document/w:body/w:tbl[1]/w:tr[1]/w:tc`: replace title text, preserve its border properties and footnote reference. May remove spacer-only paragraphs and add deliberate spacing.
- First table's second-row cell: replace nested six-author placeholder block with one centered author/affiliation/With block; replace abstract guidance with accepted abstract. Preserve the outer component and its border-free lower edge.
- Body guidance table and all guidance/sample paragraphs are editable slots and are removed. Reuse Heading2/Heading3/body/list components for each accepted manuscript block in its original order. Optional author-contribution placeholder is removed because the manuscript has none.
- `word/sectPr`: preserve page geometry and section count. Add only the page-number footer reference.
- Preserve all original package parts byte-for-byte except `word/document.xml`, `word/_rels/document.xml.rels`, `[Content_Types].xml` and `word/settings.xml`. Add only footer and two original figure images. Styles, numbering, theme, embedded fonts, font relationships, footnotes and footnote relationship bytes must remain identical. Unused source hyperlink relationships may remain; none are silently remapped.
- All content controls, drawings and body fields in the reference number zero. There is one actual title footnote. The new PAGE field and two figures are deliberate additions, not lost reference features.

## Gates

1. Original DOCX and canonical Markdown/figures unchanged; all preserve-only parts identical.
2. Every accepted source block has the same normalized visible text in the generated Word body, with author metadata and sprint attribution mapped to their specified slots. No guidance text or placeholder authors survives.
3. Render through Word, then Poppler; inspect every page. Check title/author/abstract proportions, section rhythm, figure/table placement, wide header wrapping and no clipping or duplicate/missing text. Compare with all five reference page patterns.
4. Count all main pages including the opening page: at most 8. Abstract 150-250 words, final PDF below 25 MB, required ethics appendix and disclosure present.
5. Existing validated PDFs and archives remain unchanged. Any replacement final artifact is versioned and bound to the unchanged scientific source.
