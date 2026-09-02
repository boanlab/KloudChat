/**
 * Keeps Paged.js's stylesheets inside the preview they were made for.
 *
 * Paged.js is built on the assumption that it owns the document: `preview()`
 * hands every stylesheet it was given to its polisher, which appends the
 * rewritten result to `document.head`. For a print page that is right. Inside
 * an app it is a stylesheet written for a whole document — `body`, bare `h1`,
 * bare `p`, `table`, `*` — landing on the application shell.
 *
 * What that looked like: open a 명조 report and the sidebar, the composer and
 * every menu in the product turned serif, because the 서식 says
 * `body { font-family: var(--font-body) }` and nothing stopped it at the panel.
 * The font was only the visible half — `p` margins, `ul` padding, `table`
 * width and the page's white `body` background came with it, and all of it
 * outlived the panel, because the styles stay in `<head>` after it closes.
 *
 * The pages Paged.js draws are all inside one element, so a selector confined
 * to that element reaches every one of them and nothing else. Rewriting is
 * done through the CSSOM rather than on the text: `selectorText` is parsed by
 * the engine that will match it, which a regular expression over CSS source is
 * not.
 *
 * `--pagedjs-width` and its siblings survive the move because nothing in
 * Paged.js reads them from JavaScript — they are consumed by rules on
 * `.pagedjs_page`, which is inside the scope with everything else.
 */

/** Selectors that mean "the document" — under a scope, the scope is it. */
const DOCUMENT_ROOTS = new Set(['html', 'body', ':root', ':host', 'html body'])

function scopeSelector(selector: string, scope: string): string {
  const parts = selector
    .split(',')
    .map((part) => {
      const one = part.trim()
      if (!one) return ''
      if (DOCUMENT_ROOTS.has(one.toLowerCase())) return scope
      // `body > x` and `html.foo` are about the document too; the scope stands
      // in for the root, so the rest of the combinator still means what it did.
      const rooted = one.replace(/^\s*(?::root|:host|html|body)\b/i, scope)
      return rooted === one ? `${scope} ${one}` : rooted
    })
    .filter(Boolean)
  // `html, body { … }` collapses to the same selector twice.
  return [...new Set(parts)].join(', ')
}

function scopeRules(rules: CSSRuleList, scope: string) {
  for (const rule of Array.from(rules)) {
    // Keyframe percentages are not selectors, and a keyframe name means
    // nothing outside the animation that calls it.
    if (rule instanceof CSSKeyframesRule) continue

    // Asked before the grouping branch, and by type. `CSSStyleRule` carries a
    // `cssRules` of its own now that CSS nesting is implemented — testing for
    // the property first sent every ordinary rule down the recursion, which
    // rewrote nothing and left the whole sheet loose on the app.
    if (rule instanceof CSSStyleRule) {
      try {
        rule.selectorText = scopeSelector(rule.selectorText, scope)
      } catch {
        // A selector the engine will not take back is one it never matched.
      }
      // Nested rules are relative to the parent, which is now scoped.
      continue
    }

    // A grouping rule's condition is untouched; its children are not.
    const grouped = rule as CSSRule & { cssRules?: CSSRuleList }
    if (grouped.cssRules) scopeRules(grouped.cssRules, scope)
  }
}

/**
 * Watches `<head>` and confines every sheet Paged.js puts there to `scope`.
 *
 * Started before `preview()`, so the rewrite lands in the same microtask the
 * insertion did and no frame is ever painted with the document's CSS loose on
 * the app. Returns the teardown, which also removes the sheets — a closed
 * panel must not leave its stylesheet behind.
 */
export function scopePagedStyles(scope: string): () => void {
  const claimed = new Set<HTMLStyleElement>()

  const take = (node: Node) => {
    if (!(node instanceof HTMLStyleElement)) return
    if (!node.hasAttribute('data-pagedjs-inserted-styles')) return
    if (claimed.has(node)) return
    claimed.add(node)
    // `sheet` is null until the element is in the document; it is, by the time
    // the observer sees it.
    if (node.sheet) scopeRules(node.sheet.cssRules, scope)
  }

  document.head.querySelectorAll('style[data-pagedjs-inserted-styles]').forEach(take)

  const observer = new MutationObserver((records) => {
    for (const record of records) record.addedNodes.forEach(take)
  })
  observer.observe(document.head, { childList: true })

  return () => {
    observer.disconnect()
    claimed.forEach((node) => node.remove())
    claimed.clear()
  }
}
