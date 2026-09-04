import { History } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, ConfirmDialog, Modal } from '@/components/ui'
import { artifactsApi, errorMessage } from '@/lib/api'
import type { ArtifactVersionRow } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { relativeTime } from '@/lib/utils'
import { useT } from '@/lib/useT'

function historicalText(data: Record<string, unknown> | null): string {
  if (!data) return ''
  const plain = (value: unknown) => String(value ?? '')
    .replace(/<[^>]*>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (Array.isArray(data.sections)) {
    return data.sections.map((row) => {
      const section = row as Record<string, unknown>
      return [plain(section.heading), plain(section.content)].filter(Boolean).join('\n')
    }).filter(Boolean).join('\n\n').slice(0, 5000)
  }
  if (Array.isArray(data.slides)) {
    return data.slides.map((row, index) => {
      const slide = row as Record<string, unknown>
      const bullets = Array.isArray(slide.bullets) ? slide.bullets.map(plain).join('\n') : ''
      return [`${index + 1}. ${plain(slide.title)}`, plain(slide.body), bullets].filter(Boolean).join('\n')
    }).filter(Boolean).join('\n\n').slice(0, 5000)
  }
  return JSON.stringify(data, null, 2).slice(0, 5000)
}

type VersionComparison = {
  unit: '절' | '장'
  added: number
  removed: number
  modified: number
  moved: number
  unchanged: number
}

function compareVersion(
  historical: Record<string, unknown> | null,
  current: unknown,
): VersionComparison | null {
  if (!historical || !current || typeof current !== 'object') return null
  const currentData = current as Record<string, unknown>
  const field = Array.isArray(historical.sections) && Array.isArray(currentData.sections)
    ? 'sections'
    : Array.isArray(historical.slides) && Array.isArray(currentData.slides)
      ? 'slides'
      : null
  if (!field) return null
  const oldRows = historical[field] as Array<Record<string, unknown>>
  const currentRows = currentData[field] as Array<Record<string, unknown>>
  const keyed = (rows: Array<Record<string, unknown>>) => new Map(
    rows.map((row, index) => [String(row.id ?? `at-${index}`), row]),
  )
  const oldById = keyed(oldRows)
  const currentById = keyed(currentRows)
  const oldPosition = new Map(oldRows.map((row, index) => [String(row.id ?? `at-${index}`), index]))
  const currentPosition = new Map(currentRows.map((row, index) => [String(row.id ?? `at-${index}`), index]))
  let modified = 0
  let unchanged = 0
  for (const [id, row] of oldById) {
    const now = currentById.get(id)
    if (!now) continue
    if (JSON.stringify(row) === JSON.stringify(now)) unchanged += 1
    else modified += 1
  }
  return {
    unit: field === 'sections' ? '절' : '장',
    added: [...currentById.keys()].filter((id) => !oldById.has(id)).length,
    removed: [...oldById.keys()].filter((id) => !currentById.has(id)).length,
    modified,
    moved: [...oldById.keys()].filter((id) => currentById.has(id) && oldPosition.get(id) !== currentPosition.get(id)).length,
    unchanged,
  }
}

/** Version list, preview and restore for any artifact kind. */
export function VersionHistory({
  artifact,
  onRestored,
  hasUnsavedChanges = false,
  currentData,
}: {
  artifact: { id: string; title: string; version: number }
  /** Called after a restore so the panel can drop local drafts. */
  onRestored?: () => void
  hasUnsavedChanges?: boolean
  currentData?: unknown
}) {
  const t = useT()
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const [open, setOpen] = useState(false)
  const [versions, setVersions] = useState<ArtifactVersionRow[] | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)
  const [pendingRestore, setPendingRestore] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ version: number; text: string; comparison: VersionComparison | null } | null>(null)
  const [previewing, setPreviewing] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const openHistory = async () => {
    setOpen(true)
    setError(null)
    setPreview(null)
    setVersions(null)
    try {
      setVersions(await artifactsApi.versions(artifact.id))
    } catch (err) {
      setVersions([])
      setError(errorMessage(err, t('버전 기록을 불러오지 못했습니다.')))
    }
  }

  const restore = async (version: number) => {
    setRestoring(version)
    setError(null)
    try {
      // A restore snapshots the current revision first, so it stays restorable.
      await artifactsApi.restore(artifact.id, version)
      await refreshArtifact(artifact.id)
      onRestored?.()
      setOpen(false)
    } catch (err) {
      setError(errorMessage(err, t('되돌리지 못했습니다.')))
    } finally {
      setRestoring(null)
    }
  }

  const previewVersion = async (version: number) => {
    setPreviewing(version)
    setError(null)
    try {
      const row = await artifactsApi.version(artifact.id, version)
      setPreview({
        version,
        text: historicalText(row.data),
        comparison: compareVersion(row.data, currentData),
      })
    } catch (err) {
      setError(errorMessage(err, t('이전 내용을 불러오지 못했습니다.')))
    } finally {
      setPreviewing(null)
    }
  }

  return (
    <>
      {/* aria-label carries both names, since it replaces the visible text. */}
      <Button
        size="sm"
        aria-label={`${t('버전 기록')} · ${t('저장 시점')} v${artifact.version}`}
        onClick={() => void openHistory()}
      >
        <History size={13} />
        {t('저장 시점')} v{artifact.version}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('버전 기록')}
        description={`${artifact.title} · ${t('현재')} v${artifact.version}`}
      >
        <div className="space-y-1.5">
          {versions === null && <p className="text-base text-faint">{t('불러오는 중…')}</p>}
          {versions?.length === 0 && !error && (
            <p className="text-base text-faint">{t('아직 저장된 이전 판이 없습니다.')}</p>
          )}
          {/* The server returns superseded revisions only. */}
          {versions?.map(({ version: v, summary, createdAt }) => (
            <div
              key={v}
              className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-2 rounded-card border border-line px-3 py-2.5 sm:grid-cols-[auto_minmax(0,1fr)_auto]"
            >
              <Badge>v{v}</Badge>
              <div className="min-w-0 flex-1">
                <p className="text-base">{summary || t('편집')}</p>
                <p className="text-xs text-faint">{relativeTime(createdAt)}</p>
              </div>
              <div className="col-span-2 flex flex-wrap justify-end gap-1.5 sm:col-span-1">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={previewing !== null || restoring !== null}
                  aria-label={t('v{n} 내용 보기').replace('{n}', String(v))}
                  onClick={() => void previewVersion(v)}
                >
                  {previewing === v ? t('불러오는 중…') : t('내용 보기')}
                </Button>
                <Button
                  size="sm"
                  disabled={restoring !== null}
                  aria-label={t('v{n} 로 되돌리기').replace('{n}', String(v))}
                  onClick={() => setPendingRestore(v)}
                >
                  {restoring === v ? t('되돌리는 중…') : t('되돌리기')}
                </Button>
              </div>
            </div>
          ))}
          {preview && (
            <section aria-label={t('v{n} 내용 미리보기').replace('{n}', String(preview.version))} className="mt-3 rounded-card border border-line bg-elevated p-3">
              <div className="mb-2 flex items-center gap-2">
                <Badge>v{preview.version}</Badge>
                <p className="text-sm font-medium">{t('이전 내용')}</p>
              </div>
              {preview.comparison && (
                <div aria-label={t('현재 판과 변경 비교')} className="mb-3 flex flex-wrap gap-1.5">
                  <Badge>{t('추가')} {preview.comparison.added}{preview.comparison.unit}</Badge>
                  <Badge>{t('삭제')} {preview.comparison.removed}{preview.comparison.unit}</Badge>
                  <Badge>{t('수정')} {preview.comparison.modified}{preview.comparison.unit}</Badge>
                  <Badge>{t('이동')} {preview.comparison.moved}{preview.comparison.unit}</Badge>
                  <span className="self-center text-xs text-faint">
                    {t('동일')} {preview.comparison.unchanged}{preview.comparison.unit}
                  </span>
                </div>
              )}
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-muted">
                {preview.text || t('표시할 텍스트가 없습니다.')}
              </pre>
            </section>
          )}
          {error && <p className="mt-2 text-base text-danger">{error}</p>}
        </div>
      </Modal>
      <ConfirmDialog
        open={pendingRestore !== null}
        onClose={() => setPendingRestore(null)}
        title={t('v{n}으로 되돌릴까요?').replace('{n}', String(pendingRestore ?? ''))}
        description={hasUnsavedChanges
          ? t('저장하지 않은 변경 내용은 사라집니다. 현재 저장본은 버전 기록에 남아 다시 복원할 수 있습니다.')
          : t('현재 저장본은 버전 기록에 남아 다시 복원할 수 있습니다.')}
        confirmLabel={t('이 버전으로 복원')}
        onConfirm={() => {
          const version = pendingRestore
          setPendingRestore(null)
          if (version !== null) void restore(version)
        }}
      />
    </>
  )
}
