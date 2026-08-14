import { Switch } from '@/components/ui'
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
  const set = (patch: Partial<Preferences>) => void updateProfile({ preferences: patch })

  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <div>
          <h2 className="text-[13px] font-semibold">{t('기본 모델')}</h2>
          <p className="text-xs text-muted">{t('화면별로 처음 선택되는 모델입니다.')}</p>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {kindOrder.map((kind) => {
            const meta = kindMeta[kind]
            const Icon = meta.icon
            const usable = models.filter((m) => m.kinds.includes(kind))
            return (
              <label key={kind} className="block space-y-1.5">
                <span className="flex items-center gap-1.5 text-[13px] font-medium">
                  <Icon size={13} style={{ color: meta.color }} />
                  {t(meta.label)}
                </span>
                <select
                  value={modelByKind[kind]}
                  onChange={(e) => setModel(kind, e.target.value)}
                  className="h-9 w-full rounded-lg border border-line bg-panel px-3 text-sm focus:border-accent focus:outline-none"
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
        <h2 className="text-[13px] font-semibold">{t('동작')}</h2>
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
              <p className="text-[13px] font-medium">{t(row.label)}</p>
              <p className="text-xs text-muted">{t(row.desc)}</p>
            </div>
            <Switch checked={row.value} onChange={row.set} label={t(row.label)} />
          </div>
        ))}
        <p className="pt-1 text-[11px] text-faint">
          {t('계정에 저장되므로 다른 기기에서도 같게 적용됩니다.')}
        </p>
      </section>
    </div>
  )
}
