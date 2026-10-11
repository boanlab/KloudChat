/** Writes the brand name and logo to the tab title and favicon. */

/** The instance name a fresh install ships with; its shipped favicon is kept. */
const DEFAULT_NAME = 'kloudchat'

/** The favicon the page loaded with, so a brand reverted to the default gets it back. */
let shippedHref: string | null = null

/** 32×32 favicon like `Brand`'s initial mark; light-theme accent (favicons ignore the theme). */
function initialFavicon(initial: string): string {
  const escaped = initial.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c] ?? c)
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' +
    '<rect width="32" height="32" rx="8" fill="#2a54e4"/>' +
    '<text x="16" y="17" text-anchor="middle" dominant-baseline="central" ' +
    'font-family="system-ui,-apple-system,Segoe UI,Roboto,sans-serif" font-size="19" font-weight="700" fill="#ffffff">' +
    escaped +
    '</text></svg>'
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

export function applyBrand(brand: { name: string; logo: string }) {
  const name = brand.name.trim()
  if (name) document.title = name

  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!icon) return
  shippedHref ??= icon.getAttribute('href')

  if (brand.logo) {
    // Uploaded logos are png/jpg/webp; the browser sniffs the type.
    icon.removeAttribute('type')
    icon.href = brand.logo
    return
  }

  // No logo: initial favicon, unless the default name keeps the shipped icon.
  const initial = name[0]?.toUpperCase()
  if (!initial || name.toLowerCase() === DEFAULT_NAME) {
    if (shippedHref) {
      icon.type = 'image/svg+xml'
      icon.href = shippedHref
    }
    return
  }
  icon.type = 'image/svg+xml'
  icon.href = initialFavicon(initial)
}
