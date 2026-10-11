/**
 * Keyboard shortcuts: the list the dialog prints is the list the handler
 * matches. `mod` is ⌘ on a Mac and Ctrl elsewhere. No chord a browser keeps for itself:
 * Shift+Esc (Chrome's task manager), Ctrl+Shift+C/I (developer tools) and Ctrl+Shift+O
 * (bookmarks) never reach the page, so those actions use Alt+Shift or a bare key.
 */

type ShortcutId =
  | 'new-chat'
  | 'focus-composer'
  | 'copy-last-answer'
  | 'copy-last-code'
  | 'personalization'
  | 'toggle-dictation'
  | 'toggle-sidebar'
  | 'delete-conversation'
  | 'search'
  | 'show-shortcuts'

interface Shortcut {
  id: ShortcutId
  label: string
  /** `mod` renders as ⌘ or Ctrl, `alt` as ⌥ or Alt; the last entry is the key itself. */
  keys: string[]
  /** `key` as the browser reports it, compared case-insensitively. */
  key: string
  /** Matched instead of `key` when set: with ⌥ held a Mac reports another character. */
  code?: string
  mod?: boolean
  shift?: boolean
  alt?: boolean
  /** Only when no text field has focus (a bare key would otherwise be typed). */
  outsideFields?: boolean
}

export const SHORTCUTS: Shortcut[] = [
  { id: 'new-chat', label: '새 대화 열기', keys: ['alt', 'Shift', 'N'], key: 'n', code: 'KeyN', alt: true, shift: true },
  { id: 'focus-composer', label: '입력창에 집중', keys: ['/'], key: '/', outsideFields: true },
  { id: 'copy-last-code', label: '마지막 코드 블록 복사', keys: ['mod', 'Shift', ';'], key: ';', mod: true, shift: true },
  { id: 'copy-last-answer', label: '마지막 답변 복사', keys: ['alt', 'Shift', 'C'], key: 'c', code: 'KeyC', alt: true, shift: true },
  { id: 'personalization', label: '개인 맞춤 설정', keys: ['alt', 'Shift', 'P'], key: 'p', code: 'KeyP', alt: true, shift: true },
  { id: 'toggle-dictation', label: '말로 쓰기 시작·끝내기', keys: ['mod', 'Shift', 'M'], key: 'm', mod: true, shift: true },
  { id: 'toggle-sidebar', label: '사이드바 토글', keys: ['mod', 'Shift', 'S'], key: 's', mod: true, shift: true },
  { id: 'delete-conversation', label: '대화 삭제', keys: ['mod', 'Shift', '⌫'], key: 'Backspace', mod: true, shift: true },
  { id: 'search', label: '대화·아티팩트 검색', keys: ['mod', 'K'], key: 'k', mod: true },
  { id: 'show-shortcuts', label: '단축키 표시', keys: ['mod', '/'], key: '/', mod: true },
]

/** Keys the composer itself handles; listed so the dialog is complete. */
export const COMPOSER_KEYS: { label: string; note?: string; keys: string[] }[] = [
  { label: '보내기', keys: ['Enter'] },
  { label: '줄 바꿈', keys: ['Shift', 'Enter'] },
  { label: '말로 쓰기', note: '빈 입력창에서 누른 채 말하고, 떼면 보냄', keys: ['Space'] },
]

export const isMac = () =>
  typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** Whether focus is somewhere that takes typing. */
function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  if (!el || typeof el.closest !== 'function') return false
  return Boolean(el.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'))
}

/** The chord a keydown is, or null. Shift+; arrives as `:` on US layouts. */
export function shortcutFor(e: KeyboardEvent): Shortcut | null {
  const mod = isMac() ? e.metaKey : e.ctrlKey
  // The other platform's modifier is no chord of ours.
  const other = isMac() ? e.ctrlKey : e.metaKey
  const key = e.key === ':' ? ';' : e.key
  for (const s of SHORTCUTS) {
    if (Boolean(s.mod) !== mod || other) continue
    if (Boolean(s.shift) !== e.shiftKey) continue
    if (Boolean(s.alt) !== e.altKey) continue
    if (s.outsideFields && typing(e.target)) continue
    if (s.code ? e.code === s.code : key.toLowerCase() === s.key.toLowerCase()) return s
  }
  return null
}

/** The composer listens for this and toggles its microphone. */
export const DICTATION_EVENT = 'kloudchat:dictation'
export const toggleDictation = () => window.dispatchEvent(new Event(DICTATION_EVENT))

/** The fenced code blocks in a markdown answer, in order. */
export function codeBlocks(markdown: string): string[] {
  const out: string[] = []
  const re = /```[^\n]*\n([\s\S]*?)```/g
  let m: RegExpExecArray | null
  while ((m = re.exec(markdown))) out.push(m[1].replace(/\n$/, ''))
  return out
}
