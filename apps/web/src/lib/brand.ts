/** Writes the brand name and logo to the tab title and favicon. */
export function applyBrand(brand: { name: string; logo: string }) {
  const name = brand.name.trim()
  if (name) document.title = name

  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (!icon) return
  if (brand.logo) {
    // Uploaded logos are png/jpg/webp; the browser sniffs the type.
    icon.removeAttribute('type')
    icon.href = brand.logo
  }
}
