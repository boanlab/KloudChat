import { Bell, BellOff, Send, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { Badge, Button, Switch } from '@/components/ui'
import { errorMessage, pushApi, type PushDevice } from '@/lib/api'
import {
  currentDeviceId,
  currentSubscription,
  notificationPermission,
  PUSH_EVENTS,
  pushSupport,
  subscribeThisDevice,
  unsubscribeThisDevice,
  wantsEvent,
} from '@/lib/push'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Preferences, PushEvent } from '@/types'
import { useT } from '@/lib/useT'

const EVENT_TEXT: Record<PushEvent, [string, string]> = {
  job_done: ['작업 완료', '보고서·슬라이드·문서·조사처럼 오래 걸린 작업이 끝났을 때'],
  job_failed: ['작업 실패', '오래 걸린 작업을 끝내지 못했을 때'],
  shared: ['나에게 보낸 대화', '방문자가 공개 에이전트 대화를 보냈을 때'],
  review: ['공개 승인 요청', '에이전트 공개 승인을 요청받았을 때'],
  decided: ['공개 요청 결과', '내 에이전트 공개 요청이 승인·반려됐을 때'],
  issue: ['문의 답변', '문의·신고에 답이 달렸을 때'],
  credits: ['크레딧 한도 임박', '이번 달 크레딧을 90% 넘게 썼을 때'],
}

