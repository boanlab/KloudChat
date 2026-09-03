import { CircleCheck } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ConfirmDialog, Modal } from '@/components/ui'
import { copyText } from '@/lib/clipboard'
import { COMPOSER_KEYS, SHORTCUTS, codeBlocks, isMac, shortcutFor, toggleDictation } from '@/lib/shortcuts'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/** Opens the dialog from anywhere — the account menu, say. */
const OPEN_EVENT = 'kloudchat:shortcuts'
export const openShortcuts = () => window.dispatchEvent(new Event(OPEN_EVENT))

/** One key as a cap. `mod` is drawn as the platform's own. */
function Key({ children }: { children: ReactNode }) {
  return (
    <kbd className="inline-flex h-7 min-w-7 items-center justify-center rounded-control border border-line bg-elevated px-1.5 font-sans text-xs text-muted shadow-[0_1px_0_var(--line)]">
      {children}
    </kbd>
  )
}

function Chord({ keys }: { keys: string[] }) {
  const mac = isMac()
  return (
    <span className="flex shrink-0 items-center gap-1">
      {keys.map((k, i) => (
        <Key key={i}>{k === 'mod' ? (mac ? '⌘' : 'Ctrl') : k}</Key>
      ))}
    </span>
  )
}

/**
 * The shortcuts, and the dialog that lists them.
 *
 * Mounted once in the shell. A chord typed into a text field still counts —
 * every one carries a modifier or Shift+Esc, none of which a field wants —
 * except when a dialog is already up, where Esc and the rest belong to it.
 */
export function KeyboardShortcuts() {
  const t = useT()
  const navigate = useNavigate()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 2_500)
    return () => clearTimeout(timer)
  }, [toast])

  useEffect(() => {
    const onOpen = () => setOpen(true)
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EVENT, onOpen)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const chord = shortcutFor(e)
      if (!chord) return
      // A dialog that is up owns the keyboard; the shortcut dialog itself
      // still answers its own chord so it toggles.
      const dialogUp = document.querySelector('[role="dialog"]') !== null
      if (dialogUp && !(chord.id === 'show-shortcuts' && open)) return
      const state = useStore.getState()
      const session = state.sessions.find((s) => s.id === state.activeSessionId)
      const focus = (selector: string) => {
        const el = document.querySelector<HTMLElement>(selector)
        if (el) el.focus()
        return el !== null
      }
      const lastAnswer = () => {
        const m = [...(session?.messages ?? [])].reverse().find((row) => row.role === 'assistant')
        return m?.content ?? ''
      }
      switch (chord.id) {
        case 'new-chat':
          e.preventDefault()
          navigate('/new/chat')
          requestAnimationFrame(() => focus('[data-composer]'))
          return
        case 'focus-composer':
          if (focus('[data-composer]')) e.preventDefault()
          return
        case 'copy-last-answer': {
          const text = lastAnswer()
          e.preventDefault()
          if (!text) {
            setToast(t('복사할 답변이 없습니다'))
            return
          }
          void copyText(text).then((ok) => setToast(ok ? t('마지막 답변을 복사했습니다') : t('복사하지 못했습니다')))
          return
        }
        case 'copy-last-code': {
          const blocks = codeBlocks(lastAnswer())
          e.preventDefault()
          const code = blocks[blocks.length - 1]
          if (!code) {
            setToast(t('마지막 답변에 코드 블록이 없습니다'))
            return
          }
          void copyText(code).then((ok) => setToast(ok ? t('마지막 코드 블록을 복사했습니다') : t('복사하지 못했습니다')))
          return
        }
        case 'personalization':
          e.preventDefault()
          navigate('/settings/personalization')
          return
        case 'toggle-dictation':
          e.preventDefault()
          toggleDictation()
          return
        case 'toggle-sidebar':
          e.preventDefault()
          state.cycleSidebar()
          return
        case 'delete-conversation':
          if (!session) return
          e.preventDefault()
          setConfirmDelete(true)
          return
        case 'search':
          e.preventDefault()
          if (state.sidebar !== 'full') state.cycleSidebar()
          requestAnimationFrame(() => focus('[data-session-search]'))
          return
        case 'show-shortcuts':
          e.preventDefault()
          setOpen((v) => !v)
          return
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate, open, t])

  const state = useStore.getState()
  const active = state.sessions.find((s) => s.id === state.activeSessionId)

  return (
    <>
      <Modal open={open} onClose={() => setOpen(false)} title={t('키보드 단축키')} width="max-w-2xl">
        <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {SHORTCUTS.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-4 text-base">
              <span>{t(s.label)}</span>
              <Chord keys={s.keys} />
            </div>
          ))}
        </div>
        <p className="mt-5 mb-2 text-xs font-semibold tracking-wide text-faint uppercase">
          {t('입력창에서')}
        </p>
        <div className="grid gap-x-8 gap-y-3 sm:grid-cols-2">
          {COMPOSER_KEYS.map((s) => (
            <div key={s.label} className="flex items-center justify-between gap-4 text-base">
              <span className="min-w-0">
                {t(s.label)}
                {s.note && <span className="block text-sm text-muted">{t(s.note)}</span>}
              </span>
              <Chord keys={s.keys} />
            </div>
          ))}
        </div>
      </Modal>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          const id = useStore.getState().activeSessionId
          setConfirmDelete(false)
          if (!id) return
          void useStore.getState().deleteSession(id)
          if (location.pathname.startsWith('/s/')) navigate('/', { replace: true })
        }}
        title={t('{name} 삭제').replace('{name}', active?.title ?? t('이 대화'))}
        description={t('되돌릴 수 없습니다. 아티팩트와 프로젝트, 메모리는 지워지지 않습니다.')}
      />

      {toast && (
        <div
          role="status"
          className="animate-fade-up absolute bottom-24 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-card border border-line bg-panel px-4 py-2 text-base text-fg shadow-float"
        >
          <CircleCheck size={14} className="text-success" />
          {toast}
        </div>
      )}
    </>
  )
}
