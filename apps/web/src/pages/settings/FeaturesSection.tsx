import { useEffect, useState } from 'react'
import { Button } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'
import type { SessionKind } from '@/types'

/**
 * Which surfaces stay open.
 *
 * Chat cannot be turned off — without conversation there is nothing this
 * instance can do. A disabled surface disappears from the lists *and* the
 * server refuses work of that kind.
 */

const OPTIONAL: SessionKind[] = ['report', 'slides', 'image', 'av']

const NOTE: Partial<Record<SessionKind, string>> = {
  image: '생성할 때마다 크레딧이 나갑니다.',
  av: '생성할 때마다 크레딧이 나갑니다. 동영상은 특히 비쌉니다.',
}

export function FeaturesSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const [enabled, setEnabled] = useState<SessionKind[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (settings) setEnabled(settings.enabledKinds as SessionKind[])
  }, [settings])

  if (!settings) return null

  const dirty =
    JSON.stringify([...enabled].sort()) !==
    JSON.stringify([...(settings.enabledKinds as SessionKind[])].sort())

  const toggle = (kind: SessionKind) =>
    setEnabled((prev) =>
      prev.includes(kind) ? prev.filter((k) => k !== kind) : [...prev, kind],
    )

  const save = async () => {
    setBusy(true)
    try {
      await adminApi.updateSettings({
        enabledKinds: OPTIONAL.filter((k) => enabled.includes(k)).join(','),
      })
      await onSaved()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 rounded-control border border-line bg-panel px-3 py-2.5">
        <span className="text-base font-medium">{t(kindMeta.chat.label)}</span>
        <span className="text-sm text-muted">{t('항상 켜져 있습니다.')}</span>
      </div>

      {OPTIONAL.map((kind) => {
        const on = enabled.includes(kind)
        return (
          <label
            key={kind}
            className="flex cursor-pointer items-center gap-3 rounded-control border border-line px-3 py-2.5"
          >
            <input
              type="checkbox"
              checked={on}
              onChange={() => toggle(kind)}
              className="size-4 accent-[var(--accent)]"
            />
            <span className="min-w-0 flex-1">
              <span className="text-base font-medium">{t(kindMeta[kind].label)}</span>
              {NOTE[kind] && (
                <span className="ml-2 text-sm text-muted">{t(NOTE[kind])}</span>
              )}
            </span>
          </label>
        )
      })}

      <div className="flex items-center gap-2">
        <Button
          disabled={busy || !dirty}
          title={!dirty ? t('바뀐 내용이 없습니다') : busy ? t('저장 중…') : undefined}
          onClick={() => void save()}
        >
          {t('저장')}
        </Button>
        <span className="text-sm text-muted">
          {t('끈 화면은 목록에서 사라지고 새 작업도 만들 수 없습니다. 기존 기록은 남습니다.')}
        </span>
      </div>
    </div>
  )
}
