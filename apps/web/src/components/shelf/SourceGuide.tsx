import { Loader2, MessageCircleQuestion, RefreshCw, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button } from '@/components/ui'
import { ApiError, errorMessage, shelfApi } from '@/lib/api'
import { poll, type SourceGuide as Guide } from '@/lib/shelf'
import { withNames } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { shelfFailure } from './failures'

/**
 * A source's (or the project's, `subject="project"`) guide: summary, key topics and suggested
 * questions. Made by a strict-local model the first time it is asked for; a question goes to
 * `onAsk` (dropped into the composer, never sent on the person's behalf).
 */
export function SourceGuide({
  projectId,
  subject,
  onAsk,
  compact = false,
}: {
  projectId: string
  subject: string
  onAsk?: (question: string) => void
  compact?: boolean
}) {
  const t = useT()
  const [guide, setGuide] = useState<Guide | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [asking, setAsking] = useState(false)

  useEffect(() => {
    let alive = true
    setGuide(null)
    setError(null)
    shelfApi
      .guide(projectId, subject)
      .then((found) => alive && setGuide(found))
      .catch((err) => alive && setError(errorMessage(err, t('안내를 불러오지 못했습니다.'))))
    return () => {
      alive = false
    }
  }, [projectId, subject, t])

  const running = guide?.state === 'running'
  useEffect(() => {
    if (!running) return
    return poll(() => shelfApi.guide(projectId, subject), (g) => g.state === 'running', setGuide)
  }, [running, projectId, subject])

  const make = async (force = false) => {
    setAsking(true)
    setError(null)
    try {
      setGuide(await shelfApi.makeGuide(projectId, subject, force))
    } catch (err) {
      setError(shelfFailure(err instanceof ApiError ? err.detail : undefined, t))
    } finally {
      setAsking(false)
    }
  }

  if (!guide && !error) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted">
        <Loader2 size={13} className="animate-spin" />
        {t('불러오는 중…')}
      </p>
    )
  }
  const made = guide?.state === 'done'
  return (
    <div className="space-y-2.5" data-testid="source-guide" data-state={guide?.state}>
      {guide?.summary && <p className="text-base leading-relaxed">{guide.summary}</p>}
      {!!guide?.topics.length && (
        <div className="flex flex-wrap gap-1">
          {guide.topics.map((topic) => (
            <Badge key={topic}>{topic}</Badge>
          ))}
        </div>
      )}
      {!!guide?.questions.length && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-faint">{t('이렇게 물어보세요')}</p>
          {guide.questions.map((question) => (
            <button
              key={question}
              type="button"
              disabled={!onAsk}
              onClick={() => onAsk?.(question)}
              className="flex w-full items-start gap-1.5 rounded-control border border-line px-2.5 py-1.5 text-left text-sm hover:border-accent hover:bg-accent-soft disabled:pointer-events-none"
            >
              <MessageCircleQuestion size={13} className="mt-0.5 shrink-0 text-accent" />
              <span>{question}</span>
            </button>
          ))}
        </div>
      )}
      {running && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted">
          <Loader2 size={13} className="animate-spin" />
          {withNames(t('{guide}를 만드는 중… (자료는 안에서만 읽습니다)'), t)}
        </p>
      )}
      {guide?.state === 'failed' && (
        <p role="alert" className="text-sm text-danger">{shelfFailure(guide.error, t)}</p>
      )}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {!running && (
        <div className="flex gap-2">
          {!made ? (
            <Button size="sm" disabled={asking} onClick={() => void make(guide?.state === 'failed')}>
              <Sparkles size={13} />
              {compact ? t('추천 질문 만들기') : withNames(t('{guide} 만들기'), t)}
            </Button>
          ) : (
            <Button variant="ghost" size="sm" disabled={asking} onClick={() => void make(true)}>
              <RefreshCw size={13} />
              {t('다시 만들기')}
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
