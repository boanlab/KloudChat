/**
 * Copies to the clipboard; false when it failed. `navigator.clipboard` exists
 * only in a secure context, so plain HTTP falls back to a textarea and `execCommand`.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Permission denied or document not focused: fall through.
    }
  }

  try {
    const area = document.createElement('textarea')
    area.value = text
    // Off-screen, but still focusable: `display:none` cannot be selected.
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    area.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(area)
    return ok
  } catch {
    return false
  }
}
