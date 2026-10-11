import {
  ArrowLeft,
  ArrowUp,
  Ban,
  Bot,
  Loader2,
  Menu,
  MessageSquarePlus,
  PauseCircle,
  Send,
  ShieldCheck,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  LogIn,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Markdown } from '@/components/chat/Markdown'
import { Button, ConfirmDialog, Textarea } from '@/components/ui'
import {
  ApiError,
  botsApi,
  streamBot,
  type BotConversationRow,
  type BotPage as BotPageData,
  type BotTranscriptMessage,
} from '@/lib/api'
import { botErrorMessage, noticeLines, PAUSE_LABEL } from '@/lib/publishing'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Step } from '@/types'
import { isImeKey } from '@/lib/ime'

interface Turn extends BotTranscriptMessage {
  /** Still streaming. */
  live?: boolean
  error?: string
}

/** Keeps search engines off a bot that is not listed for everyone (a member's or a link's). */
function useNoIndex(on: boolean) {
  useEffect(() => {
    if (!on) return
    const meta = document.createElement('meta')
    meta.name = 'robots'
    meta.content = 'noindex, nofollow'
    document.head.appendChild(meta)
    return () => meta.remove()
  }, [on])
}

/** A chat bot someone opened — a member, or anyone once an administrator opened it outside:
 *  the notice first, then questions to the bot's fixed version. `embedded`: framed on a site
 *  an administrator approved, always without an account. */
