/**
 * Keyboard shortcuts: the list the dialog prints is the list the handler
 * matches. Chords follow ChatGPT's; `mod` is ⌘ on a Mac and Ctrl elsewhere.
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
  /** `mod` renders as ⌘ or Ctrl; the last entry is the key itself. */
  keys: string[]
  /** `key` as the browser reports it, compared case-insensitively. */
  key: string
  mod?: boolean
  shift?: boolean
}

export const SHORTCUTS: Shortcut[] = [
  { id: 'new-chat', label: '새 대화 열기', keys: ['mod', 'Shift', 'O'], key: 'o', mod: true, shift: true },
  { id: 'focus-composer', label: '입력창에 집중', keys: ['Shift', 'Esc'], key: 'Escape', shift: true },
  { id: 'copy-last-code', label: '마지막 코드 블록 복사', keys: ['mod', 'Shift', ';'], key: ';', mod: true, shift: true },
  { id: 'copy-last-answer', label: '마지막 답변 복사', keys: ['mod', 'Shift', 'C'], key: 'c', mod: true, shift: true },
  { id: 'personalization', label: '개인 맞춤 설정', keys: ['mod', 'Shift', 'I'], key: 'i', mod: true, shift: true },
  { id: 'toggle-dictation', label: '말로 쓰기 시작·끝내기', keys: ['mod', 'Shift', 'M'], key: 'm', mod: true, shift: true },
  { id: 'toggle-sidebar', label: '사이드바 토글', keys: ['mod', 'Shift', 'S'], key: 's', mod: true, shift: true },
  { id: 'delete-conversation', label: '대화 삭제', keys: ['mod', 'Shift', '⌫'], key: 'Backspace', mod: true, shift: true },
  { id: 'search', label: '대화 검색', keys: ['mod', 'K'], key: 'k', mod: true },
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

/** The chord a keydown is, or null. Shift+; arrives as `:` on US layouts. */
export function shortcutFor(e: KeyboardEvent): Shortcut | null {
  const mod = isMac() ? e.metaKey : e.ctrlKey
  const key = e.key === ':' ? ';' : e.key
  for (const s of SHORTCUTS) {
    if (Boolean(s.mod) !== mod) continue
    if (Boolean(s.shift) !== e.shiftKey) continue
    if (e.altKey) continue
    if (key.toLowerCase() === s.key.toLowerCase()) return s
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
