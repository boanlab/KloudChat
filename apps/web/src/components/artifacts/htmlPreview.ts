/** Keep fragment links inside srcdoc without changing the document's resource base.
 *  Only the displayed copy changes; stored and downloaded HTML stays intact. */
export function htmlPreviewDocument(html: string): string {
  const document = new DOMParser().parseFromString(html, 'text/html')
  document.querySelectorAll('a[href], area[href]').forEach((link) => {
    const href = link.getAttribute('href')?.trim()
    if (href?.startsWith('#')) link.setAttribute('href', `about:srcdoc${href}`)
  })
  const doctype = document.doctype
    ? new XMLSerializer().serializeToString(document.doctype)
    : ''
  return doctype + document.documentElement.outerHTML
}
