/**
 * Copies to the clipboard and reports whether it worked.
 *
 * `navigator.clipboard` exists only in a secure context, so on plain HTTP it is
 * undefined and the button would claim success having copied nothing. Falls
 * back to a temporary textarea and `execCommand`; false when both fail, so the
 * caller shows no success state.
 */
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Permission denied, or the document is not focused — fall through.
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
