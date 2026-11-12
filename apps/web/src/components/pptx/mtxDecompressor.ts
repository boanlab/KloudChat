/**
 * Stands in for `mtx-decompressor` (MPL-2.0), which the PowerPoint editor's font loader asks
 * for to unpack fonts embedded as MTX/EOT (old decks only). Not shipped: such a font is not
 * unpacked and the text falls back to the deck's other fonts. Aliased in vite.config.ts.
 */
export function decompressEotFont(): null {
  return null
}
