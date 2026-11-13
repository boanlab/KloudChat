import { Gauge, RotateCcw, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Card, Field, Select, Switch } from '@/components/ui'
import { errorCode, errorMessage, type SuperAgentRule } from '@/lib/api'
import { SUPER_CLASSES, SUPER_CLASS_LABEL, SUPER_EFFORT_LABEL } from '@/lib/superAgent'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { useUnsavedMark } from '@/lib/unsaved'
import { tokenRate } from '@/lib/utils'
import type { ModelInfo, SuperAgentClass } from '@/types'

/** Super Agent policy refusal codes. */
const SUPER_REFUSALS: Record<string, string> = {
  super_agent_class_unknown: '알 수 없는 작업 종류가 있습니다.',
  super_agent_effort_invalid: '추론 강도 값이 올바르지 않습니다.',
  super_agent_model_invalid: '표에 이 서버가 제공하지 않거나 이 작업에 쓸 수 없는 모델이 있습니다.',
  super_agent_strict_model_must_be_strict_local: 'strict-local 열에는 strict-local 모델만 넣을 수 있습니다.',
  adaptive_classifier_must_be_zero_cost_strict_local: '분류 모델은 무료 strict-local 모델이어야 합니다.',
}

const EFFORTS = Object.keys(SUPER_EFFORT_LABEL) as SuperAgentRule['effort'][]

