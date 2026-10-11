/** Key event inside an IME composition; Safari's committing Enter has keyCode 229 with isComposing false. */
export function isImeKey(e: { nativeEvent: { isComposing?: boolean; keyCode?: number }; keyCode?: number }): boolean {
  return Boolean(e.nativeEvent.isComposing) || e.nativeEvent.keyCode === 229 || e.keyCode === 229
}
