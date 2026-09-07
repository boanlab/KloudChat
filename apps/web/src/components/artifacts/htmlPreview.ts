/** Keep implicit fragment links inside srcdoc instead of navigating to the app.
 *  Explicit bases and stored/downloaded HTML retain their original semantics. */
export function htmlPreviewDocument(html: string): string {
  const document = new DOMParser().parseFromString(html, 'text/html')
  if (document.querySelector('base[href]')) return html
  const hasFragmentLink = [...document.querySelectorAll('a[href], area[href]')]
    .some((link) => link.getAttribute('href')?.trim().startsWith('#'))
  if (!hasFragmentLink) return html
  const base = document.createElement('base')
  base.href = 'about:srcdoc'
  document.head.prepend(base)
  const doctype = document.doctype
    ? new XMLSerializer().serializeToString(document.doctype)
    : ''
  return doctype + document.documentElement.outerHTML
}