/** The Super Agent switch, its classifier and the class → model table (설정 › 시스템 › 라우팅). */
export function SuperAgentSection() {
  const t = useT()
  const { governance, loadGovernance, saveGovernance, models, loadModels, superAgent } = useStore()
  const [enabled, setEnabled] = useState(false)
  const [classifierModelId, setClassifierModelId] = useState('')
  const [rules, setRules] = useState<Record<string, SuperAgentRule>>({})
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = useRef(false)
  const markUnsaved = useUnsavedMark('system.superagent')
  const touched = () => {
    dirty.current = true
    markUnsaved(true)
    setSaved(false)
    setError(null)
  }

  useEffect(() => {
    void loadGovernance()
  }, [loadGovernance])

  useEffect(() => {
    if (!governance || dirty.current) return
    setEnabled(governance.superAgentEnabled ?? false)
    setClassifierModelId(governance.adaptiveClassifierModelId ?? '')
    setRules(governance.superAgentRules ?? {})
  }, [governance])

  const defaults = governance?.superAgentDefaultRules ?? {}
  const chatModels = models.filter((model) => model.kinds.includes('chat'))
  const documentModels = models.filter((model) => model.kinds.includes('report'))
  const strictOnly = (pool: ModelInfo[]) =>
    pool.filter((model) => model.dataBoundary === 'self_hosted' && model.strictLocal)
  const classifiers = strictOnly(chatModels).filter(
    (model) => model.inputCreditCost === 0 && model.creditCost === 0,
  )
  const setRule = (name: SuperAgentClass, patch: Partial<SuperAgentRule>) => {
    setRules((current) => ({ ...current, [name]: { ...current[name], ...patch } }))
    touched()
  }
  const options = (pool: ModelInfo[], current: string) => (
    <>
      {current && !pool.some((model) => model.id === current) && (
        <option value={current}>
          {current} · {t('현재 사용 불가')}
        </option>
      )}
      {pool.map((model) => (
        <option key={model.id} value={model.id}>
          {model.label} · {tokenRate(model, t)}
        </option>
      ))}
    </>
  )

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent max-sm:hidden">
          <Gauge size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">Super Agent</p>
          <p className="text-sm text-muted">
            {t('질문마다 작업 종류를 먼저 규칙으로 가리고, 애매할 때만 분류 모델에 짧게 물어 아래 표의 모델과 추론 강도로 답합니다. 개인정보나 내부 자료가 있으면 strict-local 열의 모델을 씁니다.')}
          </p>
        </div>
        <Switch
          checked={enabled}
          onChange={(value) => {
            setEnabled(value)
            touched()
          }}
          label={t('모델 선택기에 Super Agent 보이기')}
        />
      </div>

      <Field
        label={t('분류 모델')}
        hint={t('규칙으로 가리지 못한 질문만 보냅니다. 질문 한 개만 짧게 보내며, 외부 전환이 없는 무료 strict-local 모델만 쓸 수 있습니다.')}
      >
        <Select
          value={classifierModelId}
          onChange={(event) => {
            setClassifierModelId(event.target.value)
            touched()
          }}
        >
          <option value="">{t('표의 「간단한 질문」 strict-local 모델')}</option>
          {options(classifiers, classifierModelId)}
        </Select>
      </Field>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <caption className="sr-only">{t('작업 종류별 모델')}</caption>
          <thead>
            <tr className="text-left text-xs text-faint">
              <th scope="col" className="py-1.5 pr-2 font-medium">{t('작업 종류')}</th>
              <th scope="col" className="py-1.5 pr-2 font-medium">Super Agent</th>
              <th scope="col" className="py-1.5 pr-2 font-medium">Super Agent (strict-local)</th>
              <th scope="col" className="py-1.5 font-medium">{t('추론 강도')}</th>
            </tr>
          </thead>
          <tbody>
            {SUPER_CLASSES.map((name) => {
              const rule = rules[name] ?? defaults[name]
              if (!rule) return null
              const pool = name === 'document' ? documentModels : chatModels
              const label = t(SUPER_CLASS_LABEL[name])
              return (
                <tr key={name} className="border-t border-line align-top">
                  <th scope="row" className="py-2 pr-2 text-left font-medium">{label}</th>
                  <td className="py-2 pr-2">
                    <Select
                      aria-label={t('{class} · Super Agent 모델').replace('{class}', label)}
                      value={rule.model}
                      onChange={(event) => setRule(name, { model: event.target.value })}
                    >
                      {options(pool, rule.model)}
                    </Select>
                  </td>
                  <td className="py-2 pr-2">
                    <Select
                      aria-label={t('{class} · strict-local 모델').replace('{class}', label)}
                      value={rule.strictModel}
                      onChange={(event) => setRule(name, { strictModel: event.target.value })}
                    >
                      {options(strictOnly(pool), rule.strictModel)}
                    </Select>
                  </td>
                  <td className="py-2">
                    <Select
                      aria-label={t('{class} · 추론 강도').replace('{class}', label)}
                      value={rule.effort}
                      onChange={(event) =>
                        setRule(name, { effort: event.target.value as SuperAgentRule['effort'] })}
                    >
                      {EFFORTS.map((effort) => (
                        <option key={effort} value={effort}>{t(SUPER_EFFORT_LABEL[effort])}</option>
                      ))}
                    </Select>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-faint">
        {t('다시 해 달라거나 더 자세히 해 달라는 요청, 비었거나 실패한 답 뒤에는 「복잡한 추론」 줄의 모델과 높은 추론으로 올립니다. 같은 문서의 수정은 처음 쓴 모델을 유지합니다.')}
      </p>

      {enabled && classifiers.length === 0 && (
        <p className="flex items-start gap-2 rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-warn">
          <TriangleAlert size={14} className="mt-0.5 shrink-0" />
          {t('무료 strict-local 분류 모델이 없어 애매한 질문은 규칙의 기본값으로 보냅니다.')}
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
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            setSaved(false)
            setError(null)
            try {
              await saveGovernance({
                superAgentEnabled: enabled,
                superAgentRules: rules,
                ...(classifierModelId !== (governance?.adaptiveClassifierModelId ?? '')
                  ? { adaptiveClassifierModelId: classifierModelId || null }
                  : {}),
              })
              dirty.current = false
              markUnsaved(false)
              await loadModels()
              setSaved(true)
            } catch (saveError) {
              const reason = SUPER_REFUSALS[errorCode(saveError)]
              setError(
                reason ? t(reason) : errorMessage(saveError, t('Super Agent 설정을 저장하지 못했습니다.')),
              )
            } finally {
              setBusy(false)
            }
          }}
        >
          {busy ? t('저장 중…') : t('Super Agent 설정 저장')}
        </Button>
        <Button
          variant="ghost"
          disabled={busy}
          onClick={() => {
            setRules(defaults)
            touched()
          }}
        >
          <RotateCcw size={14} />
          {t('표를 기본값으로')}
        </Button>
        {saved && <span className="text-sm text-success">{t('저장했습니다.')}</span>}
        {!saved && superAgent.enabled && !superAgent.default.available && (
          <span className="text-sm text-warn">
            {t('표의 모델 중 지금 쓸 수 있는 것이 없어 Super Agent를 제공하지 못합니다.')}
          </span>
        )}
      </div>
    </Card>
  )
}
