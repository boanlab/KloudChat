import { Hash, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, Select } from '@/components/ui'
import { errorCode, errorMessage, integrationsApi, type SlackBotChannelRow, type SlackChannel } from '@/lib/api'
import { useT } from '@/lib/useT'

const BLOCKED: Record<string, string> = {
  bot_paused: '공개가 멈춰 있어 답하지 않습니다.',
  outside_not_approved: '다른 조직과 공유된 채널이라 관리자 승인 뒤에 답합니다.',
}
const REFUSED: Record<string, string> = {
  not_connected: '설정 › 연결에서 Slack을 먼저 연결하세요.',
  reconnect_required: '설정 › 연결에서 Slack을 다시 연결하세요.',
  app_not_installed: '이 워크스페이스에 앱이 설치되지 않았습니다. Slack 관리자에게 요청하세요.',
  feature_off: '관리자가 Slack 봇을 켜지 않았습니다.',
  channel_taken: '이 채널에는 다른 에이전트가 이미 답합니다.',
}

/** A published agent answering @mentions in Slack channels the publisher is in. */
export function SlackBotCard({ publicationId }: { publicationId: string }) {
  const t = useT()
  const [rows, setRows] = useState<SlackBotChannelRow[] | null>(null)
  const [channels, setChannels] = useState<SlackChannel[] | null>(null)
  const [picked, setPicked] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let live = true
    integrationsApi.slackBotChannels(publicationId).then((r) => live && setRows(r)).catch(() => live && setRows([]))
    integrationsApi
      .slackChannels()
      .then((r) => live && setChannels(r))
      .catch((err) => {
        if (!live) return
        setChannels([])
        setError(t(REFUSED[errorCode(err)] ?? '채널 목록을 불러오지 못했습니다.'))
      })
    return () => {
      live = false
    }
  }, [publicationId, t])

  const link = async () => {
    if (!picked) return
    setBusy(true)
    setError(null)
    try {
      const row = await integrationsApi.slackBotLink(publicationId, picked)
      setRows((r) => [...(r ?? []).filter((x) => x.id !== row.id), row])
      setPicked('')
    } catch (err) {
      setError(t(REFUSED[errorCode(err)] ?? errorMessage(err, '연결하지 못했습니다. 다시 시도하세요.')))
    } finally {
      setBusy(false)
    }
  }
  const unlink = async (row: SlackBotChannelRow) => {
    setBusy(true)
    try {
      await integrationsApi.slackBotUnlink(publicationId, row.id)
      setRows((r) => (r ?? []).filter((x) => x.id !== row.id))
    } catch (err) {
      setError(errorMessage(err, t('처리하지 못했습니다. 다시 시도하세요.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-2 p-4" data-testid="slack-bot">
      <div>
        <p className="text-base font-medium">{t('Slack')}</p>
        <p className="text-sm text-muted">{t('채널에서 앱을 부르면 스레드에 답합니다. 채널에 앱을 초대해 두세요.')}</p>
      </div>
      {(rows ?? []).map((row) => (
        <div key={row.id} className="flex flex-wrap items-center gap-2">
          <Hash size={14} className="shrink-0 text-faint" />
          <span className="min-w-0 flex-1 truncate">{row.name || row.channelId}</span>
          {row.shared && <Badge tone="warn">{t('외부 공유')}</Badge>}
          {row.blocked && <span className="basis-full text-sm text-warn">{t(BLOCKED[row.blocked] ?? row.blocked)}</span>}
          <Button size="icon" variant="ghost" aria-label={t('#{name} 연결 해제').replace('{name}', row.name)} disabled={busy} onClick={() => void unlink(row)}>
            <Trash2 size={14} />
          </Button>
        </div>
      ))}
      {channels && channels.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <Select aria-label={t('답할 채널')} value={picked} onChange={(e) => setPicked(e.target.value)} className="w-auto max-w-full">
            <option value="">{t('채널 고르기')}</option>
            {channels
              .filter((c) => !(rows ?? []).some((r) => r.channelId === c.id))
              .map((c) => (
                <option key={c.id} value={c.id}>
                  #{c.name}
                </option>
              ))}
          </Select>
          <Button size="sm" disabled={!picked || busy} onClick={() => void link()}>
            {t('채널 추가')}
          </Button>
        </div>
      )}
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </Card>
  )
}
