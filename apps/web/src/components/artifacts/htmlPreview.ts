/** A srcdoc document otherwise resolves #links against the embedding app's URL.
 *  Only the displayed copy gets this base; stored and downloaded HTML stays intact. */
export function htmlPreviewDocument(html: string): string {
  const document = new DOMParser().parseFromString(html, 'text/html')
  // The preview owns its navigation base, including when generated HTML supplies one.
  document.querySelectorAll('base').forEach((base) => base.remove())
  const base = document.createElement('base')
  base.href = 'about:srcdoc'
  document.head.prepend(base)
  const doctype = document.doctype
    ? new XMLSerializer().serializeToString(document.doctype)
    : ''
  return doctype + document.documentElement.outerHTML
}
