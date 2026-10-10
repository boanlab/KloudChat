/** 숨김: a shipped skill, 서식 or theme kept as a part but not offered in the lists people pick
 *  from (`listed: false` on the row). It still resolves by id everywhere it is already used. */

export function isListed(row: { listed?: boolean }): boolean {
  return row.listed !== false
}

/** The rows a picker offers: the listed ones, plus any already chosen (`keep`, by id). */
export function pickable<T extends { id: string; listed?: boolean }>(
  rows: T[],
  keep: ReadonlyArray<string | null | undefined> = [],
): T[] {
  return rows.filter((row) => isListed(row) || keep.includes(row.id))
}
