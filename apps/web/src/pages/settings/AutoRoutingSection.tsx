import { ArrowDown, ArrowUp, Gauge, Sparkles, TriangleAlert, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Field, Switch } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

export function AutoRoutingSection() {
  const t = useT()
  const {
    governance,
    loadGovernance,
    saveGovernance,
    models,
    loadModels,
    autoRouting,
  } = useStore()
  const [enabled, setEnabled] = useState(false)
  const [classifierModelId, setClassifierModelId] = useState('')
  const [economyModelIds, setEconomyModelIds] = useState<string[]>([])
  const [qualityEnabled, setQualityEnabled] = useState(false)
  const [qualityModelIds, setQualityModelIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = useRef(false)

  useEffect(() => {
    void loadGovernance()
  }, [loadGovernance])

  useEffect(() => {
    if (!governance || dirty.current) return
    setEnabled(governance.adaptiveRoutingEnabled ?? false)
    setClassifierModelId(governance.adaptiveClassifierModelId ?? '')
    setEconomyModelIds(governance.adaptiveEconomyModelIds ?? [])
    setQualityEnabled(governance.adaptiveQualityEnabled ?? false)
    setQualityModelIds(governance.adaptiveQualityModelIds ?? [])
  }, [governance])

  const classifiers = models.filter(
    (model) =>
      model.kinds.includes('chat') &&
      model.dataBoundary === 'self_hosted' &&
      model.strictLocal &&
      model.inputCreditCost === 0 &&
      model.creditCost === 0,
  )
  const economyModels = models.filter(
    (model) =>
      model.kinds.includes('chat') &&
      !model.privacyOnly &&
      model.dataBoundary !== 'hybrid' &&
      model.dataBoundary !== 'unknown',
  )
  //: Every chat model. Deliberately unfiltered by price — an upgrade list is
  //: a finding about which models answer better here, and on this instance a
  //: 122b failed an outline call a 35b completed. The order is the finding.
  const qualityModels = models.filter((model) => model.kinds.includes('chat'))
  //: Each lane is asked only about itself. Gating the upgrade list behind the
  //: economy one made an administrator who wanted only the upgrade pick
  //: 절약 모델 they had no use for, and told them so in the warning.
  const canSave =
    (!enabled || Boolean(classifierModelId && economyModelIds.length > 0)) &&
    (!qualityEnabled || Boolean(classifierModelId && qualityModelIds.length > 0))

  const moveWithin = (
    setIds: (updater: (current: string[]) => string[]) => void,
    id: string,
    offset: -1 | 1,
  ) => {
    setIds((current) => {
      const at = current.indexOf(id)
      const to = at + offset
      if (at < 0 || to < 0 || to >= current.length) return current
      const next = [...current]
      const moved = next[at]
      next[at] = next[to]
      next[to] = moved
      return next
    })
    dirty.current = true
    setSaved(false)
    setError(null)
  }
  const updateOrder = (id: string, offset: -1 | 1) =>
    moveWithin(setEconomyModelIds, id, offset)

  const touched = () => {
    dirty.current = true
    setSaved(false)
    setError(null)
  }

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
          <Gauge size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('Auto 비용 절약 라우팅')}</p>
          <p className="text-sm text-muted">
            {t('간단한 일반 채팅만 순서대로 지정한 절약 모델로 보내고, 복잡하거나 지원하지 않는 요청은 사용자가 선택한 품질 모델을 유지합니다.')}
          </p>
        </div>
        <Switch
          checked={enabled}
          onChange={(value) => {
            setEnabled(value)
            dirty.current = true
            setSaved(false)
            setError(null)
          }}
          label={t('Auto 비용 절약 라우팅')}
        />
      </div>

      <div className="grid gap-4 border-t border-line pt-4 lg:grid-cols-2">
        <Field
          label={t('난이도 분류 모델')}
          hint={t('외부 전환이 없는 무료 strict-local 모델만 사용할 수 있습니다.')}
        >
          <select
            value={classifierModelId}
            onChange={(event) => {
              setClassifierModelId(event.target.value)
              dirty.current = true
              setSaved(false)
              setError(null)
            }}
            className="w-full rounded-control border border-line bg-panel px-3 py-2 text-base outline-none focus:border-accent"
          >
            <option value="">{t('분류 모델 선택')}</option>
            {classifierModelId &&
              !classifiers.some((model) => model.id === classifierModelId) && (
                <option value={classifierModelId}>
                  {classifierModelId} · {t('현재 사용 불가')}
                </option>
              )}
            {classifiers.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label={t('절약 모델 추가')}
          hint={t('최대 3개까지 추가할 수 있으며 위에서부터 사용 가능 여부를 확인합니다.')}
        >
          {/* Greyed out at three, and the hint above it is read once and then
              forgotten — so the reason is carried by the control that stopped
              responding, where it is looked for. */}
          <select
            value=""
            disabled={economyModelIds.length >= 3}
            title={
              economyModelIds.length >= 3 ? t('절약 모델은 3개까지만 추가할 수 있습니다') : undefined
            }
            onChange={(event) => {
              const id = event.target.value
              if (!id || economyModelIds.includes(id)) return
              setEconomyModelIds((current) => [...current, id].slice(0, 3))
              dirty.current = true
              setSaved(false)
              setError(null)
            }}
            className="w-full rounded-control border border-line bg-panel px-3 py-2 text-base outline-none focus:border-accent disabled:opacity-50"
          >
            <option value="">
              {economyModelIds.length >= 3 ? t('최대 3개를 선택했습니다') : t('절약 모델 선택')}
            </option>
            {economyModels
              .filter((model) => !economyModelIds.includes(model.id))
              .map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} · {model.inputCreditCost}/{model.creditCost}
                </option>
              ))}
          </select>
        </Field>
      </div>

      <div>
        <p className="mb-2 text-sm font-medium">{t('절약 모델 우선순위')}</p>
        {economyModelIds.length > 0 ? (
          <ol className="space-y-1.5">
            {economyModelIds.map((id, index) => {
              const model = models.find((candidate) => candidate.id === id)
              return (
                <li
                  key={id}
                  className="flex min-w-0 items-center gap-2 rounded-control border border-line px-2.5 py-2"
                >
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-elevated text-xs font-semibold">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-base">{model?.label ?? id}</span>
                  {model && (
                    <Badge
                      className="hidden sm:inline-flex"
                      tone={model.dataBoundary === 'self_hosted' ? 'success' : 'neutral'}
                    >
                      {model.dataBoundary === 'self_hosted' ? 'self-hosted' : t('외부 제공')}
                    </Badge>
                  )}
                  {/* An arrow that cannot move is the row telling you it is
                      already at the end of the list, but only if you already
                      read the numbering that way. The tooltip says it outright,
                      at the arrow that refused. */}
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={index === 0}
                    aria-label={t('{name} 우선순위 올리기').replace('{name}', model?.label ?? id)}
                    title={
                      index === 0
                        ? t('이미 가장 먼저 시도하는 모델입니다')
                        : t('한 칸 위로 올려 더 먼저 시도합니다')
                    }
                    onClick={() => updateOrder(id, -1)}
                  >
                    <ArrowUp size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={index === economyModelIds.length - 1}
                    aria-label={t('{name} 우선순위 내리기').replace('{name}', model?.label ?? id)}
                    title={
                      index === economyModelIds.length - 1
                        ? t('이미 가장 나중에 시도하는 모델입니다')
                        : t('한 칸 아래로 내려 더 나중에 시도합니다')
                    }
                    onClick={() => updateOrder(id, 1)}
                  >
                    <ArrowDown size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t('{name} 제거').replace('{name}', model?.label ?? id)}
                    title={t('이 모델을 절약 목록에서 뺍니다')}
                    onClick={() => {
                      setEconomyModelIds((current) => current.filter((candidate) => candidate !== id))
                      dirty.current = true
                      setSaved(false)
                      setError(null)
                    }}
                  >
                    <X size={14} />
                  </Button>
                </li>
              )
            })}
          </ol>
        ) : (
          <p className="rounded-control border border-dashed border-line px-3 py-3 text-sm text-faint">
            {t('선택한 절약 모델이 없습니다.')}
          </p>
        )}
      </div>

      {/* The other direction, on the same switch and the same classifier. Left
          empty the lane simply is not offered, which is what every installation
          starts with — an upgrade path that turned itself on would spend money
          nobody agreed to. */}
      {/* Drawn like the lane above it, because it is the same kind of thing
          pointed the other way: two opposite decisions about money, sharing
          only the classifier that judges the turn. A control that looked
          different would suggest the two answer to different rules. */}
      <div className="space-y-4 border-t border-line pt-4">
        <div className="flex items-start gap-3">
          <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
            <Sparkles size={17} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-base font-medium">{t('Auto 품질 우선 라우팅')}</p>
            <p className="text-sm text-muted">
              {t('복잡하다고 분류된 요청만 지정한 상위 모델로 보내고, 그 밖에는 사용자가 선택한 모델을 유지합니다. 데이터가 지금보다 멀리 나가지는 않습니다.')}
            </p>
          </div>
          <Switch
            checked={qualityEnabled}
            onChange={(value) => {
              setQualityEnabled(value)
              touched()
            }}
            label={t('Auto 품질 우선 라우팅')}
          />
        </div>

        <Field
          label={t('상향 모델 추가')}
          hint={t('최대 3개까지 추가할 수 있으며 위에서부터 사용 가능 여부를 확인합니다.')}
        >
          <select
            value=""
            disabled={qualityModelIds.length >= 3}
            title={
              qualityModelIds.length >= 3 ? t('상향 모델은 3개까지만 추가할 수 있습니다') : undefined
            }
            onChange={(event) => {
              const id = event.target.value
              if (!id || qualityModelIds.includes(id)) return
              setQualityModelIds((current) => [...current, id].slice(0, 3))
              touched()
            }}
            className="w-full rounded-control border border-line bg-panel px-3 py-2 text-base outline-none focus:border-accent disabled:opacity-50"
          >
            <option value="">
              {qualityModelIds.length >= 3 ? t('최대 3개를 선택했습니다') : t('상향 모델 선택')}
            </option>
            {qualityModels
              .filter((model) => !qualityModelIds.includes(model.id))
              .map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label} · {model.inputCreditCost}/{model.creditCost}
                </option>
              ))}
          </select>
        </Field>
        <p className="mb-2 text-sm font-medium">{t('상향 모델 우선순위')}</p>
        {qualityModelIds.length > 0 ? (
          <ol className="space-y-1.5">
            {qualityModelIds.map((id, index) => {
              const model = models.find((candidate) => candidate.id === id)
              return (
                <li
                  key={id}
                  className="flex min-w-0 items-center gap-2 rounded-control border border-line px-2.5 py-2"
                >
                  <span className="grid size-6 shrink-0 place-items-center rounded-full bg-elevated text-xs font-semibold">
                    {index + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-base">{model?.label ?? id}</span>
                  {model && (
                    <Badge
                      className="hidden sm:inline-flex"
                      tone={model.dataBoundary === 'self_hosted' ? 'success' : 'neutral'}
                    >
                      {model.dataBoundary === 'self_hosted' ? 'self-hosted' : t('외부 제공')}
                    </Badge>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={index === 0}
                    aria-label={t('{name} 우선순위 올리기').replace('{name}', model?.label ?? id)}
                    title={
                      index === 0
                        ? t('이미 가장 먼저 시도하는 모델입니다')
                        : t('한 칸 위로 올려 더 먼저 시도합니다')
                    }
                    onClick={() => moveWithin(setQualityModelIds, id, -1)}
                  >
                    <ArrowUp size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={index === qualityModelIds.length - 1}
                    aria-label={t('{name} 우선순위 내리기').replace('{name}', model?.label ?? id)}
                    title={
                      index === qualityModelIds.length - 1
                        ? t('이미 가장 나중에 시도하는 모델입니다')
                        : t('한 칸 아래로 내려 더 나중에 시도합니다')
                    }
                    onClick={() => moveWithin(setQualityModelIds, id, 1)}
                  >
                    <ArrowDown size={14} />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t('{name} 제거').replace('{name}', model?.label ?? id)}
                    title={t('이 모델을 상향 목록에서 뺍니다')}
                    onClick={() => {
                      setQualityModelIds((current) =>
                        current.filter((candidate) => candidate !== id),
                      )
                      touched()
                    }}
                  >
                    <X size={14} />
                  </Button>
                </li>
              )
            })}
          </ol>
        ) : (
          <p className="rounded-control border border-dashed border-line px-3 py-3 text-sm text-faint">
            {t('선택한 상향 모델이 없습니다. 품질 우선 Auto 는 표시되지 않습니다.')}
          </p>
        )}
      </div>

      {enabled && classifiers.length === 0 && (
        <p className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-warn">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {t('현재 사용할 수 있는 무료 strict-local 분류 모델이 없습니다. 설정을 켜도 Auto를 제공하지 않습니다.')}
        </p>
      )}

      {error && (
        <p role="alert" className="rounded-control border border-danger/30 bg-danger/5 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Button
          variant="primary"
          disabled={busy || !canSave}
          onClick={async () => {
            setBusy(true)
            setSaved(false)
            setError(null)
            try {
              await saveGovernance({
                adaptiveRoutingEnabled: enabled,
                adaptiveQualityEnabled: qualityEnabled,
                // The classifier is shared, so it goes with either lane being
                // on. The lists follow their own switch.
                ...(enabled || qualityEnabled
                  ? { adaptiveClassifierModelId: classifierModelId || null }
                  : {}),
                ...(enabled ? { adaptiveEconomyModelIds: economyModelIds } : {}),
                ...(qualityEnabled ? { adaptiveQualityModelIds: qualityModelIds } : {}),
              })
              dirty.current = false
              await loadModels()
              setSaved(true)
            } catch (saveError) {
              setError(errorMessage(saveError, t('라우팅 설정을 저장하지 못했습니다.')))
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? t('저장 중…') : t('라우팅 설정 저장')}
        </Button>
        {saved && <span className="text-sm text-success">{t('저장했습니다.')}</span>}
        {!canSave && (
          <span className="text-sm text-warn">
            {enabled && !(classifierModelId && economyModelIds.length > 0)
              ? t('분류 모델과 절약 모델을 한 개 이상 선택하세요.')
              : t('분류 모델과 상향 모델을 한 개 이상 선택하세요.')}
          </span>
        )}
        {!saved && autoRouting.enabled && !autoRouting.available && (
          <span className="text-sm text-warn">
            {t('현재 사용자에게 제공할 수 있는 Auto 경로가 없습니다.')}
          </span>
        )}
      </div>
    </Card>
  )
}
