/**
 * The name and icon shown on the browser tab.
 *
 * Components render the in-page logo, but the tab title and favicon have to be
 * written to the document directly. Every place that fetches the public
 * configuration calls this, because the sign-in screen and the signed-in app
 * receive those values by different routes.
 */
export function applyBrand(brand: { name: string; logo: string }) {
  const name = brand.name.trim()
  if (name) document.title = name

  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!icon) return
  if (brand.logo) {
    // Uploaded logos are only ever png/jpg/webp, so the type is left off and
    // the browser decides.
    icon.removeAttribute('type')
    icon.href = brand.logo
  }
}
