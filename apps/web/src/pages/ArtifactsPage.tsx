import { BarChart3, Code2, Globe, Layers, MessageSquare, MoreHorizontal, Trash2, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { ArtifactModal } from '@/components/artifacts/ArtifactModal'
import { ReuseItems } from '@/components/artifacts/ResultActions'
import { useGallery } from '@/components/artifacts/useGallery'
import { ColumnHeader, PhoneSort, useUrlSort, type SortField } from '@/components/files/ListSort'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dropdown,
  EmptyState,
  Input,
  PageHeader,
  ReloadNotice,
  Tabs,
} from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { RelTime } from '@/components/ui/RelTime'
import { ListPager } from '@/components/ui/ListPager'
import { ListSkeleton } from '@/components/ui/Skeleton'
import { artifactsApi, errorMessage, type GallerySection, type GallerySort } from '@/lib/api'
import { reusable } from '@/lib/reuse'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact } from '@/types'

/** The page's sections, in tab order. */
const SECTIONS: { id: GallerySection; label: string; icon: typeof Globe }[] = [
  { id: 'html', label: 'HTML', icon: Globe },
  { id: 'code', label: '코드 파일', icon: Code2 },
  { id: 'chart', label: '차트', icon: BarChart3 },
  { id: 'diagram', label: '도식', icon: Workflow },
]

const sectionOf = (artifact: Artifact) => SECTIONS.find((s) => s.id === artifact.section) ?? SECTIONS[1]

/** The sortable columns; the newest edit first by default, as in 파일함 › 작업 문서. */
const SORT_FIELDS: SortField<GallerySort>[] = [
  { id: 'title', label: '제목', first: 'asc' },
  { id: 'section', label: '종류', first: 'asc' },
  { id: 'version', label: '판 수', first: 'desc' },
  { id: 'updated', label: '마지막 수정', first: 'desc' },
]

/** The grid on a wide screen: pick, title, kind, versions, last edit, controls. */
const GRID = 'sm:grid sm:grid-cols-[1.75rem_minmax(0,1fr)_8.5rem_4rem_8rem_7.5rem] sm:items-center sm:gap-x-3'

/** The file a picture, a sound or a video is kept as (`/files/<id>/content`), if any. */
function fileOf(data: Record<string, unknown> | null): string | null {
  const src = typeof data?.src === 'string' ? data.src : ''
  return /\/files\/([0-9a-f]{32})\/content/.exec(src)?.[1] ?? null
}

/** A kind's detail beside its section: a code file's language. */
function detailOf(artifact: Artifact): string {
  if (artifact.kind !== 'code') return ''
  const language = artifact.language ?? ''
  return language && language !== 'text' ? language : ''
}

/**
 * 「아티팩트」: what conversations made to view or run — HTML pages, code files, charts and
 * diagrams. Reports and decks are in 파일함 › 작업 문서, pictures, sound and video in 만든 파일;
 * an old link to one of those (`?open=<id>`) goes there.
 */
