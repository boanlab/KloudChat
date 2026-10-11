/** 숨김: shipped item kept as a part, not offered (`listed: false`); still resolves by id. */

export function isListed(row: { listed?: boolean }): boolean {
  return row.listed !== false
}
