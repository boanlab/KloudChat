import {
  BookOpenCheck, CalendarRange, FileQuestion, FileText, GalleryVerticalEnd,
  ListChecks, Loader2, MoreHorizontal, Network, RefreshCw, ShieldCheck, SpellCheck, Trash2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Badge, Button, Card, ConfirmDialog, Dropdown, EmptyState, MenuItem } from '@/components/ui'
import { ApiError, errorMessage, shelfApi } from '@/lib/api'
import { poll, type ShelfSource, type StudioItem } from '@/lib/shelf'
import { DOCUMENT_KINDS, LEARNING_KINDS, SHELF_NAMES, STUDIO_KINDS, withNames, type StudioKind } from '@/lib/shelfNames'
import { useT } from '@/lib/useT'
import { relativeTime } from '@/lib/utils'
import { FlashcardPlayer } from './FlashcardPlayer'
import { QuizPlayer } from './QuizPlayer'
import { shelfFailure } from './failures'
import { SourcePicker } from './SourcePicker'
import { StudioDialog } from './StudioDialog'
import { StudioReader } from './StudioReader'

const ICONS: Record<StudioKind, typeof FileText> = {
  study_guide: BookOpenCheck,
  faq: FileQuestion,
  briefing: FileText,
  timeline: CalendarRange,
  glossary: SpellCheck,
  mindmap: Network,
  quiz: ListChecks,
  flashcards: GalleryVerticalEnd,
}

/**
 * A project's 스튜디오: pick the sources, then one click for a study guide, FAQ, briefing,
 * timeline, glossary, mind map, quiz or flashcards, each grounded in those
 * sources with citations. Outputs list below; documents open in the document panel.
 */
