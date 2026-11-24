/** While a report is read, the section being read is scrolled into view and marked (`[data-reading]`, styled in index.css). */
let marked: Element | null = null

export function followSection(id: string) {
  marked?.removeAttribute('data-reading')
  marked = null
  if (!id) return
  const heading = document.querySelector(`[data-section-id="${CSS.escape(id)}"]`)
  if (!heading) return
  heading.setAttribute('data-reading', 'true')
  marked = heading
  heading.scrollIntoView({ block: 'center', behavior: 'smooth' })
}

/** The reading ended: the mark goes. */
export function clearFollow() {
  marked?.removeAttribute('data-reading')
  marked = null
}
