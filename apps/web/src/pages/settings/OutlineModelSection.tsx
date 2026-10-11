import { ListTree } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Button, Card, Field, Select } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { tokenRate } from '@/lib/utils'

/** Single outline call that plans a document; per-block calls keep the surface model. */
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
            {t('보고서·슬라이드의 구성을 이 모델이 잡고, 쓰는 동안 자료의 수치 읽기·계산·검사 같은 판정도 이 모델이 맡습니다. 본문은 언제나 화면의 모델이 씁니다. 판정 호출은 짧아서 여기만 다른 모델로 두어도 비용은 거의 그대로입니다.')}
          </p>
        </div>
      </div>

      <div className="border-t border-line pt-4">
        <Field
          label={t('구성에 쓸 모델')}
          hint={t('비워 두면 각 화면이 쓰는 모델이 구성과 판정까지 맡습니다. 개인정보 때문에 strict-local 로 보낸 대화에서는 이 모델의 strict-local 판이 맡고, 그런 판이 없거나 본문 모델보다 데이터 경계가 넓으면 본문 모델이 맡습니다.')}
        >
          <Select
            aria-label={t('구성에 쓸 모델')}
            value={modelId}
            onChange={(event) => {
              setModelId(event.target.value)
              dirty.current = true
              setSaved(false)
              setError(null)
            }}
          >
            <option value="">{t('화면의 모델을 그대로 사용')}</option>
            {/* Stored model missing from the catalogue stays selectable, so the policy is not misread. */}
            {modelId && !planners.some((model) => model.id === modelId) && (
              <option value={modelId}>
                {modelId} · {t('현재 사용 불가')}
              </option>
            )}
            {planners.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label} · {tokenRate(model, t)}
              </option>
            ))}
          </Select>
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
