import { useEffect, useState } from 'react'
import { Switch } from '@/components/ui'
import { authConfig } from '@/lib/api'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { Preferences } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Per-surface default model, and the behaviour switches that go with it.
 *
 * The switches live on the account, not in component state, so they follow the
 * person rather than the browser — and something acts on each one.
 */
export function PreferencesTab() {
  const t = useT()
  const { models, modelByKind, setModel, user, updateProfile } = useStore()
  const prefs = user?.preferences
  const [allowRawExternal, setAllowRawExternal] = useState<boolean | null>(null)
  const set = (patch: Partial<Preferences>) => void updateProfile({ preferences: patch })

  useEffect(() => {
    void authConfig
      .get()
      .then((config) => setAllowRawExternal(config.privacy.allowUserRawExternal))
      .catch(() => setAllowRawExternal(null))
  }, [])

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div>
          <h2 className="text-base font-semibold">{t('기본 모델')}</h2>
          <p className="text-sm text-muted">{t('화면별로 처음 선택되는 모델입니다.')}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {kindOrder.map((kind) => {
            const meta = kindMeta[kind]
            const Icon = meta.icon
            const usable = models.filter((m) => m.kinds.includes(kind))
            return (
              <label key={kind} className="block space-y-1.5">
                <span className="flex items-center gap-1.5 text-base font-medium">
                  <Icon size={13} style={{ color: meta.color }} />
                  {t(meta.label)}
                </span>
                <select
                  value={modelByKind[kind]}
                  onChange={(e) => setModel(kind, e.target.value)}
                  className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
                >
                  {usable.length === 0 && <option value="">{t('사용 가능한 모델 없음')}</option>}
                  {usable.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </label>
            )
          })}
        </div>
      </section>

      <section className="space-y-3 border-t border-line pt-6">
        <div>
          <h2 className="text-base font-semibold">{t('개인정보가 감지된 요청')}</h2>
          <p className="text-sm text-muted">
            {t('외부 모델로 전송하기 전에 서버가 전체 대화 맥락을 검사하고 이 동작을 적용합니다.')}
          </p>
        </div>
        <label className="block max-w-xl space-y-1.5">
          <span className="text-base font-medium">{t('기본 처리 방법')}</span>
          <select
            value={
              prefs?.privacyDefaultAction === 'send_raw_external' && allowRawExternal === false
                ? 'ask'
                : (prefs?.privacyDefaultAction ?? 'ask')
            }
            onChange={(event) =>
              set({ privacyDefaultAction: event.target.value as Preferences['privacyDefaultAction'] })
            }
            className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
          >
            <option value="ask">{t('매번 확인')}</option>
            <option value="route_strict_local">{t('strict-local 모델로 전환')}</option>
            <option value="mask_external">{t('개인정보를 가린 뒤 기존 모델 사용')}</option>
            {allowRawExternal === true && (
              <option value="send_raw_external">{t('원문을 외부 모델로 전송')}</option>
            )}
          </select>
          <span className="block text-sm text-faint">
            {t('모델 전환은 외부 fallback이 없는 strict-local 모델이 실제로 사용 가능할 때만 적용됩니다.')}
          </span>
        </label>
      </section>

      <section className="space-y-3 border-t border-line pt-6">
        <h2 className="text-base font-semibold">{t('동작')}</h2>
        {[
          {
            label: '응답 스트리밍',
            desc: '토큰이 도착하는 대로 표시합니다. 끄면 답변이 완성된 뒤 한 번에 나타납니다.',
            value: prefs?.streamResponses ?? true,
            set: (v: boolean) => set({ streamResponses: v }),
          },
          {
            label: '메모리 자동 저장',
            desc: '대화가 끝나면 사용자에 대해 계속 참인 사실만 골라 메모리에 기록합니다. 이미 아는 사실은 다시 쓰지 않습니다.',
            value: prefs?.autoMemory ?? false,
            set: (v: boolean) => set({ autoMemory: v }),
          },
          {
            label: '토큰·크레딧 표시',
            desc: '각 응답 아래에 모델·토큰·크레딧을 표시합니다.',
            value: prefs?.showUsage ?? true,
            set: (v: boolean) => set({ showUsage: v }),
          },
        ].map((row) => (
          <div key={row.label} className="flex items-center justify-between gap-4">
            <div>
              <p className="text-base font-medium">{t(row.label)}</p>
              <p className="text-sm text-muted">{t(row.desc)}</p>
            </div>
            <Switch checked={row.value} onChange={row.set} label={t(row.label)} />
          </div>
        ))}
        <p className="pt-1 text-xs text-faint">
          {t('계정에 저장되므로 다른 기기에서도 같게 적용됩니다.')}
        </p>
      </section>
    </div>
  )
}
