// Paged.js appends the document's stylesheets to `document.head`, where they
// would restyle the app. These helpers rewrite every selector through the
// CSSOM so it only matches inside the preview element.

/** Selectors that mean "the document"; under a scope, the scope is it. */
const DOCUMENT_ROOTS = new Set(['html', 'body', ':root', ':host', 'html body'])

function scopeSelector(selector: string, scope: string): string {
  const parts = selector
    .split(',')
    .map((part) => {
      const one = part.trim()
      if (!one) return ''
      if (DOCUMENT_ROOTS.has(one.toLowerCase())) return scope
      // The scope stands in for a leading root selector.
      const rooted = one.replace(/^\s*(?::root|:host|html|body)\b/i, scope)
      return rooted === one ? `${scope} ${one}` : rooted
    })
    .filter(Boolean)
  // `html, body { … }` collapses to the same selector twice.
  return [...new Set(parts)].join(', ')
}

function scopeRules(rules: CSSRuleList, scope: string) {
  for (const rule of Array.from(rules)) {
    if (rule instanceof CSSKeyframesRule) continue

    // Checked by type before the grouping branch: with CSS nesting a
    // CSSStyleRule has `cssRules` of its own, and its nested rules are relative
    // to the now-scoped parent.
    if (rule instanceof CSSStyleRule) {
      try {
        rule.selectorText = scopeSelector(rule.selectorText, scope)
      } catch {
        // A selector the engine rejects never matched anything.
      }
      continue
    }

    const grouped = rule as CSSRule & { cssRules?: CSSRuleList }
    if (grouped.cssRules) scopeRules(grouped.cssRules, scope)
  }
}

/**
 * Watches `<head>` and confines every sheet Paged.js inserts to `scope`.
 * Start it before `preview()`. The returned teardown also removes the sheets.
 */
export function scopePagedStyles(scope: string): () => void {
  const claimed = new Set<HTMLStyleElement>()

  const take = (node: Node) => {
    if (!(node instanceof HTMLStyleElement)) return
    if (!node.hasAttribute('data-pagedjs-inserted-styles')) return
    if (claimed.has(node)) return
    claimed.add(node)
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
