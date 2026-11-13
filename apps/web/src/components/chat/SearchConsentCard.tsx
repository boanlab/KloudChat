import { Globe, Pencil, Plus, ShieldCheck, X } from 'lucide-react'
import { useState } from 'react'
import { Button, Input } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useT, useTIn } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { SearchConsent, SearchConsentQuery } from '@/types'

const MAX_QUERIES = 5
const MAX_CHARS = 100

/**
 * A strict-local turn that stopped before searching the web (`services/search_consent`): the
 * queries that would leave — masked, with the ones withheld and why — and the person's choice.
 * The card lives on the stored answer, so a reload shows it until it is answered.
 */
export function SearchConsentCard({
  sessionId,
  messageId,
  consent,
  actionable,
}: {
  sessionId: string
  messageId: string
  consent: SearchConsent
  /** The newest answer, with nothing running: only then can it be answered. */
  actionable: boolean
}) {
  const t = useT()
  const tCard = useTIn('searchConsent')
  const answer = useStore((s) => s.answerSearchConsent)
  const queries = consent.queries.filter(
    (q): q is SearchConsentQuery => typeof q === 'object' && q !== null,
  )
  const sendable = queries.filter((q) => !q.blocked)
  const [editing, setEditing] = useState<string[] | null>(null)
  const [remember, setRemember] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const document = consent.scope === 'document'

  const decide = async (decision: 'allow' | 'deny', edited?: string[]) => {
    setBusy(true)
    setError(null)
    try {
      await answer(sessionId, messageId, {
        decision,
        ...(edited ? { queries: edited.map((q) => q.trim()).filter(Boolean) } : {}),
        ...(decision === 'allow' && remember ? { remember: true } : {}),
      })
    } catch (err) {
      setError(errorMessage(err, t('답을 보내지 못했습니다. 다시 시도해 주세요.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      aria-label={t('외부 검색 확인')}
      data-testid="search-consent"
      className="mt-1 rounded-card border border-accent/30 bg-accent/5 p-3 text-base"
    >
      <p className="flex items-start gap-2 font-medium">
        <Globe size={16} className="mt-0.5 shrink-0 text-accent" />
        <span className="min-w-0">
          {t(document
            ? '자료를 찾으려면 외부 검색이 필요합니다. 아래 검색어만 밖으로 나갑니다. 프로젝트 자료 내용은 보내지 않습니다.'
            : '이 질문에 답하려면 외부 검색이 필요합니다. 아래 검색어만 밖으로 나갑니다. 프로젝트 자료 내용은 보내지 않습니다.')}
        </span>
      </p>

      {editing ? (
        <div className="mt-2 space-y-1.5">
          {editing.map((value, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <Input
                aria-label={t('검색어 {n}').replace('{n}', String(i + 1))}
                value={value}
                maxLength={MAX_CHARS}
                onChange={(e) => setEditing(editing.map((v, k) => (k === i ? e.target.value : v)))}
                className="min-w-0 flex-1"
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={t('검색어 빼기')}
                disabled={editing.length <= 1}
                onClick={() => setEditing(editing.filter((_, k) => k !== i))}
              >
                <X size={14} />
              </Button>
            </div>
          ))}
          {editing.length < MAX_QUERIES && (
            <Button variant="ghost" size="sm" onClick={() => setEditing([...editing, ''])}>
              <Plus size={13} />
              {t('검색어 더하기')}
            </Button>
          )}
          <p className="text-sm text-muted">
            {t('고친 검색어도 보내기 전에 다시 검사합니다. 개인정보는 가리고, 비밀 값이나 자료 문장은 보내지 않습니다.')}
          </p>
        </div>
      ) : (
        <ul className="mt-2 space-y-1" aria-label={t('보낼 검색어')}>
          {queries.map((q, i) => (
            <li
              key={i}
              className="flex flex-wrap items-baseline gap-x-2 rounded-control border border-line bg-panel px-2.5 py-1.5"
            >
              <span className={q.blocked ? 'text-muted line-through' : 'font-mono text-sm'}>{q.text}</span>
              {q.masked > 0 && !q.blocked && (
                <span className="text-sm text-muted">{t('개인정보 가림')}</span>
              )}
              {q.blocked && (
                <span className="basis-full text-sm text-warn">{t(q.reason || '보내지 않습니다.')}</span>
              )}
            </li>
          ))}
        </ul>
      )}

      {actionable ? (
        <>
          {!editing && (
            <label className="mt-2 flex items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
              />
              {t('이 대화에서는 계속 허용')}
            </label>
          )}
          <div className="mt-2 flex flex-wrap gap-1.5">
            {editing ? (
              <>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy || !editing.some((q) => q.trim())}
                  onClick={() => void decide('allow', editing)}
                >
                  <ShieldCheck size={13} />
                  {t('고친 검색어로 허용')}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing(null)}>
                  {t('취소')}
                </Button>
              </>
            ) : (
              <>
                <Button
                  size="sm"
                  variant="primary"
                  disabled={busy || sendable.length === 0}
                  onClick={() => void decide('allow')}
                >
                  <ShieldCheck size={13} />
                  {tCard('허용')}
                </Button>
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => setEditing(queries.length ? queries.map((q) => (q.blocked ? '' : q.text)) : [''])}
                >
                  <Pencil size={13} />
                  {t('검색어 고치고 허용')}
                </Button>
                <Button size="sm" variant="ghost" disabled={busy} onClick={() => void decide('deny')}>
                  {t('이번엔 자료만으로')}
                </Button>
              </>
            )}
          </div>
          {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
        </>
      ) : (
        <p className="mt-2 text-sm text-muted">{t('답하지 않은 확인 요청입니다. 아무것도 보내지 않았습니다.')}</p>
      )}
    </section>
  )
}
