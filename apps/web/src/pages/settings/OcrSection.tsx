import { ScanText } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Card, Field, Input, Select, Switch } from '@/components/ui'
import { adminApi, errorMessage, type SystemSettings } from '@/lib/api'
import { useT } from '@/lib/useT'

type Ocr = SystemSettings['ocr']

const MODE_LABEL: Record<Ocr['mode'], string> = {
  both: 'OCR 엔진 + 비전 모델',
  engine: 'OCR 엔진만',
  vision: '비전 모델만',
}

/** Text from scanned pages: on or off, which readers, which model may see a page. */
export function OcrSection({
  settings,
  onSaved,
}: {
  settings: SystemSettings | null
  onSaved: () => Promise<void>
}) {
  const t = useT()
  const [enabled, setEnabled] = useState(true)
  const [mode, setMode] = useState<Ocr['mode']>('both')
  const [visionModel, setVisionModel] = useState('')
  const [maxPages, setMaxPages] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!settings) return
    setEnabled(settings.ocr.enabled)
    setMode(settings.ocr.mode)
    setVisionModel(settings.ocr.visionModel)
    setMaxPages(String(settings.ocr.maxPages))
  }, [settings])

  if (!settings) return null
  const ocr = settings.ocr

  const save = async () => {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await adminApi.updateSettings({
        ocrEnabled: enabled ? '1' : '0',
        ocrMode: mode,
        ocrVisionModel: visionModel,
        ocrMaxPages: maxPages.trim(),
      })
      await onSaved()
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const inForce = !ocr.modeInForce
    ? t('지금은 글자를 읽을 수 없습니다. 아래 주소나 비전 모델을 확인하세요.')
    : t('지금 쓰는 방식: {mode}').replace('{mode}', t(MODE_LABEL[ocr.modeInForce]))

  return (
    <Card className="space-y-4 p-4" data-section="ocr">
      <div className="flex items-start gap-3">
        <div className="grid size-9 shrink-0 place-items-center rounded-control bg-accent-soft text-accent">
          <ScanText size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-base font-medium">{t('글자 읽기(OCR)')}</p>
          <p className="text-sm text-muted">
            {t('스캔한 PDF 쪽과 문서 사진의 글자를 읽어 대화·프로젝트·에이전트 자료에서 찾을 수 있게 합니다. 엔진이 먼저 읽고, 자신 없는 쪽과 표가 있는 쪽은 비전 모델이 다시 읽습니다. 두 결과가 다르면 「확인 필요」로 표시합니다.')}
          </p>
        </div>
        <Switch checked={enabled} onChange={setEnabled} label={t('글자 읽기(OCR)')} />
      </div>

      <div className="grid gap-4 border-t border-line pt-4 lg:grid-cols-3">
        <Field label={t('읽는 방식')}>
          <Select value={mode} onChange={(e) => setMode(e.target.value as Ocr['mode'])}>
            {(Object.keys(MODE_LABEL) as Ocr['mode'][]).map((key) => (
              <option key={key} value={key}>
                {t(MODE_LABEL[key])}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label={t('비전 모델')}
          hint={t('그림을 읽는 strict-local 모델만 고를 수 있습니다. 쪽 그림은 이 모델 밖으로 나가지 않습니다.')}
        >
          <Select value={visionModel} onChange={(e) => setVisionModel(e.target.value)}>
            <option value="">
              {ocr.visionChoices.length
                ? t('자동 ({name})').replace('{name}', ocr.visionChoices[0].label)
                : t('쓸 수 있는 모델 없음')}
            </option>
            {visionModel && !ocr.visionChoices.some((m) => m.id === visionModel) && (
              <option value={visionModel}>
                {visionModel} · {t('현재 사용 불가')}
              </option>
            )}
            {ocr.visionChoices.map((model) => (
              <option key={model.id} value={model.id}>
                {model.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={t('문서 하나에서 읽는 최대 쪽 수')} hint={t('1~2000쪽')}>
          <Input
            type="number"
            min={1}
            max={2000}
            value={maxPages}
            onChange={(e) => setMaxPages(e.target.value)}
            aria-label={t('문서 하나에서 읽는 최대 쪽 수')}
          />
        </Field>
      </div>

      <div className="flex flex-wrap items-center gap-3 border-t border-line pt-4">
        <p className="min-w-0 flex-1 text-sm text-muted" role="status">
          {inForce}
          {!ocr.engineConfigured && ` ${t('OCR 엔진 주소가 없어 PDF 쪽은 읽지 못하고 사진만 비전 모델로 읽습니다.')}`}
        </p>
        {error && <p className="text-sm text-danger">{error}</p>}
        {saved && !error && <p className="text-sm text-success">{t('저장했습니다')}</p>}
        <Button disabled={busy} onClick={() => void save()}>
          {t('저장')}
        </Button>
      </div>
    </Card>
  )
}
