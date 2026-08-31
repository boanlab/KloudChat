import { History } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Modal } from '@/components/ui'
import { artifactsApi, errorMessage } from '@/lib/api'
import type { ArtifactVersionRow } from '@/lib/api'
import { useStore } from '@/store/useStore'
import { relativeTime } from '@/lib/utils'
import { useT } from '@/lib/useT'

/**
 * The way back from an edit, for any artifact that keeps versions.
 *
 * Every write snapshots the revision it replaces and neither `versions` nor
 * `restore` looks at the kind, so the control is shared rather than written per
 * panel: the report had one and a deck or an HTML document did not, which made
 * "수정할 때마다 버전이 쌓이고" true of the storage and false of the screen.
 */
export function VersionHistory({
  artifact,
  onRestored,
}: {
  /** The document on screen. Structural, so every panel can pass its own. */
  artifact: { id: string; title: string; version: number }
  /** Panel-local state the restored document invalidates — a draft in an open
   *  editor is about slides or paragraphs that have just changed under it. */
  onRestored?: () => void
}) {
  const t = useT()
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const [open, setOpen] = useState(false)
  //: Real history, fetched when the dialog opens — the version number alone
  //: would print N identical rows.
  const [versions, setVersions] = useState<ArtifactVersionRow[] | null>(null)
  const [restoring, setRestoring] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const openHistory = async () => {
    setOpen(true)
    setError(null)
    setVersions(null)
    setVersions(await artifactsApi.versions(artifact.id).catch(() => []))
  }

  const restore = async (version: number) => {
    setRestoring(version)
    setError(null)
    try {
      // A restore is an edit like any other: the server snapshots what is on
      // screen now, so the way back from the way back is the newest row here.
      await artifactsApi.restore(artifact.id, version)
      // Through the store rather than by mutating the panel's copy: the panel
      // holds whatever the gallery listed, and every surface showing this
      // document — cards, header, the panel itself — has to move together.
      await refreshArtifact(artifact.id)
      onRestored?.()
      setOpen(false)
    } catch (err) {
      setError(errorMessage(err, t('되돌리지 못했습니다.')))
    } finally {
      setRestoring(null)
    }
  }

  return (
    <>
      {/* Both names, because an `aria-label` replaces the visible text rather
          than adding to it: the button read 저장 시점 v3 and answered only to
          버전 기록, so saying what is written on it reached nothing. The label
          it opens with is still 버전 기록, which is what the dialog is called
          and what the rest of this app already calls it. */}
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
          {versions?.length === 0 && (
            <p className="text-base text-faint">{t('아직 저장된 이전 판이 없습니다.')}</p>
          )}
          {/* Only superseded revisions come back — the current one is the
              document on screen, and offering to restore it would be a button
              that does nothing. */}
          {versions?.map(({ version: v, summary, createdAt }) => (
            <div
              key={v}
              className="flex items-center gap-3 rounded-card border border-line px-3 py-2.5"
            >
              <Badge>v{v}</Badge>
              <div className="min-w-0 flex-1">
                <p className="text-base">{summary || t('편집')}</p>
                <p className="text-xs text-faint">{relativeTime(createdAt)}</p>
              </div>
              <Button
                size="sm"
                disabled={restoring !== null}
                aria-label={t('v{n} 로 되돌리기').replace('{n}', String(v))}
                onClick={() => void restore(v)}
              >
                {restoring === v ? t('되돌리는 중…') : t('되돌리기')}
              </Button>
            </div>
          ))}
          {error && <p className="mt-2 text-base text-danger">{error}</p>}
        </div>
      </Modal>
    </>
  )
}
