import { ListTree } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Card, Field } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * Which model plans a document, when it should not be the one that writes it.
 *
 * A deck is one outline call and one call per slide. The outline decides the
 * shape — how many slides and what each one is — and it is the call a small
 * model gets visibly wrong: on this instance `bullets` was 77% of every body
 * slide it planned, with four layouts available. Naming a stronger model here
 * buys that single call and leaves the per-block cost alone, which is why it
 * is a separate setting rather than a better default model.
 */
export function OutlineModelSection() {
  const t = useT()
  const { governance, loadGovernance, saveGovernance, models, loadModels } = useStore()
  const [modelId, setModelId] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const dirty = useRef(false)

  useEffect(() => {
    void loadGovernance()
    void loadModels()
  }, [loadGovernance, loadModels])

  useEffect(() => {
    if (!governance || dirty.current) return
    setModelId(governance.outlineModelId ?? '')
  }, [governance])

  // Whatever can write a document can plan one; the plan is a shorter answer
  // in the same language about the same request.
  const planners = models.filter(
    (model) => model.kinds.includes('slides') || model.kinds.includes('report'),
  )

  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      await saveGovernance({ outlineModelId: modelId || null })
      dirty.current = false
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
          <ListTree size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('구성 단계 모델')}</p>
          <p className="text-sm text-muted">
            {t('보고서·슬라이드는 구성을 한 번 잡고 그 뒤에 한 절씩 씁니다. 구성 호출은 문서당 한 번뿐이라 여기만 다른 모델로 두어도 비용은 거의 그대로입니다. 다만 이 인스턴스에서 재어 보니 레이아웃 다양성 같은 구조 지표는 달라지지 않았습니다 — 그 부분은 프롬프트 쪽에서 이미 해결됐습니다.')}
          </p>
        </div>
      </div>

      <div className="border-t border-line pt-4">
        <Field
          label={t('구성에 쓸 모델')}
          hint={t('비워 두면 각 화면이 쓰는 모델이 구성까지 맡습니다. 본문은 언제나 화면의 모델이 씁니다. 개인정보 때문에 strict-local 로 보낸 대화, 그리고 본문 모델보다 데이터 경계가 넓은 경우에는 이 설정이 적용되지 않습니다.')}
        >
          <select
            aria-label={t('구성에 쓸 모델')}
            value={modelId}
            onChange={(event) => {
              setModelId(event.target.value)
              dirty.current = true
              setSaved(false)
              setError(null)
            }}
            className="w-full rounded-control border border-line bg-panel px-3 py-2 text-base outline-none focus:border-accent"
          >
            <option value="">{t('화면의 모델을 그대로 사용')}</option>
            {/* A model that has been removed from the catalogue still has to be
                visible here, or the screen would silently show "same model" for
                a policy that names something else. */}
            {modelId && !planners.some((model) => model.id === modelId) && (
              <option value={modelId}>
                {modelId} · {t('현재 사용 불가')}
              </option>
            )}
            {planners.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label} · {model.inputCreditCost}/{model.creditCost}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={() => void save()} disabled={busy}>
          {busy ? t('저장 중…') : t('저장')}
        </Button>
        {saved && <span className="text-sm text-success">{t('저장했습니다')}</span>}
        {error && <span className="text-sm text-danger">{error}</span>}
      </div>
    </Card>
  )
}