/** 설정 › 알림: this device's push, which events notify, the account's devices. */
export function NotificationsTab() {
  const t = useT()
  const { user, updateProfile } = useStore()
  const prefs = user?.preferences
  const support = pushSupport()
  const [permission, setPermission] = useState(notificationPermission())
  const [subscribed, setSubscribed] = useState<boolean | null>(null)
  const [devices, setDevices] = useState<PushDevice[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ text: string; alert?: boolean } | null>(null)

  const refresh = useCallback(async () => {
    const [sub, listed] = await Promise.all([
      support === 'ok' ? currentSubscription() : Promise.resolve(null),
      pushApi.devices().catch(() => []),
    ])
    setPermission(notificationPermission())
    setSubscribed(Boolean(sub))
    setDevices(listed)
  }, [support])
  useEffect(() => {
    void refresh()
  }, [refresh])

  const save = (patch: Partial<Preferences>) => {
    setNote(null)
    updateProfile({ preferences: patch }).catch((err: unknown) =>
      setNote({ text: errorMessage(err, t('설정을 저장하지 못했습니다. 다시 시도해 주세요.')), alert: true }),
    )
  }
  const run = async (what: () => Promise<void>) => {
    setBusy(true)
    setNote(null)
    try {
      await what()
    } catch (err) {
      setNote({ text: errorMessage(err, t('알림을 설정하지 못했습니다. 다시 시도해 주세요.')), alert: true })
    } finally {
      setBusy(false)
      await refresh()
    }
  }

  const here = currentDeviceId()
  const deviceLine =
    support === 'insecure'
      ? t('보안 연결(https)에서만 알림을 받을 수 있습니다.')
      : support === 'ios-install'
        ? t('iPhone·iPad는 홈 화면에 추가한 앱에서만 알림을 받습니다(iOS 16.4 이상).')
        : support === 'unsupported'
          ? t('이 브라우저는 알림을 지원하지 않습니다.')
          : permission === 'denied'
            ? t('브라우저 설정에서 이 사이트의 알림을 허용해 주세요.')
            : subscribed
              ? t('이 기기에서 알림을 받습니다.')
              : t('이 기기에서는 알림이 꺼져 있습니다.')
  const events = PUSH_EVENTS.filter((event) => event !== 'review' || user?.role === 'admin')

  return (
    <div className="space-y-8">
      {note && (
        <p role={note.alert ? 'alert' : 'status'} className={note.alert ? 'text-base text-danger' : 'text-base text-faint'}>
          {note.text}
        </p>
      )}
      <section className="space-y-3" aria-labelledby="notify-device">
        <h2 id="notify-device" className="text-base font-semibold">
          {t('이 기기')}
        </h2>
        <p className="text-base text-muted" data-testid="notify-device-state">
          {deviceLine}
        </p>
        {support === 'ok' && permission !== 'denied' && (
          <div className="flex flex-wrap gap-2">
            {subscribed ? (
              <>
                <Button
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      const { sent } = await pushApi.test()
                      setNote({ text: sent ? t('테스트 알림을 보냈습니다.') : t('테스트 알림을 보내지 못했습니다.') })
                    })
                  }
                >
                  <Send size={15} />
                  {t('테스트 알림')}
                </Button>
                <Button variant="ghost" disabled={busy} onClick={() => void run(unsubscribeThisDevice)}>
                  <BellOff size={15} />
                  {t('이 기기 알림 끄기')}
                </Button>
              </>
            ) : (
              <Button
                variant="primary"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    if (!(await subscribeThisDevice()) && notificationPermission() !== 'granted') {
                      setNote({ text: t('브라우저에서 알림을 허용해야 받을 수 있습니다.') })
                    }
                  })
                }
              >
                <Bell size={15} />
                {t('이 기기에서 알림 켜기')}
              </Button>
            )}
          </div>
        )}
      </section>

      <section className="space-y-3 border-t border-line pt-6" aria-labelledby="notify-events">
        <h2 id="notify-events" className="text-base font-semibold">
          {t('알릴 일')}
        </h2>
        {events.map((event) => {
          const [label, desc] = EVENT_TEXT[event]
          return (
            <div key={event} className="flex items-center justify-between gap-4">
              <div>
                <p className="text-base font-medium">{t(label)}</p>
                <p className="text-sm text-muted">{t(desc)}</p>
              </div>
              <Switch
                checked={wantsEvent(prefs, event)}
                onChange={(on) => save({ pushEvents: { ...prefs?.pushEvents, [event]: on } })}
                label={t(label)}
              />
            </div>
          )
        })}
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-base font-medium">{t('내부 자료 대화의 제목 표시')}</p>
            <p className="text-sm text-muted">{t('끄면 내부 자료나 내부 전용 모델을 쓴 대화는 제목 없이 알립니다.')}</p>
          </div>
          <Switch
            checked={prefs?.pushInternalTitles ?? false}
            onChange={(on) => save({ pushInternalTitles: on })}
            label={t('내부 자료 대화의 제목 표시')}
          />
        </div>
        <p className="pt-1 text-xs text-faint">{t('알림에는 제목만 담고 대화 내용은 담지 않습니다.')}</p>
      </section>

      <section className="space-y-3 border-t border-line pt-6" aria-labelledby="notify-devices">
        <h2 id="notify-devices" className="text-base font-semibold">
          {t('알림 받는 기기')}
        </h2>
        {devices && devices.length === 0 && <p className="text-base text-muted">{t('알림 받는 기기가 없습니다.')}</p>}
        <ul className="divide-y divide-line">
          {devices?.map((device) => (
            <li key={device.id} className="flex items-center justify-between gap-3 py-2.5" data-testid="push-device">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-base font-medium">
                  {device.device || t('브라우저')}
                  {device.id === here && <Badge>{t('이 기기')}</Badge>}
                </p>
                <p className="text-sm text-muted">
                  {device.lastSentAt
                    ? t('마지막 알림 {when}').replace('{when}', relativeTime(device.lastSentAt))
                    : t('등록 {when}').replace('{when}', relativeTime(device.createdAt))}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                aria-label={t('{name} 삭제').replace('{name}', device.device || t('브라우저'))}
                onClick={() =>
                  void run(async () => {
                    await pushApi.remove(device.id)
                    if (device.id === here) await unsubscribeThisDevice()
                  })
                }
              >
                <Trash2 size={15} />
              </Button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
