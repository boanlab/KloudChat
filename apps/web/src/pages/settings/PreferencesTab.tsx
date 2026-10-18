import { useEffect, useState } from 'react'
import { ModelPicker } from '@/components/chat/ModelPicker'
import { Select, Switch } from '@/components/ui'
import { authConfig } from '@/lib/api'
import { kindMeta, kindOrder } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import type { Preferences } from '@/types'
import { useT } from '@/lib/useT'

/** Per-surface defaults and switches, stored on the account. */
export function PreferencesTab() {
  const t = useT()
  const { user, updateProfile } = useStore()
  const prefs = user?.preferences
  const dictationEnabled = useStore((s) => s.dictationEnabled)
  const models = useStore((s) => s.models)
  const modelsSettled = useStore((s) => s.modelsSettled)
  const enabledKinds = useStore((s) => s.enabledKinds)
  // No model for a surface: not offered.
  const offered = (kind: (typeof kindOrder)[number], modality?: 'audio' | 'video') =>
    !modelsSettled || (enabledKinds.includes(kind) &&
      models.some((m) => m.kinds.includes(kind) && (!modality || m.modality === modality)))
  const [allowRawExternal, setAllowRawExternal] = useState<boolean | null>(null)
  const set = (patch: Partial<Preferences>) => void updateProfile({ preferences: patch })
  // Pictures kept inside: picked here, offered only when there is a choice.
  const strictImages = models.filter((m) => m.kinds.includes('image') && m.strictLocal)
  const strictImageDefault = useStore((s) => s.strictImageDefault)
  // The model the server draws with: the pick if served, else its default, else the first.
  const served = (id?: string) => (id && strictImages.some((m) => m.id === id) ? id : undefined)
  const strictImage = served(prefs?.strictImageModel) ?? served(strictImageDefault) ?? strictImages[0]?.id

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
          {kindOrder.flatMap((kind) => {
            const meta = kindMeta[kind]
            const Icon = meta.icon
            // av: separate audio and video defaults.
            const fields =
              kind === 'av'
                ? ([
                    { modality: 'audio', label: '오디오/동영상 · 오디오 모델' },
                    { modality: 'video', label: '오디오/동영상 · 동영상 모델' },
                  ] as const)
                : ([{ modality: undefined, label: meta.label }] as const)
            return fields.filter((field) => offered(kind, field.modality)).map((field) => (
              <div key={`${kind}:${field.modality ?? ''}`} className="space-y-1.5">
                <span className="flex items-center gap-1.5 text-base font-medium">
                  <Icon size={13} style={{ color: meta.color }} />
                  {t(field.label)}
                </span>
                <ModelPicker
                  kind={kind}
                  modality={field.modality}
                  variant="field"
                  label={t(field.label)}
                />
              </div>
            ))
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
          <Select
            value={
              prefs?.privacyDefaultAction === 'send_raw_external' && allowRawExternal === false
                ? 'ask'
                : (prefs?.privacyDefaultAction ?? 'ask')
            }
            onChange={(event) =>
              set({ privacyDefaultAction: event.target.value as Preferences['privacyDefaultAction'] })
            }
          >
            <option value="ask">{t('매번 확인')}</option>
            <option value="route_strict_local">{t('strict-local 모델로 전환')}</option>
            <option value="mask_external">{t('개인정보를 가린 뒤 기존 모델 사용')}</option>
            {allowRawExternal === true && (
              <option value="send_raw_external">{t('원문을 외부 모델로 전송')}</option>
            )}
          </Select>
          <span className="block text-sm text-faint">
            {t('모델 전환은 외부 대체 모델이 없는 strict-local 모델이 실제로 사용 가능할 때만 적용됩니다.')}
          </span>
        </label>
        {strictImages.length > 1 && (
          <label className="block max-w-xl space-y-1.5">
            <span className="text-base font-medium">{t('strict-local 그림 모델')}</span>
            <Select
              value={strictImage}
              onChange={(event) => set({ strictImageModel: event.target.value })}
            >
              {strictImages.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label}
                </option>
              ))}
            </Select>
            <span className="block text-sm text-faint">
              {t('내부 자료가 있는 대화나 문서의 그림을 strict-local로 그려야 할 때, 고른 그림 모델이 외부 모델이면 이 모델로 그립니다.')}
            </span>
          </label>
        )}
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
          ...(dictationEnabled ? [{
            label: '스페이스로 말하기',
            desc: '빈 입력창에서 스페이스를 누른 채 말하면, 떼는 순간 받아 적어 입력창에 넣습니다. 보내지는 않습니다.',
            value: prefs?.spaceToTalk ?? false,
            set: (v: boolean) => set({ spaceToTalk: v }),
          }] : []),
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
