/**
 * Whether a key event belongs to an IME composition still in progress. Safari ends a Korean
 * composition with an Enter whose `isComposing` is already false but whose `keyCode` is 229;
 * that Enter commits the last syllable and must not also send.
 */
export function isImeKey(e: { nativeEvent: { isComposing?: boolean; keyCode?: number }; keyCode?: number }): boolean {
  return Boolean(e.nativeEvent.isComposing) || e.nativeEvent.keyCode === 229 || e.keyCode === 229
}
