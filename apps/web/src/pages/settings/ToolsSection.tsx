import { CircleCheck, Plug, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, Field, Input } from '@/components/ui'
import { adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

/**
 * Feature integration — one backend address wires up all six features.
 *
 * The backend splits its features by path behind a single gateway port, so in
 * principle there is one field to fill in here; the per-feature fields are for
 * the case where one of them lives somewhere else. A blank field is derived
 * from the backend address, and a badge says which of the two a value came
 * from.
 */

type Feature = SystemSettings['tools']['features'][number]
type ProbeResult = { ok: boolean; detail: string }

const OVERRIDE_KEYS: Record<Feature['key'], string> = {
  search: 'toolsSearchUrl',
  fetch: 'toolsFetchUrl',
  exec: 'toolsExecUrl',
  research: 'toolsResearchUrl',
  stt: 'toolsSttUrl',
  index: 'toolsIndexUrl',
}

const SOURCE_LABEL: Record<Feature['source'], string> = {
  database: '직접 지정',
  backend: '서버 주소 사용',
  environment: '기본값',
}

export function ToolsSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const [backendUrl, setBackendUrl] = useState('')
  const [overrides, setOverrides] = useState<Partial<Record<Feature['key'], string>>>({})
  const [busy, setBusy] = useState(false)
  const [probes, setProbes] = useState<Partial<Record<Feature['key'], ProbeResult>>>({})

  // After saving, the form is reconciled with what the server returned. A
  // derived value has to leave its field empty, or "inherited" and "typed in
  // by hand" look the same on screen.
  useEffect(() => {
    if (!settings) return
    setBackendUrl(settings.tools.backendBaseUrl)
    setOverrides(
      Object.fromEntries(
        settings.tools.features
          .filter((f) => f.source === 'database')
          .map((f) => [f.key, f.url]),
      ),
    )
  }, [settings])

  if (!settings) return null

  const save = async (patch: Record<string, string>) => {
    setBusy(true)
    setProbes({})
    try {
      await adminApi.updateSettings(patch)
      await onSaved()
    } finally {
      setBusy(false)
    }
  }

  const testOne = async (key: Feature['key']) => {
    setProbes((p) => ({ ...p, [key]: undefined }))
    const result = await adminApi.testTool(key).catch(() => ({
      ok: false,
      detail: t('확인할 수 없습니다.'),
    }))
    setProbes((p) => ({
      ...p,
      [key]: { ok: result.ok, detail: result.detail ?? (result.ok ? t('연결됨') : t('실패')) },
    }))
  }

  const testAll = async () => {
    setBusy(true)
    try {
      await Promise.all(settings.tools.features.map((f) => testOne(f.key)))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <Card className="flex flex-wrap items-center gap-3 p-4">
        <Plug size={18} className="text-muted" />
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('기능 서버')}</p>
          <p className="text-base text-muted">
            {t('주소 하나만 입력하면 아래 기능이 모두 연결됩니다.')}
          </p>
        </div>
      </Card>

      <Field
        label={t('서버 주소')}
        hint={t('기능 서버를 설치할 때 안내받은 주소입니다. 기능마다 서버가 다르면 비워 두고 아래에서 따로 지정하세요.')}
      >
        <div className="flex items-center gap-2">
          <Input
            value={backendUrl}
            onChange={(e) => setBackendUrl(e.target.value)}
            placeholder="http://backend-host:8080"
            aria-label={t('도구 백엔드 주소')}
            spellCheck={false}
          />
          <Button
            disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
            onClick={() => save({ backendBaseUrl: backendUrl.trim() })}
          >
            {t('저장')}
          </Button>
        </div>
      </Field>

      <div className="flex items-center justify-between">
        <p className="text-base font-medium">{t('기능별 주소')}</p>
        <Button variant="ghost" disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined} onClick={testAll}>
          {t('전체 연결 테스트')}
        </Button>
      </div>

      <div className="space-y-3">
        {settings.tools.features.map((feature) => {
          const probe = probes[feature.key]
          const inherited = feature.source !== 'database'
          return (
            <div key={feature.key} className="rounded-control border border-border p-3">
              <div className="mb-2 flex items-center gap-2">
                <span className="text-base font-medium">{t(feature.label)}</span>
                <Badge>{t(SOURCE_LABEL[feature.source])}</Badge>
                {probe && (
                  <span
                    className={`ml-auto flex items-center gap-1 text-sm ${
                      probe.ok ? 'text-success' : 'text-danger'
                    }`}
                  >
                    {probe.ok ? <CircleCheck size={13} /> : <TriangleAlert size={13} />}
                    {probe.detail}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Input
                  value={overrides[feature.key] ?? ''}
                  onChange={(e) =>
                    setOverrides((o) => ({ ...o, [feature.key]: e.target.value }))
                  }
                  placeholder={inherited ? feature.url : t('주소를 입력하세요')}
                  aria-label={t('{name} 주소').replace('{name}', t(feature.label))}
                  spellCheck={false}
                />
                <Button
                  variant="ghost"
                  disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined}
                  onClick={() =>
                    save({ [OVERRIDE_KEYS[feature.key]]: (overrides[feature.key] ?? '').trim() })
                  }
                >
                  {t('저장')}
                </Button>
                <Button variant="ghost" disabled={busy}
            title={busy ? t('설정을 불러오거나 저장하는 중입니다') : undefined} onClick={() => testOne(feature.key)}>
                  {t('테스트')}
                </Button>
              </div>
            </div>
          )
        })}
      </div>

      <p className="text-sm text-muted">
        {t('기능별 칸을 비우면 위 서버 주소를 따릅니다. 연결되지 않은 기능은 대화에서 쓸 수 없고, 나머지 기능은 그대로 동작합니다.')}
      </p>
    </div>
  )
}
