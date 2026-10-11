import { Cpu } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Card, Field, Select, Switch } from '@/components/ui'
import { ApiError, adminApi, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

type Roles = NonNullable<SystemSettings['modelRoles']>
type Choice = Roles['choices']['chat'][number]
type ModelKey = 'chat' | 'report' | 'slides' | 'image' | 'audio' | 'video' | 'title' | 'code' | 'codeFallback'

/** Each picker: its label, the list it picks from, and what an empty choice means (none: required). */
const SURFACES: { key: ModelKey; label: string; from: keyof Roles['choices']; empty?: string }[] = [
  { key: 'chat', label: '대화 기본 모델', from: 'chat' },
  { key: 'report', label: '보고서 기본 모델', from: 'report', empty: '대화 기본 모델을 따름' },
  { key: 'slides', label: '발표 자료 기본 모델', from: 'slides', empty: '대화 기본 모델을 따름' },
  { key: 'image', label: '그림 기본 모델', from: 'image', empty: '가장 싼 그림 모델' },
  { key: 'audio', label: '오디오 기본 모델', from: 'audio', empty: '가장 싼 오디오 모델' },
  { key: 'video', label: '동영상 기본 모델', from: 'video', empty: '가장 싼 동영상 모델' },
]

const HELPERS: { key: ModelKey; label: string; hint: string; empty: string }[] = [
  {
    key: 'title',
    label: '제목·기억·요약 모델',
    hint: '대화 제목을 짓고, 기억할 내용을 뽑고, 긴 대화를 요약합니다.',
    empty: '대화의 모델을 그대로 사용',
  },
  {
    key: 'code',
    label: '코드 작성 모델',
    hint: '파일을 읽고 계산하는 코드(run_code)를 씁니다.',
    empty: '고르지 않음',
  },
  {
    key: 'codeFallback',
    label: '코드 작성 대체 모델',
    hint: '앞의 모델이 실패하면 한 번 더 씁니다.',
    empty: '대체 모델 없음',
  },
]

const FIELD: Record<ModelKey, string> = {
  chat: 'modelChat',
  report: 'modelReport',
  slides: 'modelSlides',
  image: 'modelImage',
  audio: 'modelAudio',
  video: 'modelVideo',
  title: 'modelTitle',
  code: 'modelCode',
  codeFallback: 'modelCodeFallback',
}

/** Server refusals (`model_title_invalid`, …) as sentences. */
function refusal(detail: string, t: (s: string) => string): string {
  if (detail === 'model_chat_required') return t('대화 기본 모델은 비워 둘 수 없습니다.')
  if (/^model_(title|code|code_fallback)_invalid$/.test(detail))
    return t('보조 작업에는 이 인스턴스가 직접 운영하는 모델만 고를 수 있습니다.')
  if (/^model_\w+_invalid$/.test(detail)) return t('이 작업에 쓸 수 없는 모델입니다. 목록에서 다시 고르세요.')
  return t('저장하지 못했습니다.')
}

function ModelSelect({
  label,
  hint,
  value,
  choices,
  empty,
  onChange,
}: {
  label: string
  hint?: string
  value: string
  choices: Choice[]
  empty?: string
  onChange: (value: string) => void
}) {
  const t = useT()
  return (
    <Field label={label} hint={hint}>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {empty !== undefined && <option value="">{empty}</option>}
        {/* A stored model missing from the catalogue stays selectable so the choice is not misread. */}
        {value && !choices.some((m) => m.id === value) && (
          <option value={value}>
            {value} · {t('현재 사용 불가')}
          </option>
        )}
        {choices.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </Select>
    </Field>
  )
}

/** Which model does which job, and whether models think: the administrator's choice. */
export function ModelRolesSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const [models, setModels] = useState<Record<ModelKey, string> | null>(null)
  const [selfHostedThinking, setSelfHostedThinking] = useState(false)
  const [externalThinking, setExternalThinking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Choosing wins over a fetch that lands late.
  const dirty = useRef(false)
  const roles = settings?.modelRoles

  useEffect(() => {
    if (!roles || dirty.current) return
    setModels({
      chat: roles.chat,
      report: roles.report,
      slides: roles.slides,
      image: roles.image,
      audio: roles.audio,
      video: roles.video,
      title: roles.title,
      code: roles.code,
      codeFallback: roles.codeFallback,
    })
    setSelfHostedThinking(roles.selfHostedThinking)
    setExternalThinking(roles.externalThinking)
  }, [roles])

  if (!roles || !models) return null

  const touch = () => {
    dirty.current = true
    setSaved(false)
    setError(null)
  }
  const pick = (key: ModelKey) => (value: string) => {
    touch()
    setModels({ ...models, [key]: value })
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const patch: Record<string, string> = {
        thinkingSelfHosted: selfHostedThinking ? '1' : '0',
        thinkingExternal: externalThinking ? '1' : '0',
      }
      for (const key of Object.keys(FIELD) as ModelKey[]) patch[FIELD[key]] = models[key]
      await adminApi.updateSettings(patch)
      dirty.current = false
      await onSaved()
      setSaved(true)
    } catch (err) {
      setError(err instanceof ApiError ? refusal(err.detail, t) : t('저장하지 못했습니다.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-4 p-4" data-section="model-roles">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
          <Cpu size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('작업별 모델')}</p>
          <p className="text-sm text-muted">
            {t('사용자가 모델을 고르지 않았을 때 각 화면이 쓰는 모델과, 제목 짓기·코드 작성 같은 보조 작업을 맡을 모델을 정합니다. 사용자가 고른 모델이 있으면 그 모델이 우선합니다.')}
          </p>
        </div>
      </div>

      <div className="grid gap-4 border-t border-line pt-4 lg:grid-cols-2">
        {SURFACES.map((row) => (
          <ModelSelect
            key={row.key}
            label={t(row.label)}
            value={models[row.key]}
            choices={roles.choices[row.from]}
            empty={row.empty === undefined ? undefined : t(row.empty)}
            onChange={pick(row.key)}
          />
        ))}
      </div>

      <div className="space-y-3 border-t border-line pt-4">
        <div>
          <p className="text-base font-medium">{t('보조 작업 모델')}</p>
          <p className="text-sm text-muted">
            {t('대화 내용을 읽는 작업이라 이 인스턴스가 직접 운영하는 모델만 고를 수 있습니다. strict-local 대화에서는 고른 모델의 strict-local 판이 맡고, 그런 판이 없으면 대화의 모델이 맡습니다.')}
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-3">
          {HELPERS.map((row) => (
            <ModelSelect
              key={row.key}
              label={t(row.label)}
              hint={t(row.hint)}
              value={models[row.key]}
              choices={roles.choices.helper}
              empty={t(row.empty)}
              onChange={pick(row.key)}
            />
          ))}
        </div>
      </div>

      <div className="space-y-3 border-t border-line pt-4">
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium">{t('자체 호스팅 모델이 답하기 전에 생각')}</p>
            <p className="text-sm text-muted">
              {t('켜면 생각하는 모델이 답하기 전에 추론합니다. 판정은 정확해지지만 호출마다 수십 초에서 수 분이 더 걸립니다.')}
            </p>
          </div>
          <Switch
            checked={selfHostedThinking}
            onChange={(v) => {
              touch()
              setSelfHostedThinking(v)
            }}
            label={t('자체 호스팅 모델이 답하기 전에 생각')}
          />
        </div>
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium">{t('외부 모델이 답하기 전에 생각')}</p>
            <p className="text-sm text-muted">
              {t('끄면 외부 모델에 추론을 끄라고 요청합니다. 요청을 거절하는 모델은 그대로 생각합니다.')}
            </p>
          </div>
          <Switch
            checked={externalThinking}
            onChange={(v) => {
              touch()
              setExternalThinking(v)
            }}
            label={t('외부 모델이 답하기 전에 생각')}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
        <p className="min-w-0 flex-1 text-sm text-muted">
          {t('바로 적용됩니다. 진행 중인 대화는 다음 메시지부터 바뀝니다.')}
        </p>
        {error && (
          <p className="text-sm text-danger" role="alert">
            {error}
          </p>
        )}
        {saved && !error && <p className="text-sm text-success">{t('저장했습니다')}</p>}
        <Button disabled={busy} onClick={() => void save()}>
          {busy ? t('저장 중…') : t('저장')}
        </Button>
      </div>
    </Card>
  )
}