export function BotPage({ onSignIn, embedded = false }: { onSignIn?: () => void; embedded?: boolean }) {
  const t = useT()
  const { slug = '' } = useParams()
  const signedIn = useStore((s) => s.authenticated)
  const authenticated = signedIn && !embedded
  const [bot, setBot] = useState<BotPageData | null>(null)
  const [missing, setMissing] = useState(false)
  const [rows, setRows] = useState<BotConversationRow[]>([])
  const [current, setCurrent] = useState<BotConversationRow | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [listOpen, setListOpen] = useState(false)
  const [offerShare, setOfferShare] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const stopper = useRef<AbortController | null>(null)
  const seq = useRef(0)
  const end = useRef<HTMLDivElement>(null)

  const loadList = useCallback(async () => {
    try {
      setRows(await botsApi.conversations(slug))
    } catch {
      /* the list is a convenience; the page still works */
    }
  }, [slug])

  useEffect(() => {
    let live = true
    botsApi
      .page(slug)
      .then((page) => {
        if (!live) return
        setBot(page)
        setMissing(false)
      })
      .catch((err) => live && (err instanceof ApiError && err.status === 404 ? setMissing(true) : setError(t('챗봇을 불러오지 못했습니다.'))))
    void loadList()
    return () => {
      live = false
    }
    // Signing in on this page reopens it as a member.
  }, [slug, loadList, t, authenticated])

  useNoIndex(bot?.visibility !== 'external')

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [turns])

  const open = async (row: BotConversationRow) => {
    setListOpen(false)
    setError(null)
    setOfferShare(false)
    try {
      const got = await botsApi.conversation(slug, row.id)
      setCurrent({ id: got.id, title: got.title, sharedAt: got.sharedAt, updatedAt: row.updatedAt })
      setTurns(got.messages)
    } catch {
      setError(t('대화를 불러오지 못했습니다.'))
    }
  }

  const fresh = () => {
    setCurrent(null)
    setTurns([])
    setError(null)
    setOfferShare(false)
    setListOpen(false)
  }

  const consent = async () => {
    if (!bot) return
    try {
      await botsApi.consent(slug, bot.consentVersion)
      setBot({ ...bot, consented: true })
    } catch (err) {
      setError(botErrorMessage(err, t))
    }
  }

  const ask = async (question: string) => {
    if (!bot || busy || !question.trim()) return
    if (question.length > bot.maxInputChars) {
      setError(t('질문이 너무 깁니다. {n}자 안으로 줄여 주세요.').replace('{n}', String(bot.maxInputChars)))
      return
    }
    setError(null)
    setOfferShare(false)
    setBusy(true)
    setText('')
    let conversation = current
    const now = new Date().toISOString()
    seq.current += 1
    const answerId = `pending-${seq.current}`
    const questionId = `q-${seq.current}`
    setTurns((all) => [
      ...all,
      { id: questionId, role: 'user', content: question, rating: null, createdAt: now },
      { id: answerId, role: 'assistant', content: '', rating: null, createdAt: now, steps: [], live: true },
    ])
    const update = (fn: (turn: Turn) => Turn) =>
      setTurns((all) => all.map((turn) => (turn.id === answerId ? fn(turn) : turn)))
    try {
      if (!conversation) {
        conversation = await botsApi.start(slug)
        setCurrent(conversation)
      }
      const controller = new AbortController()
      stopper.current = controller
      for await (const event of streamBot(slug, conversation.id, question, controller.signal)) {
        if (event.type === 'delta') update((turn) => ({ ...turn, content: turn.content + event.text }))
        else if (event.type === 'retract') update((turn) => ({ ...turn, content: turn.content.replace(event.text, '') }))
        else if (event.type === 'step') {
          const step = event as unknown as Step
          update((turn) => {
            const steps = [...(turn.steps ?? [])]
            const at = steps.findIndex((s) => s.id === step.id)
            if (at >= 0) steps[at] = { ...steps[at], ...step }
            else steps.push(step)
            return { ...turn, steps }
          })
        } else if (event.type === 'title') {
          const title = event.title
          setCurrent((c) => (c ? { ...c, title } : c))
        } else if (event.type === 'error') {
          update((turn) => ({ ...turn, error: event.message || t('답을 받지 못했습니다. 다시 시도해 주세요.') }))
        } else if (event.type === 'done') {
          const id = event.messageId
          update((turn) => ({ ...turn, live: false, ...(id ? { id } : {}) }))
        }
      }
    } catch (err) {
      const aborted = err instanceof DOMException && err.name === 'AbortError'
      if (!aborted) {
        setError(botErrorMessage(err, t, bot.maxInputChars))
        if (err instanceof ApiError && err.detail === 'consent_required') setBot({ ...bot, consented: false })
        if (err instanceof ApiError && err.detail === 'bot_paused') setBot({ ...bot, status: 'paused' })
        if (err instanceof ApiError && err.detail === 'visitor_blocked') setBot({ ...bot, blocked: true })
        // The question did not go through: it goes back in the box.
        setTurns((all) => all.filter((turn) => turn.id !== answerId && turn.id !== questionId))
        setText(question)
      }
    } finally {
      stopper.current = null
      setBusy(false)
      update((turn) => ({ ...turn, live: false }))
      void loadList()
      if (conversation) {
        // The stored turn: its id for 👍👎, its words as kept.
        try {
          const got = await botsApi.conversation(slug, conversation.id)
          setTurns(got.messages)
          setCurrent((c) => (c ? { ...c, title: got.title, sharedAt: got.sharedAt } : c))
        } catch {
          /* the streamed text stays */
        }
      }
    }
  }

  const stop = async () => {
    stopper.current?.abort()
    if (current) await botsApi.stop(slug, current.id).catch(() => undefined)
  }

  const rate = async (turn: Turn, rating: 'up' | 'down') => {
    const next = turn.rating === rating ? null : rating
    setTurns((all) => all.map((x) => (x.id === turn.id ? { ...x, rating: next } : x)))
    try {
      await botsApi.rate(slug, turn.id, next)
      if (next === 'down' && current && !current.sharedAt) setOfferShare(true)
    } catch {
      setTurns((all) => all.map((x) => (x.id === turn.id ? { ...x, rating: turn.rating } : x)))
    }
  }

  const share = async (on: boolean) => {
    if (!current) return
    try {
      const done = on ? await botsApi.share(slug, current.id) : await botsApi.unshare(slug, current.id)
      setCurrent({ ...current, sharedAt: done.sharedAt })
      setOfferShare(false)
      void loadList()
    } catch {
      setError(t('보내지 못했습니다. 다시 시도해 주세요.'))
    }
  }

  const remove = async () => {
    if (!current) return
    await botsApi.remove(slug, current.id).catch(() => undefined)
    setDeleting(false)
    fresh()
    void loadList()
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !isImeKey(e)) {
      e.preventDefault()
      void ask(text)
    }
  }

  if (missing) {
    return (
      <div className="mx-auto flex min-h-dvh max-w-md flex-col justify-center px-6 text-center">
        <Bot size={22} className="mx-auto mb-3 text-faint" aria-hidden />
        <h1 className="text-lg font-semibold">{t('열 수 없는 챗봇입니다')}</h1>
        <p className="mt-1.5 text-base text-muted">
          {authenticated || embedded
            ? t('닫혔거나 주소가 올바르지 않습니다.')
            : t('닫혔거나 주소가 올바르지 않습니다. 구성원에게만 열린 챗봇이면 로그인해야 합니다.')}
        </p>
        {embedded ? null : authenticated ? (
          <Link to="/bots" className="mt-4 text-base text-accent hover:underline">
            {t('챗봇 목록으로')}
          </Link>
        ) : (
          <div className="mt-4 flex flex-wrap justify-center gap-3">
            {onSignIn && (
              <Button variant="primary" onClick={onSignIn}>
                <LogIn size={14} />
                {t('로그인')}
              </Button>
            )}
            <Link to="/b" className="inline-flex h-9 items-center text-base text-accent hover:underline">
              {t('공개 챗봇 목록')}
            </Link>
          </div>
        )}
      </div>
    )
  }
  if (!bot) {
    return (
      <div role="status" className="flex min-h-dvh items-center justify-center gap-2 text-base text-faint">
        <Loader2 size={15} className="animate-spin" />
        {error ?? t('불러오는 중…')}
      </div>
    )
  }

  const paused = bot.status !== 'approved'
  const closed = paused || bot.blocked
  // A member goes back to the members' list; a visitor without an account to the public one
  // when the bot is listed there, and nowhere when it is reached by link only.
  // Framed on another site, the frame is the whole page: nowhere to go back to.
  const backTo = embedded ? null : bot.signedIn ? '/bots' : bot.visibility === 'external' ? '/b' : null
  const prompts = bot.branding.suggestedPrompts
  const list = (
    <nav aria-label={t('내 대화')} className="flex h-full flex-col gap-2">
      <Button variant="secondary" onClick={fresh} className="w-full justify-start" disabled={!bot.consented}>
        <MessageSquarePlus size={15} />
        {t('새 대화')}
      </Button>
      <p className="px-1 pt-2 text-sm font-medium text-faint">{t('내 대화')}</p>
      <ul className="min-h-0 flex-1 space-y-0.5 overflow-y-auto" data-testid="bot-conversations">
        {rows.length === 0 && <li className="px-1 text-sm text-faint">{t('아직 대화가 없습니다.')}</li>}
        {rows.map((row) => (
          <li key={row.id}>
            <button
              type="button"
              onClick={() => void open(row)}
              aria-current={current?.id === row.id ? 'true' : undefined}
              className={cn(
                'w-full truncate rounded-control px-2.5 py-1.5 text-left text-base transition-colors',
                current?.id === row.id ? 'bg-selected font-medium text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
              )}
            >
              {row.title || t('새 대화')}
            </button>
          </li>
        ))}
      </ul>
      <p className="px-1 text-xs text-faint">
        {t('대화는 마지막 질문 뒤 {n}일이 지나면 지워집니다.').replace('{n}', String(bot.retentionDays))}
      </p>
    </nav>
  )

  return (
    <div className="flex h-dvh flex-col bg-bg text-fg">
      <header
        className="flex items-center gap-3 border-b border-line px-4 py-3"
        style={{ paddingTop: 'max(0.75rem, env(safe-area-inset-top))' }}
      >
        {backTo && (
          <Link to={backTo} className="grid size-9 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg" aria-label={t('챗봇 목록으로')}>
            <ArrowLeft size={17} />
          </Link>
        )}
        <span className="grid size-9 shrink-0 place-items-center rounded-control text-white" style={{ backgroundColor: bot.color }} aria-hidden>
          <Bot size={17} />
        </span>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-md font-semibold">{bot.title}</h1>
          <p className="truncate text-sm text-faint">
            {bot.ownerName ? t('만든 사람: {name}').replace('{name}', bot.ownerName) : bot.description}
            {bot.ownerName && bot.description && <span className="max-sm:hidden"> · {bot.description}</span>}
          </p>
        </div>
        <Button variant="ghost" size="icon" className="sm:hidden" onClick={() => setListOpen((v) => !v)} aria-label={t('내 대화')} aria-expanded={listOpen}>
          <Menu size={17} />
        </Button>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="w-64 shrink-0 border-r border-line p-3 max-sm:hidden">{list}</aside>
        {listOpen && (
          <div className="fixed inset-0 z-40 sm:hidden" role="dialog" aria-label={t('내 대화')}>
            <div className="absolute inset-0 bg-black/40" onClick={() => setListOpen(false)} />
            <div className="absolute inset-y-0 left-0 w-72 max-w-[85vw] bg-panel p-3 shadow-float">{list}</div>
          </div>
        )}

        <main className="flex min-w-0 flex-1 flex-col">
          {current && turns.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium">{current.title || t('새 대화')}</span>
              {current.sharedAt ? (
                <span className="flex items-center gap-2 text-success" data-testid="bot-shared">
                  {t('만든 사람에게 보냈습니다')}
                  <button type="button" className="text-muted underline hover:text-fg" onClick={() => void share(false)}>
                    {t('보내기 취소')}
                  </button>
                </span>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => void share(true)} disabled={busy}>
                  <Send size={13} />
                  {t('만든 사람에게 보내기')}
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setDeleting(true)} aria-label={t('이 대화 지우기')} disabled={busy}>
                <Trash2 size={13} />
              </Button>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6">
            <div className="mx-auto max-w-3xl space-y-6">
              {bot.blocked && (
                <p role="status" className="flex items-start gap-2 rounded-card border border-danger/30 bg-danger/5 px-4 py-3 text-base text-danger" data-testid="bot-blocked">
                  <Ban size={16} className="mt-0.5 shrink-0" aria-hidden />
                  {t('이 챗봇을 쓸 수 없습니다.')}
                </p>
              )}
              {paused && !bot.blocked && (
                <p role="status" className="flex items-start gap-2 rounded-card border border-warn/30 bg-warn/5 px-4 py-3 text-base text-warn" data-testid="bot-paused">
                  <PauseCircle size={16} className="mt-0.5 shrink-0" aria-hidden />
                  <span>
                    {t('지금은 이 챗봇을 쓸 수 없습니다.')}
                    {bot.mine && bot.pauseReason && ` ${t(PAUSE_LABEL[bot.pauseReason] ?? '')}`}
                  </span>
                </p>
              )}
              {!bot.consented && closed ? null : !bot.consented ? (
                <section aria-label={t('이용 안내')} className="rounded-card border border-line bg-panel px-5 py-4" data-testid="bot-notice">
                  <p className="mb-2 flex items-center gap-2 text-base font-medium">
                    <ShieldCheck size={16} className="text-accent" aria-hidden />
                    {t('이용 안내')}
                  </p>
                  <ul className="list-disc space-y-1 pl-5 text-base text-muted">
                    {noticeLines(bot.conversationView, bot.retentionDays, t, bot.signedIn).map((line) => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                  <Button variant="primary" className="mt-4" onClick={() => void consent()}>
                    {bot.conversationView === 'on' ? t('동의하고 시작') : t('확인하고 시작')}
                  </Button>
                </section>
              ) : turns.length === 0 ? (
                <section className="space-y-4 pt-6 text-center">
                  <p className="text-lg font-semibold">{bot.title}</p>
                  {bot.branding.greeting && <p className="mx-auto max-w-xl whitespace-pre-wrap text-base text-muted">{bot.branding.greeting}</p>}
                  {prompts.length > 0 && !closed && (
                    <div className="flex flex-wrap justify-center gap-2" data-testid="bot-prompts">
                      {prompts.map((line) => (
                        <button
                          key={line}
                          type="button"
                          onClick={() => void ask(line)}
                          className="max-w-full rounded-full border border-line bg-panel px-3.5 py-1.5 text-left text-sm transition-colors hover:border-accent/40 hover:bg-accent-soft"
                        >
                          {line}
                        </button>
                      ))}
                    </div>
                  )}
                </section>
              ) : (
                turns.map((turn) =>
                  turn.role === 'user' ? (
                    <div key={turn.id} className="flex justify-end">
                      <p className="max-w-[85%] rounded-card bg-selected px-4 py-2.5 text-base whitespace-pre-wrap">{turn.content}</p>
                    </div>
                  ) : (
                    <article key={turn.id} className="space-y-2" aria-busy={turn.live || undefined}>
                      {(turn.steps?.length ?? 0) > 0 && (
                        <ul className="space-y-0.5 text-sm text-faint" data-testid="bot-steps">
                          {turn.steps!.map((step) => (
                            <li key={step.id} className="flex items-center gap-1.5">
                              {step.status === 'running' ? <Loader2 size={12} className="animate-spin" /> : <span aria-hidden>·</span>}
                              {step.label}
                            </li>
                          ))}
                        </ul>
                      )}
                      {turn.content ? (
                        <Markdown>{turn.content}</Markdown>
                      ) : turn.live ? (
                        <p className="flex items-center gap-2 text-base text-faint">
                          <Loader2 size={14} className="animate-spin" />
                          {t('답을 쓰는 중…')}
                        </p>
                      ) : null}
                      {turn.error && <p className="text-base text-danger">{turn.error}</p>}
                      {!turn.live && turn.content && !turn.id.startsWith('pending-') && (
                        <div className="flex items-center gap-1" data-testid="bot-rating">
                          <Button
                            size="icon"
                            variant="ghost"
                            aria-label={t('도움이 됐어요')}
                            aria-pressed={turn.rating === 'up'}
                            onClick={() => void rate(turn, 'up')}
                            className={cn(turn.rating === 'up' && 'text-accent')}
                          >
                            <ThumbsUp size={14} />
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            aria-label={t('아쉬워요')}
                            aria-pressed={turn.rating === 'down'}
                            onClick={() => void rate(turn, 'down')}
                            className={cn(turn.rating === 'down' && 'text-danger')}
                          >
                            <ThumbsDown size={14} />
                          </Button>
                        </div>
                      )}
                    </article>
                  ),
                )
              )}
              {offerShare && current && !current.sharedAt && (
                <div className="flex flex-wrap items-center gap-2 rounded-card border border-line bg-elevated px-4 py-3 text-base" data-testid="bot-offer-share">
                  <span className="min-w-0 flex-1">{t('이 대화를 만든 사람에게 보낼까요? 개인정보는 가려서 보입니다.')}</span>
                  <Button size="sm" variant="primary" onClick={() => void share(true)}>
                    {t('보내기')}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setOfferShare(false)}>
                    {t('괜찮아요')}
                  </Button>
                </div>
              )}
              <div ref={end} />
            </div>
          </div>

          {/* A bot that cannot answer now takes no question: only the notice above. */}
          {!closed && (
          <footer className="border-t border-line px-4 pt-3" style={{ paddingBottom: 'max(0.75rem, env(safe-area-inset-bottom))' }}>
            <div className="mx-auto max-w-3xl">
              {error && (
                <p role="alert" className="mb-2 text-base text-danger">
                  {error}
                </p>
              )}
              <div className="flex items-end gap-2">
                <Textarea
                  rows={2}
                  value={text}
                  disabled={!bot.consented || closed}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={onKey}
                  aria-label={t('질문 입력')}
                  placeholder={bot.branding.placeholder || t('질문을 입력하세요')}
                  className="max-h-48 min-h-11"
                />
                {busy ? (
                  <Button variant="secondary" size="icon" className="size-11" onClick={() => void stop()} aria-label={t('중단')}>
                    <Square size={15} />
                  </Button>
                ) : (
                  <Button
                    variant="primary"
                    size="icon"
                    className="size-11"
                    disabled={!bot.consented || closed || !text.trim()}
                    onClick={() => void ask(text)}
                    aria-label={t('보내기')}
                  >
                    <ArrowUp size={17} />
                  </Button>
                )}
              </div>
              <p className="mt-1.5 flex flex-wrap justify-between gap-2 text-xs text-faint">
                <span>{t('입력 내용은 만든 사람 계정으로 처리·기록됩니다. 개인정보를 입력하지 마세요.')}</span>
                <span className={cn(text.length > bot.maxInputChars && 'text-danger')}>
                  {text.length.toLocaleString()} / {bot.maxInputChars.toLocaleString()}
                </span>
              </p>
            </div>
          </footer>
          )}
        </main>
      </div>
      <ConfirmDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        onConfirm={() => void remove()}
        title={t('이 대화 지우기')}
        description={t('되돌릴 수 없습니다.')}
      />
    </div>
  )
}
