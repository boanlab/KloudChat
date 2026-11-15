import { CircleCheck, CloudUpload, ExternalLink, Send, TriangleAlert } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import { Button, Select, Textarea } from '@/components/ui'
import { errorMessage, integrationsApi, type IntegrationAction, type SlackChannel } from '@/lib/api'
import { useT } from '@/lib/useT'

/** What a failed write says, by the server's code. */
const FAILED: Record<string, string> = {
  reconnect_required: '연결이 끊겼습니다. 설정 › 연결에서 다시 연결하세요.',
  not_connected: '연결되어 있지 않습니다. 설정 › 연결에서 연결하세요.',
  scope_missing: '연결할 때 이 권한을 허용하지 않았습니다. 다시 연결하세요.',
  too_large: '파일이 너무 큽니다.',
  forbidden: '권한이 없거나 저장 공간이 부족합니다.',
  rate_limited: '요청이 많아 거절되었습니다. 잠시 뒤 다시 시도하세요.',
}

/**
 * A write the agent proposed (`services/integrations/actions`): nothing reaches Google or
 * Slack until the person presses the button here. A reload shows it again from the stored step.
 */
export function ActionCard({ actionId }: { actionId: string }) {
  const t = useT()
  const [action, setAction] = useState<IntegrationAction | null>(null)
  const [edits, setEdits] = useState<Record<string, unknown>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [channels, setChannels] = useState<SlackChannel[] | null>(null)

  useEffect(() => {
    let live = true
    integrationsApi
      .action(actionId)
      .then((a) => live && setAction(a))
      .catch((err) => live && setError(errorMessage(err, t('확인 카드를 불러오지 못했습니다.'))))
    return () => {
      live = false
    }
  }, [actionId, t])
  const slackPending = action?.kind === 'slack.post_message' && action.status === 'pending'
  useEffect(() => {
    if (!slackPending) return
    let live = true
    integrationsApi
      .slackChannels()
      .then((rows) => live && setChannels(rows))
      .catch(() => live && setChannels([]))
    return () => {
      live = false
    }
  }, [slackPending])

  const decide = async (yes: boolean) => {
    setBusy(true)
    setError(null)
    try {
      setAction(yes ? await integrationsApi.confirm(actionId, Object.keys(edits).length ? edits : undefined) : await integrationsApi.cancel(actionId))
    } catch (err) {
      setError(errorMessage(err, t('처리하지 못했습니다. 다시 시도하세요.')))
    } finally {
      setBusy(false)
    }
  }

  if (!action) {
    return error ? <p role="alert" className="mt-2 text-sm text-danger">{error}</p> : null
  }
  const p = action.payload
  let title = ''
  let icon: ReactNode = <CloudUpload size={16} className="mt-0.5 shrink-0 text-accent" />
  let body: ReactNode = null
  let yes = t('확인')
  if (action.kind === 'drive.export') {
    title = t('내 Google 드라이브에 저장할까요?')
    yes = t('드라이브에 저장')
    const formats = (p.formats as string[] | undefined) ?? [String(p.format)]
    const format = String(edits.format ?? p.format)
    body = (
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span className="min-w-0 truncate font-medium">{String(p.title)}</span>
        {action.status === 'pending' && formats.length > 1 ? (
          <Select
            aria-label={t('파일 형식')}
            value={format}
            onChange={(e) => setEdits({ format: e.target.value })}
            className="w-auto"
          >
            {formats.map((f) => (
              <option key={f} value={f}>
                .{f}
              </option>
            ))}
          </Select>
        ) : (
          <span className="text-muted">.{format}</span>
        )}
      </div>
    )
  } else {
    icon = <Send size={16} className="mt-0.5 shrink-0 text-accent" />
    title = t('Slack에 이 메시지를 보낼까요?')
    yes = t('Slack에 보내기')
    const channel = String(edits.channel ?? p.channel ?? '')
    const text = String(edits.text ?? p.text ?? '')
    const pick = (id: string) => {
      const found = channels?.find((c) => c.id === id)
      setEdits({ ...edits, channel: id, channelName: found?.name ?? '' })
    }
    body =
      action.status === 'pending' ? (
        <div className="mt-2 space-y-1.5">
          <Select aria-label={t('보낼 채널')} value={channel} onChange={(e) => pick(e.target.value)} className="w-auto max-w-full">
            {!channel && <option value="">{t('채널 고르기')}</option>}
            {channel && !channels?.some((c) => c.id === channel) && <option value={channel}>#{String(p.channelName || channel)}</option>}
            {(channels ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                #{c.name}
              </option>
            ))}
          </Select>
          <Textarea
            aria-label={t('보낼 내용')}
            value={text}
            rows={Math.min(8, Math.max(2, text.split('\n').length))}
            onChange={(e) => setEdits({ ...edits, text: e.target.value })}
          />
        </div>
      ) : (
        <p className="mt-1.5 text-sm text-muted">
          #{String(p.channelName || p.channel)} · {String(p.text).slice(0, 140)}
        </p>
      )
  }

  return (
    <section
      aria-label={title}
      data-testid="action-card"
      className="mt-2 rounded-card border border-accent/30 bg-accent/5 p-3 text-base"
    >
      <p className="flex items-start gap-2 font-medium">
        {icon}
        <span className="min-w-0">{title}</span>
      </p>
      {body}
      {action.status === 'pending' && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <Button
            size="sm"
            variant="primary"
            disabled={busy || (action.kind === 'slack.post_message' && !String(edits.channel ?? p.channel ?? ''))}
            onClick={() => void decide(true)}
          >
            {yes}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void decide(false)}>
            {t('취소')}
          </Button>
        </div>
      )}
      {action.status === 'done' && (
        <p role="status" className="mt-2 flex flex-wrap items-center gap-1.5 text-sm text-success">
          <CircleCheck size={13} />
          {action.kind === 'drive.export' ? t('드라이브에 저장했습니다.') : t('보냈습니다.')}
          {action.result?.url && (
            <a href={action.result.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-0.5 underline">
              {t('열기')}
              <ExternalLink size={12} />
            </a>
          )}
        </p>
      )}
      {action.status === 'cancelled' && <p className="mt-2 text-sm text-muted">{t('취소했습니다. 아무것도 보내지 않았습니다.')}</p>}
      {action.status === 'expired' && <p className="mt-2 text-sm text-muted">{t('시간이 지나 확인할 수 없습니다. 다시 요청하세요.')}</p>}
      {action.status === 'failed' && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 text-sm text-danger">
          <TriangleAlert size={13} className="mt-0.5 shrink-0" />
          {t(FAILED[action.error] ?? '보내지 못했습니다.')}
        </p>
      )}
      {error && <p role="alert" className="mt-2 text-sm text-danger">{error}</p>}
    </section>
  )
}
