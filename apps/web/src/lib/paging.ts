/** Page arithmetic shared by the paged lists (audit log, settings › 보안). */

/** Page numbers around `page`, with gaps as null: 1 … 4 5 6 … 12. */
export function pageWindow(page: number, pages: number): (number | null)[] {
  const keep = new Set([1, pages, page - 1, page, page + 1].filter((p) => p >= 1 && p <= pages))
  const out: (number | null)[] = []
  let last = 0
  for (const p of [...keep].sort((a, b) => a - b)) {
    if (p - last > 1) out.push(null)
    out.push(p)
    last = p
  }
  return out
}

/** Number of pages for `total` rows, never less than one. */
export function pageCount(total: number, size: number): number {
  return Math.max(1, Math.ceil(total / size))
}
