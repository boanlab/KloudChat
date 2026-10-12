// Paged.js appends stylesheets to `document.head`; selectors rewritten via CSSOM to match only inside the preview.

/** Root selectors; under a scope, the scope stands for them. */
const DOCUMENT_ROOTS = new Set(['html', 'body', ':root', ':host', 'html body'])

function scopeSelector(selector: string, scope: string): string {
  const parts = selector
    .split(',')
    .map((part) => {
      const one = part.trim()
      if (!one) return ''
      if (DOCUMENT_ROOTS.has(one.toLowerCase())) return scope
      const rooted = one.replace(/^\s*(?::root|:host|html|body)\b/i, scope)
      return rooted === one ? `${scope} ${one}` : rooted
    })
    .filter(Boolean)
  // `html, body { … }` collapses to one selector twice.
  return [...new Set(parts)].join(', ')
}

function scopeRules(rules: CSSRuleList, scope: string) {
  for (const rule of Array.from(rules)) {
    if (rule instanceof CSSKeyframesRule) continue

    // Type check before the grouping branch: a nested CSSStyleRule has `cssRules`, relative to the scoped parent.
    if (rule instanceof CSSStyleRule) {
      try {
        rule.selectorText = scopeSelector(rule.selectorText, scope)
      } catch {
        // Rejected selector never matched anything.
      }
      continue
    }

    const grouped = rule as CSSRule & { cssRules?: CSSRuleList }
    if (grouped.cssRules) scopeRules(grouped.cssRules, scope)
  }
}

/** Confines every sheet Paged.js inserts to `scope`; start before `preview()`; teardown removes the sheets. */
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
