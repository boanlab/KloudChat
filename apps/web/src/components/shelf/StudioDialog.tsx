import { Loader2, ShieldCheck, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Field, Input, Modal, Select } from '@/components/ui'
import { ApiError, errorMessage, shelfApi } from '@/lib/api'
import type { StudioEstimate, StudioRequest } from '@/lib/shelf'
import { STUDIO_KINDS, type StudioKind } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { formatTokens } from '@/lib/utils'
import { shelfFailure } from './failures'

const DOCUMENT_FORMATS: { id: 'web' | 'hwp' | 'docx'; label: string }[] = [
  { id: 'web', label: '웹 보고서' },
  { id: 'hwp', label: '한글' },
  { id: 'docx', label: 'Word' },
]

/**
 * One studio output's options and its estimate (sources read, model, tokens, credits) before
 * anything is generated; 만들기 starts it.
 */
export function StudioDialog({
  projectId,
  kind,
  sourceIds,
  onClose,
  onStarted,
}: {
  projectId: string
  kind: StudioKind | null
  sourceIds: string[]
  onClose: () => void
  onStarted: () => void
}) {
  const t = useT()
  const [options, setOptions] = useState<StudioRequest['options']>({})
  const [estimate, setEstimate] = useState<StudioEstimate | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!kind) return
    setOptions(kind === 'quiz' ? { count: 10 } : kind === 'flashcards' ? { count: 15 } : { format: 'web' })
  }, [kind])

  const request = kind ? { kind, sourceIds, options } : null
  const key = JSON.stringify(request)
  useEffect(() => {
    if (!request) return
    let alive = true
    setEstimate(null)
    setError(null)
    const timer = window.setTimeout(() => {
      shelfApi
        .estimate(projectId, request)
        .then((found) => alive && setEstimate(found))
        .catch((err) => alive && setError(err instanceof ApiError ? shelfFailure(err.detail, t) : errorMessage(err, t('예상치를 내지 못했습니다.'))))
    }, 250)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // `key` stands for the request
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, projectId, t])

  if (!kind || !request) return null
  const meta = STUDIO_KINDS[kind]
  const start = async () => {
    setBusy(true)
    setError(null)
    try {
      await shelfApi.start(projectId, request)
      onStarted()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? shelfFailure(err.detail, t) : errorMessage(err, t('시작하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }
  const set = (patch: Partial<StudioRequest['options']>) => setOptions((current) => ({ ...current, ...patch }))

  return (
    <Modal
      open
      onClose={() => !busy && onClose()}
      title={t(meta.name)}
      description={t(meta.about)}
      footer={
        <>
          <Button onClick={onClose} disabled={busy}>{t('취소')}</Button>
          <Button variant="primary" disabled={!sourceIds.length || !estimate || busy} onClick={() => void start()}>
            {busy && <Loader2 size={14} className="animate-spin" aria-hidden />}
            {t('만들기')}
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        {(kind === 'study_guide' || kind === 'faq' || kind === 'briefing' || kind === 'timeline' || kind === 'glossary') && (
          <Field label={t('저장 형식')}>
            <Select value={options.format ?? 'web'} onChange={(e) => set({ format: e.target.value as 'web' | 'hwp' | 'docx' })}>
              {DOCUMENT_FORMATS.map((f) => (
                <option key={f.id} value={f.id}>{t(f.label)}</option>
              ))}
            </Select>
          </Field>
        )}
        {(kind === 'quiz' || kind === 'flashcards') && (
          <Field label={kind === 'quiz' ? t('문제 수') : t('카드 수')}>
            <Input
              type="number"
              inputMode="numeric"
              min={kind === 'quiz' ? 3 : 5}
              max={kind === 'quiz' ? 30 : 40}
              value={options.count ?? ''}
              onChange={(e) => set({ count: Number(e.target.value) || undefined })}
            />
          </Field>
        )}
        <Field label={t('언어')}>
          <Select value={options.language ?? 'auto'} onChange={(e) => set({ language: e.target.value as 'auto' | 'ko' | 'en' })}>
            <option value="auto">{t('자료의 언어로')}</option>
            <option value="ko">{t('한국어')}</option>
            <option value="en">{t('영어')}</option>
          </Select>
        </Field>
        <Field label={t('초점 (선택)')} className="sm:col-span-2">
          <Input
            value={options.focus ?? ''}
            maxLength={300}
            placeholder={t('예: 기말고사 범위인 5~8강 중심으로')}
            onChange={(e) => set({ focus: e.target.value || undefined })}
          />
        </Field>
      </div>
      <div className="rounded-control border border-line bg-elevated px-3 py-2.5 text-sm" data-testid="studio-estimate">
        {!estimate && !error && (
          <p className="flex items-center gap-2 text-muted">
            <Loader2 size={13} className="animate-spin" />
            {t('분량과 비용을 계산하는 중…')}
          </p>
        )}
        {estimate && (
          <div className="space-y-1.5">
            <p className="flex flex-wrap items-center gap-1.5">
              {estimate.strict ? (
                <Badge tone="success"><ShieldCheck size={11} />strict-local</Badge>
              ) : (
                <Badge tone="warn">{t('바깥 모델')}</Badge>
              )}
              <span className="font-medium">{estimate.modelLabel}</span>
              {estimate.thinking && <Badge>{t('생각하며 씀')}</Badge>}
            </p>
            {estimate.thinking && (
              <p className="text-muted">{t('생각하며 쓰므로 몇 분 걸릴 수 있습니다. 다 되면 아래 목록에 나타납니다.')}</p>
            )}
            <p>
              {t('자료 {n}개 · 읽는 양 약 {in} 토큰 · 쓰는 양 약 {out} 토큰')
                .replace('{n}', String(estimate.sources))
                .replace('{in}', formatTokens(estimate.inputTokens))
                .replace('{out}', formatTokens(estimate.outputTokens))}
            </p>
            <p className="font-medium">
              {estimate.credits > 0
                ? t('약 {n} 크레딧 (최대 {max})').replace('{n}', String(estimate.credits)).replace('{max}', String(estimate.maxCredits))
                : t('크레딧 없음 (안에서 돌아가는 모델)')}
            </p>
            {estimate.partial.length > 0 && (
              <p className="flex items-start gap-1.5 text-warn">
                <TriangleAlert size={13} className="mt-0.5 shrink-0" />
                {t('분량이 커서 다음 자료는 고르게 일부만 읽습니다: {names}').replace('{names}', estimate.partial.join(', '))}
              </p>
            )}
          </div>
        )}
        {error && <p role="alert" className="text-danger">{error}</p>}
      </div>
      {!sourceIds.length && <p role="alert" className="text-sm text-danger">{t('자료를 하나 이상 고르세요.')}</p>}
    </Modal>
  )
}
