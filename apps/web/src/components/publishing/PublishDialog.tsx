import { AlertTriangle, CheckCircle2, Clock, Copy, ExternalLink, Loader2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Modal } from '@/components/ui'
import { ApiError, errorMessage, publicationsApi, type BotVisibility, type Publication, type PublishPreflight } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import {
  botUrl,
  creditsCap,
  DISABLED_LABEL,
  FINDING_LABEL,
  PROBLEM_LABEL,
  SCOPE_ERROR,
  toSettings,
  VISIBILITY_LABEL,
  type SettingsDraft,
} from '@/lib/publishing'
import { useT } from '@/lib/useT'
import type { Agent } from '@/types'
import { PublicationSettingsForm } from './PublicationSettingsForm'
import { ScopePicker } from './ScopePicker'

/** 「챗봇으로 공개」: publish check, audience, settings, address; link or listing asked of an admin. */
export function PublishDialog({
  agent,
  open,
  onClose,
  onPublished,
}: {
  agent: Agent
  open: boolean
  onClose: () => void
  onPublished?: (pub: Publication) => void
}) {
  const t = useT()
  const [check, setCheck] = useState<PublishPreflight | null>(null)
  const [draft, setDraft] = useState<SettingsDraft | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Publication | null>(null)
  const [copied, setCopied] = useState(false)
  const [scope, setScope] = useState<BotVisibility>('internal')
  const [credits, setCredits] = useState('')
  const capMissing = scope !== 'internal' && creditsCap(credits) === undefined

  useEffect(() => {
    if (!open) return
    let live = true
    publicationsApi
      .preflight(agent.id)
      .then((found) => {
        if (!live) return
        setCheck(found)
        setDraft({
          title: agent.name,
          description: agent.description,
          greeting: agent.guide.slice(0, 400),
          prompts: agent.starters.slice(0, 4).join('\n'),
          placeholder: '',
          showProcess: 'summary',
          showSources: true,
          allowCopy: false,
          retentionDays: 30,
          conversationView: 'off',
          limits: found.defaults,
        })
      })
      .catch((err) => live && setError(errorMessage(err, t('공개할 수 있는지 확인하지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [open, agent, t])

  const publish = async () => {
    if (!draft || !check) return
    setBusy(true)
    setError(null)
    try {
      const settings = toSettings(draft)
      const made = check.existingId
        ? await publicationsApi.newVersion(check.existingId, '')
        : await publicationsApi.publish(agent.id, {
            ...settings,
            visibility: scope,
            limits: { ...settings.limits, ...(scope !== 'internal' ? { creditsPerDay: creditsCap(credits) } : {}) },
          })
      setDone(made)
      onPublished?.(made)
    } catch (err) {
      const code = err instanceof ApiError ? err.detail : ''
      setError(SCOPE_ERROR[code] ? t(SCOPE_ERROR[code]) : errorMessage(err, t('공개하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const copy = async (slug: string) => {
    setCopied(await copyText(botUrl(slug)))
  }

  const footer = done ? (
    <>
      <Link to={`/customize/publications/${done.id}`} className="inline-flex h-9 items-center rounded-control px-3.5 text-base font-medium text-muted hover:bg-elevated hover:text-fg">
        {t('관리')}
      </Link>
      <Button variant="primary" onClick={onClose}>
        {t('닫기')}
      </Button>
    </>
  ) : (
    <>
      <Button onClick={onClose}>{t('취소')}</Button>
      <Button variant="primary" disabled={busy || !check?.ok || !draft || (!check?.existingId && capMissing)} onClick={() => void publish()}>
        {busy && <Loader2 size={14} className="animate-spin" />}
        {check?.existingId ? t('새 판으로 공개') : t('공개하기')}
      </Button>
    </>
  )

  return (
    <Modal open={open} onClose={onClose} title={t('챗봇으로 공개')} width="max-w-xl" footer={footer}>
      {error && (
        <p role="alert" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger">
          {error}
        </p>
      )}
      {done ? (
        <div className="space-y-3" data-testid="publish-done">
          <p className="flex items-center gap-2 text-base font-medium text-success">
            <CheckCircle2 size={16} aria-hidden />
            {t('챗봇을 공개했습니다.')}
          </p>
          <p className="text-base text-muted">{t('이 기관 구성원이 로그인해서 아래 주소로 물어볼 수 있습니다. 구성원의 「공유된 에이전트」에도 보입니다.')}</p>
          {done.reviewState === 'pending' && done.requestedVisibility && (
            <p className="flex items-start gap-2 text-base text-warn" data-testid="publish-requested">
              <Clock size={15} className="mt-0.5 shrink-0" aria-hidden />
              {t('{scope}는 관리자가 승인하면 열립니다. 결과는 관리 화면에서 볼 수 있습니다.').replace(
                '{scope}',
                t(VISIBILITY_LABEL[done.requestedVisibility]),
              )}
            </p>
          )}
          <div className="flex min-w-0 items-center gap-2 rounded-control border border-line bg-elevated px-3 py-2">
            <code className="min-w-0 flex-1 truncate text-sm" data-testid="bot-url">
              {botUrl(done.slug)}
            </code>
            <Button size="sm" variant="ghost" onClick={() => void copy(done.slug)} aria-label={t('주소 복사')}>
              <Copy size={14} />
              {copied ? t('복사됨') : t('복사')}
            </Button>
            <a href={`/b/${done.slug}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
              <ExternalLink size={13} aria-hidden />
              {t('열기')}
            </a>
          </div>
        </div>
      ) : !check || !draft ? (
        !error && (
          <p role="status" className="flex items-center gap-2 text-base text-faint">
            <Loader2 size={14} className="animate-spin" />
            {t('공개할 수 있는지 확인하는 중…')}
          </p>
        )
      ) : (
        <>
          {!check.existingId && (
            <section aria-label={t('공개 범위')} className="space-y-2">
              <p className="text-base font-medium">{t('공개 범위')}</p>
              <ScopePicker
                value={scope}
                onChange={setScope}
                credits={credits}
                onCredits={setCredits}
                creditsRemaining={check.creditsRemaining}
              />
            </section>
          )}
          <section aria-label={t('공개 전 확인')} className="space-y-2 rounded-card border border-line bg-elevated/50 px-3.5 py-3">
            {check.model ? (
              <p className="flex items-center gap-2 text-base" data-testid="publish-model">
                <CheckCircle2 size={15} className="shrink-0 text-success" aria-hidden />
                {t('내부 모델로만 답합니다: {model}').replace('{model}', check.model.label)}
              </p>
            ) : null}
            {check.problems.map((code) => (
              <p key={code} className="flex items-start gap-2 text-base text-danger" data-testid="publish-problem">
                <XCircle size={15} className="mt-0.5 shrink-0" aria-hidden />
                {t(PROBLEM_LABEL[code] ?? '지금은 공개할 수 없습니다.')}
              </p>
            ))}
            <div>
              <p className="text-sm text-muted">{t('챗봇에서는 쓰지 않는 것')}</p>
              <ul className="mt-1 flex flex-wrap gap-1.5" data-testid="publish-disabled">
                {check.disabled.map((code) => (
                  <li key={code} className="rounded-full border border-line bg-panel px-2.5 py-0.5 text-sm text-muted">
                    {t(DISABLED_LABEL[code] ?? code)}
                  </li>
                ))}
              </ul>
            </div>
            <p className="text-sm text-muted">
              {t('공개한 때의 지시·자료로 고정됩니다. 에이전트를 고친 뒤에는 「새 판으로 공개」해야 챗봇에 반영됩니다.')}
            </p>
            {check.knowledge.length === 0 ? (
              <p className="text-sm text-warn">{t('올린 자료가 없습니다. 자료가 없으면 챗봇이 찾아 답할 근거가 없습니다.')}</p>
            ) : (
              <ul className="space-y-1">
                {check.knowledge.map((file) => (
                  <li key={file.name} className="text-sm">
                    <span className="text-muted">{file.name}</span>
                    {file.findings.length > 0 && (
                      <span className="ml-2 inline-flex items-center gap-1 text-warn" data-testid="publish-finding">
                        <AlertTriangle size={13} aria-hidden />
                        {t('개인정보로 보이는 값: {list}. 공개하기 전에 확인하세요.').replace(
                          '{list}',
                          file.findings.map((f) => `${t(FINDING_LABEL[f.category] ?? f.category)} ${f.count}`).join(', '),
                        )}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
          {check.existingId ? (
            <p className="text-base text-muted">
              {t('이 에이전트는 이미 챗봇으로 공개되어 있습니다. 지금 에이전트를 새 판으로 공개하거나 관리 화면에서 설정을 바꾸세요.')}{' '}
              <Link to={`/customize/publications/${check.existingId}`} className="text-accent hover:underline">
                {t('관리')}
              </Link>
            </p>
          ) : (
            <PublicationSettingsForm draft={draft} defaults={check.defaults} onChange={setDraft} />
          )}
        </>
      )}
    </Modal>
  )
}