export function ArtifactsPage() {
  const t = useT()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const asked = params.get('kind')
  const [section, setSectionState] = useState<GallerySection | null>(
    SECTIONS.some((s) => s.id === asked) ? (asked as GallerySection) : null,
  )
  const [query, setQuery] = useState('')
  const sort = useUrlSort(SORT_FIELDS, { field: 'updated', dir: 'desc' })
  const gallery = useGallery(section, query, sort.field, sort.dir)
  const pick = useBulkSelect(gallery.rows)
  const sessions = useStore((s) => s.sessions)
  const projects = useStore((s) => s.projects)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<Artifact | null>(null)
  const [error, setError] = useState<string | null>(null)

  const setSection = (next: GallerySection | null) => {
    setSectionState(next)
    pick.clear()
    setParams(
      (current) => {
        const out = new URLSearchParams(current)
        if (next) out.set('kind', next)
        else out.delete('kind')
        return out
      },
      { replace: true },
    )
  }

  // `?open=<id>`: a link from search or an older card. What is listed here opens here; a
  // document opens in its editor (or 작업 문서), a picture, a sound or a video in 만든 파일.
  const openId = params.get('open')
  useEffect(() => {
    if (!openId) return
    let live = true
    const clear = () =>
      setParams(
        (current) => {
          const out = new URLSearchParams(current)
          out.delete('open')
          return out
        },
        { replace: true },
      )
    void artifactsApi
      .get(openId)
      .then((row) => {
        if (!live) return
        if (row.section === 'document') {
          navigate(row.sessionId ? `/s/${row.sessionId}?artifact=${row.id}` : `/files?tab=working&doc=${row.id}`, { replace: true })
          return
        }
        if (row.section === 'media') {
          const file = fileOf(row.data)
          navigate(file ? `/files?tab=made&open=${file}` : '/files?tab=made', { replace: true })
          return
        }
        clear()
        setPreviewId(row.id)
      })
      .catch((err) => {
        if (!live) return
        clear()
        setError(errorMessage(err, t('아티팩트를 열지 못했습니다.')))
      })
    return () => {
      live = false
    }
  }, [openId, navigate, setParams, t])

  // the store's copy (an open panel) follows what changed here
  const forget = (ids: string[]) =>
    useStore.setState((s) => ({ artifacts: s.artifacts.filter((a) => !ids.includes(a.id)) }))

  const remove = async (ids: string[]) => {
    setError(null)
    try {
      if (ids.length === 1) await artifactsApi.remove(ids[0])
      else await artifactsApi.removeMany(ids)
      gallery.removeRows(ids)
      forget(ids)
    } catch (err) {
      setError(errorMessage(err, t('삭제하지 못했습니다.')))
      void gallery.load()
    }
  }

  const count = (id: GallerySection) => gallery.counts?.[id]
  const shown = gallery.rows
  const filtered = !!query.trim() || !!section

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('아티팩트')}</span>} />
      <PageBody>
        <PageHeader
          title={t('아티팩트')}
          description={t('대화에서 만든 HTML·코드·차트·도식입니다.')}
        />

        <div className="mb-3 max-w-sm">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t('아티팩트 검색')}
            placeholder={t('제목으로 찾기')}
          />
        </div>

        <Tabs<GallerySection | 'all'>
          value={section ?? 'all'}
          onChange={(next) => setSection(next === 'all' ? null : next)}
          tabs={[
            { id: 'all', label: t('전체'), count: gallery.all },
            ...SECTIONS.map((s) => ({ id: s.id, label: t(s.label), count: count(s.id) })),
          ]}
        />
        <PhoneSort className="mt-3" fields={SORT_FIELDS} sort={sort} onSort={sort.set} />

        {gallery.failed && <ReloadNotice onRetry={() => void gallery.load()} />}
        {error && <p role="alert" className="pt-3 text-base text-danger">{error}</p>}

        <div className="pt-4">
          {gallery.loading ? (
            <ListSkeleton />
          ) : gallery.failed ? null : shown.length === 0 ? (
            <EmptyState
              icon={<Layers size={18} />}
              title={filtered ? t('찾는 아티팩트가 없습니다') : t('아직 아티팩트가 없습니다')}
              description={filtered ? t('다른 제목이나 종류로 찾아보세요.') : t('대화에서 웹 페이지나 차트를 만들어 달라고 하면 여기에 모입니다.')}
              action={
                filtered ? undefined : (
                  <Button variant="primary" onClick={() => navigate('/')}>
                    {t('새 대화 시작')}
                  </Button>
                )
              }
            />
          ) : (
            <>
              <BulkBar
                count={pick.count}
                allPicked={pick.allPicked}
                onToggleAll={pick.toggleAll}
                onClear={pick.clear}
                title={t('아티팩트')}
                note={t('아티팩트를 만든 대화는 그대로 남습니다.')}
                onDelete={async () => {
                  await remove(pick.ids)
                  pick.clear()
                }}
              />
              <Card role="table" aria-label={t('아티팩트')} className="px-3 py-1">
                <div role="row" className={cn('hidden border-b border-line px-1 py-1.5 text-xs text-muted', GRID)}>
                  <div role="columnheader"><span className="sr-only">{t('선택')}</span></div>
                  {SORT_FIELDS.map((field) => (
                    <ColumnHeader key={field.id} field={field} sort={sort} onSort={sort.click} align={field.id === 'version' ? 'right' : 'left'} />
                  ))}
                  <div role="columnheader"><span className="sr-only">{t('동작')}</span></div>
                </div>
                {shown.map((a) => {
                  const kind = sectionOf(a)
                  const Icon = kind.icon
                  const detail = detailOf(a)
                  const label = (
                    <Badge>
                      {t(kind.label)}
                      {detail && <span className="ml-1 text-faint">{detail}</span>}
                    </Badge>
                  )
                  const session = a.sessionId ? sessions.find((s) => s.id === a.sessionId) : undefined
                  const project = a.projectId ? projects.find((p) => p.id === a.projectId) : undefined
                  return (
                    // A phone moves the controls under the title, so the title keeps the row's width.
                    <div
                      key={a.id}
                      role="row"
                      data-testid="artifact-row"
                      data-section={a.section}
                      className={cn('flex flex-wrap items-center gap-x-2 border-b border-line px-1 py-2 last:border-b-0 sm:flex-nowrap', GRID)}
                    >
                      <div role="cell" className="flex items-center">
                        <PickBox
                          checked={pick.picked.has(a.id)}
                          onChange={() => pick.toggle(a.id)}
                          label={t('{name} 선택').replace('{name}', a.title)}
                        />
                      </div>
                      <div role="cell" className="flex min-w-0 flex-1 items-center gap-2">
                        <Icon size={16} className="shrink-0 text-accent" aria-hidden />
                        <div className="min-w-0 flex-1">
                          <button
                            type="button"
                            aria-label={t('{name} 열기').replace('{name}', a.title)}
                            onClick={() => setPreviewId(a.id)}
                            className="block min-h-6 max-w-full truncate text-left text-base font-medium hover:underline max-sm:line-clamp-2 max-sm:whitespace-normal max-sm:break-all"
                            title={a.title}
                          >
                            {a.title || t('제목 없는 아티팩트')}
                          </button>
                          <p className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-faint">
                            {/* a wide screen has these in their own columns */}
                            <span className="sm:hidden">{label}</span>
                            <span className="sm:hidden">v{a.version}</span>
                            <span className="sm:hidden"><RelTime iso={a.updatedAt} /></span>
                            {project && (
                              <span className="truncate">{project.emoji} {project.name}</span>
                            )}
                            {session && <span className="truncate">{session.title || t('제목 없는 대화')}</span>}
                          </p>
                        </div>
                      </div>
                      <div role="cell" className="max-sm:hidden">{label}</div>
                      <div role="cell" className="text-right text-sm tabular-nums text-muted max-sm:hidden" title={t.n('버전 {n}개', a.version)}>
                        v{a.version}
                      </div>
                      <div role="cell" className="text-sm text-muted max-sm:hidden"><RelTime iso={a.updatedAt} /></div>
                      <div role="cell" className="flex shrink-0 items-center justify-end max-sm:-mt-1 max-sm:basis-full">
                        {a.sessionId && (
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('{name} 원래 대화로').replace('{name}', a.title)}
                            title={t('원래 대화로')}
                            onClick={() => navigate(`/s/${a.sessionId}?artifact=${a.id}`)}
                          >
                            <MessageSquare size={14} />
                          </Button>
                        )}
                        {reusable(a) && (
                          <Dropdown
                            align="right"
                            trigger={() => (
                              <Button variant="ghost" size="icon" aria-label={t('{name} 더보기').replace('{name}', a.title)} title={t('더보기')}>
                                <MoreHorizontal size={14} />
                              </Button>
                            )}
                          >
                            <ReuseItems artifact={a} />
                          </Dropdown>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 삭제').replace('{name}', a.title)}
                          title={t('이 아티팩트를 삭제합니다')}
                          onClick={() => setConfirming(a)}
                        >
                          <Trash2 size={14} />
                        </Button>
                      </div>
                    </div>
                  )
                })}
              </Card>
              <ListPager paging={gallery.paging} total={gallery.total} label={t('아티팩트 페이지')} />
            </>
          )}
        </div>
      </PageBody>

      <ConfirmDialog
        open={!!confirming}
        onClose={() => setConfirming(null)}
        onConfirm={() => confirming && void remove([confirming.id])}
        title={t('{name} 삭제').replace('{name}', confirming?.title ?? '')}
        description={t('되돌릴 수 없습니다. 버전 기록도 함께 사라집니다.')}
      />

      <ArtifactModal
        id={previewId}
        onClose={() => {
          setPreviewId(null)
          // an edit in the panel (a title, a new version) shows in the list
          void gallery.load()
        }}
      />
    </>
  )
}
