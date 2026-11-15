import { useEffect, useState } from 'react'
import { Select, Switch } from '@/components/ui'
import { errorMessage, integrationsApi, type SlackChannel } from '@/lib/api'
import { useT } from '@/lib/useT'

/** 설정 › 연결 › Slack: a long job's notice in Slack, to my DMs or a channel I am in. */
export function SlackNotifyChoice({ prefs, onSaved }: { prefs: Record<string, unknown>; onSaved: () => void }) {
  const t = useT()
  const notify = (prefs.notify ?? {}) as { on?: boolean; channel?: string }
  const [on, setOn] = useState(Boolean(notify.on))
  const [channel, setChannel] = useState(notify.channel ?? '')
  const [channels, setChannels] = useState<SlackChannel[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!on) return
    let live = true
    integrationsApi.slackChannels().then((rows) => live && setChannels(rows)).catch(() => {})
    return () => {
      live = false
    }
  }, [on])
  const save = async (nextOn: boolean, nextChannel: string) => {
    setError(null)
    try {
      await integrationsApi.slackNotify(nextOn, nextChannel)
      setOn(nextOn)
      setChannel(nextChannel)
      onSaved()
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    }
  }
  return (
    <div className="mt-3 border-t border-line pt-2.5" data-testid="slack-notify">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 text-base">{t('긴 작업이 끝나면 Slack으로 알림')}</span>
        <Switch checked={on} label={t('긴 작업이 끝나면 Slack으로 알림')} onChange={(v) => void save(v, channel)} />
      </div>
      {on && (
        <Select
          aria-label={t('알림 받을 곳')}
          value={channel}
          onChange={(e) => void save(true, e.target.value)}
          className="mt-1.5 w-auto"
        >
          <option value="">{t('나에게 보내는 메시지')}</option>
          {channels.map((c) => (
            <option key={c.id} value={c.id}>
              #{c.name}
            </option>
          ))}
        </Select>
      )}
      {error && <p role="alert" className="mt-1 text-sm text-danger">{error}</p>}
    </div>
  )
}