export function StudioTab({ projectId }: { projectId: string }) {
  const t = useT()
  const navigate = useNavigate()
  const [sources, setSources] = useState<ShelfSource[] | null>(null)
  // unknown until the listing answers: the strict-local line is not claimed while loading
  const [strict, setStrict] = useState<boolean | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [items, setItems] = useState<StudioItem[] | null>(null)
  const [asking, setAsking] = useState<StudioKind | null>(null)
  const [open, setOpen] = useState<StudioItem | null>(null)
  const [removing, setRemoving] = useState<StudioItem | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadItems = useCallback(() => shelfApi.studio(projectId).then(setItems), [projectId])
  useEffect(() => {
    let alive = true
    shelfApi
      .sources(projectId)
      .then((listing) => {
        if (!alive) return
        setSources(listing.sources)
        setStrict(listing.strictLocal)
        setPicked(new Set(listing.sources.filter((s) => s.readable).map((s) => s.id)))
      })
      .catch((err) => alive && setError(errorMessage(err, t('자료를 불러오지 못했습니다.'))))
    loadItems().catch((err) => alive && setError(errorMessage(err, t('결과를 불러오지 못했습니다.'))))
    return () => {
      alive = false
    }
  }, [projectId, loadItems, t])

  const running = useMemo(() => (items ?? []).some((i) => i.state === 'running'), [items])
  useEffect(() => {
    if (!running) return
    return poll(() => shelfApi.studio(projectId), (rows) => rows.some((i) => i.state === 'running'), setItems)
  }, [running, projectId])

  const openItem = (item: StudioItem) => {
    if (item.state !== 'done') return
    setOpen(item)
  }
  const regenerate = async (item: StudioItem) => {
    try {
      await shelfApi.regenerate(item.id)
      await loadItems()
    } catch (err) {
      setError(err instanceof ApiError ? shelfFailure(err.detail, t) : errorMessage(err, t('다시 만들지 못했습니다.')))
    }
  }

  const cards = (kinds: StudioKind[]) => (
    <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {kinds.map((kind) => {
        const Icon = ICONS[kind]
        return (
          <button
            key={kind}
            type="button"
            data-studio-kind={kind}
            disabled={!picked.size}
            onClick={() => setAsking(kind)}
            className="flex items-start gap-2.5 rounded-card border border-line bg-panel px-3 py-2.5 text-left transition-colors hover:border-accent hover:bg-accent-soft disabled:opacity-50"
          >
            <Icon size={18} className="mt-0.5 shrink-0 text-accent" />
            <span className="min-w-0">
              <span className="block text-base font-medium">{t(STUDIO_KINDS[kind].name)}</span>
              <span className="block text-sm text-muted">{t(STUDIO_KINDS[kind].about)}</span>
            </span>
          </button>
        )
      })}
    </div>
  )

  return (
    <div className="space-y-4" data-testid="studio">
      <div className="rounded-card border border-dashed border-line-strong px-4 py-3">
        <p className="text-base font-medium">{t(SHELF_NAMES.studio)}</p>
        <p className="text-sm text-muted">
          {withNames(t('고른 자료만으로 정리 문서·퀴즈·플래시카드를 만듭니다. 문장마다 어느 자료에서 왔는지 번호로 남기고, 번호를 누르면 원문 대목이 열립니다.'), t)}
        </p>
        {strict === true && (
          <p className="mt-1 flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck size={12} className="text-success" />
            {t('내부 자료라 strict-local 모델로만 만듭니다.')}
          </p>
        )}
      </div>
      {error && <p role="alert" className="text-base text-danger">{error}</p>}
      {sources === null && !error ? (
        <p className="flex items-center gap-2 text-base text-muted"><Loader2 size={14} className="animate-spin" />{t('불러오는 중…')}</p>
      ) : sources && sources.filter((s) => s.readable).length === 0 ? (
        <EmptyState icon={<FileText size={18} />} title={t('읽을 수 있는 자료가 없습니다')} description={t('지식 탭에서 파일·웹 자료·텍스트를 먼저 넣으세요.')} />
      ) : (
        sources && (
          <>
            <SourcePicker sources={sources} picked={picked} onPick={setPicked} />
            <div className="space-y-2">
              <p className="text-sm font-medium text-muted">{t('정리 문서')}</p>
              {cards(DOCUMENT_KINDS)}
            </div>
            <div className="space-y-2">
              <p className="text-sm font-medium text-muted">{t('학습')}</p>
              {cards(LEARNING_KINDS)}
            </div>
          </>
        )
      )}

      <div className="space-y-2">
        <p className="text-base font-medium">{t('만든 결과')}</p>
        {items?.length === 0 && <p className="text-sm text-muted">{t('아직 만든 결과가 없습니다.')}</p>}
        {(items ?? []).map((item) => {
          const Icon = ICONS[item.kind]
          return (
            <Card key={item.id} className="flex items-center gap-3 px-3 py-2.5" data-studio-item={item.id} data-state={item.state}>
              <Icon size={16} className="shrink-0 text-accent" />
              <button
                type="button"
                disabled={item.state !== 'done'}
                onClick={() => openItem(item)}
                className="min-w-0 flex-1 text-left disabled:cursor-default"
              >
                <span className="block truncate text-base font-medium">{item.title}</span>
                <span className="block text-xs text-faint">
                  {t(STUDIO_KINDS[item.kind].name)} · {t('자료 {n}개').replace('{n}', String(item.sourceIds.length))} · {relativeTime(item.createdAt)}
                  {item.credits > 0 && ` · ${t('{n} 크레딧').replace('{n}', String(item.credits))}`}
                </span>
                {item.state === 'failed' && <span className="block text-xs text-danger">{shelfFailure(item.error, t)}</span>}
              </button>
              {item.state === 'running' && (
                <Badge><Loader2 size={11} className="animate-spin" />{t('만드는 중')}</Badge>
              )}
              {item.state === 'done' && item.sessionId && item.artifactId && (
                <Button size="sm" className="max-sm:hidden" onClick={() => navigate(`/s/${item.sessionId}?artifact=${item.artifactId}`)}>
                  {t('문서로 열기')}
                </Button>
              )}
              <Dropdown
                align="right"
                trigger={() => (
                  <Button variant="ghost" size="icon" aria-label={t('{name} 더 보기').replace('{name}', item.title)}>
                    <MoreHorizontal size={15} />
                  </Button>
                )}
              >
                <MenuItem icon={<RefreshCw size={14} />} disabled={item.state === 'running'} onClick={() => void regenerate(item)}>
                  {t('다시 만들기')}
                </MenuItem>
                <MenuItem danger icon={<Trash2 size={14} />} onClick={() => setRemoving(item)}>
                  {t('삭제')}
                </MenuItem>
              </Dropdown>
            </Card>
          )
        })}
      </div>

      {asking && (
        <StudioDialog
          projectId={projectId}
          kind={asking}
          sourceIds={[...picked]}
          onClose={() => setAsking(null)}
          onStarted={() => void loadItems()}
        />
      )}
      {open && DOCUMENT_KINDS.includes(open.kind) && <StudioReader item={open} onClose={() => setOpen(null)} />}
      {open?.kind === 'quiz' && <QuizPlayer item={open} onClose={() => setOpen(null)} />}
      {open?.kind === 'flashcards' && <FlashcardPlayer item={open} onClose={() => setOpen(null)} />}
      <ConfirmDialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const target = removing
          setRemoving(null)
          if (!target) return
          shelfApi
            .remove(target.id)
            .then(() => setItems((rows) => (rows ?? []).filter((row) => row.id !== target.id)))
            .catch((err) => setError(errorMessage(err, t('삭제하지 못했습니다.'))))
        }}
        title={t('{name} 삭제').replace('{name}', removing?.title ?? '')}
        description={withNames(t('{studio}에서 지웁니다. 문서로 저장된 것은 아티팩트에 남습니다.'), t)}
      />
    </div>
  )
}
