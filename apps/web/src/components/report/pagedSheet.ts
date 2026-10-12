import type { ReportArtifact } from '@/types'

/** Page settings, every field filled. */
export type PageSettings = Required<NonNullable<ReportArtifact['pageSettings']>>

export const DEFAULT_PAGE_SETTINGS: PageSettings = {
  header: '', footer: 'KloudChat', pageNumbers: 'page-total', firstPageHeader: false,
  margins: { top: 18, right: 16, bottom: 20, left: 16 },
}

const escapeCssContent = (text: string) => text.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r?\n/g, ' ')

/** Paged.js sheet after the template `css`: A4 box, margins, running header/footer, page number, screen-layout guards; shared by the page view and `PagedPreview`. */
export function pagedSheetCss(css: string, settings: PageSettings): string {
  return `
      html, body { margin: 0; padding: 0; background: white; }
      h1 { string-set: document-title content(text); }
      ${css}
      /* After the template's css: its own @page (20mm 18mm) would otherwise outrank these
         margins, and the page settings would have no effect. */
      @page {
        size: A4;
        margin: ${settings.margins.top}mm ${settings.margins.right}mm ${settings.margins.bottom}mm ${settings.margins.left}mm;
        @top-left { content: ${settings.header ? `"${escapeCssContent(settings.header)}"` : 'string(document-title)'}; color: #777; font-size: 8pt; }
        @bottom-left { content: "${escapeCssContent(settings.footer)}"; color: #777; font-size: 8pt; }
        @bottom-right { content: ${settings.pageNumbers === 'none' ? 'none' : settings.pageNumbers === 'page' ? 'counter(page)' : 'counter(page) " / " counter(pages)'}; color: #777; font-size: 8pt; }
      }
      @page:first { @top-left { content: ${settings.firstPageHeader ? (settings.header ? `"${escapeCssContent(settings.header)}"` : 'string(document-title)') : 'none'}; } }
      /* The sheet's screen box (padded, a page high) must not reach Paged.js, or the cover
         inside it is pushed to page two and page one comes out blank. */
      .page { min-height: 0 !important; max-width: none !important; margin: 0 !important; padding: 0 !important; }
      /* The seed repeats the document name on every printed sheet as a fixed element; Paged.js
         pulls fixed elements into the flow of each page and the running header above already
         carries the name, so the copy would only push the text down page by page. */
      .doc-foot { display: none !important; }
      /* A cover taller than the page cannot be split, and Paged.js retries it forever: it is
         clipped to the sheet. A cover page (seed \`cover-page\`, a format's \`tb-cover\`) ends
         at its own break-after; its height is its content, never a screen height. */
      .cover { margin: 0 !important; box-sizing: border-box; max-height: ${297 - settings.margins.top - settings.margins.bottom - 4}mm !important; overflow: hidden; }
      .cover { min-height: 0 !important; }
      section { break-inside: auto; }
      h1, h2, h3, h4 { break-after: avoid; }
      p, li { orphans: 2; widows: 2; }
      table, tbody { break-inside: auto !important; page-break-inside: auto !important; }
      thead { display: table-header-group; }
      tr { break-inside: avoid; page-break-inside: avoid; }
      figure, img, pre, blockquote { break-inside: avoid; }
      [data-page-break="true"] { break-before: page; height: 0; }
    `
}
