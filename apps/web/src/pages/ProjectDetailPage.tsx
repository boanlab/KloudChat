import { ArrowLeft, Brain, ExternalLink, FileText, Link2, MoreHorizontal, Pencil, Plus, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  Dropdown,
  EmptyState,
  Field,
  Input,
  MenuItem,
  MenuLabel,
  MenuSeparator,
  Modal,
  Tabs,
  Textarea,
  useMenuClose,
} from '@/components/ui'
import { downloadFile, errorMessage, templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { PROJECT_EMOJIS, kindMeta, kindOrder } from '@/lib/kinds'
import { formatTokens, relativeTime } from '@/lib/utils'
import {
  MemoryEditor,
  emptyMemory,
  memoryTypeTone,
} from '@/components/memory/MemoryEditor'
import { useFileDrop } from '@/lib/useFileDrop'
import { useStore } from '@/store/useStore'
import { startFailure } from '@/lib/failures'
import type { MemoryEntry } from '@/types'
import { useT } from '@/lib/useT'

type Tab = 'sessions' | 'knowledge' | 'skills' | 'memory'

/** Emoji picker grid; closes the menu on pick. */
function EmojiGrid({ onPick }: { onPick: (emoji: string) => void }) {
  const close = useMenuClose()
  return (
    <div className="grid grid-cols-4 gap-1 p-1">
      {PROJECT_EMOJIS.map((e) => (
        <button
          key={e}
          onClick={() => {
            onPick(e)
            close()
          }}
          className="grid size-9 place-items-center rounded-control text-lg transition-colors hover:bg-elevated"
        >
          {e}
        </button>
      ))}
    </div>
  )
}

/** Mirrors the server's project delete: knowledge files go, sessions are detached, artifacts and memories stay. */
const DELETE_SCOPE =
  '되돌릴 수 없습니다. 지침과 지식 파일이 사라집니다. 대화는 지워지지 않고 프로젝트 밖으로 나오며, 아티팩트와 메모리는 그대로 남습니다.'

export function ProjectDetailPage() {
  const t = useT()
  const { projectId } = useParams()
  const navigate = useNavigate()
  const {
    projects,
    sessions,
    skills,
    designs,
    designTemplates,
    enabledKinds,
    memories,
    updateProject,
    deleteProject,
    newSession,
    setNotice,
    loadWorkspace,
    uploadFile,
    addProjectUrl,
    deleteFile,
    moveSessionToProject,
    deleteMemory,
  } = useStore()
  const project = projects.find((p) => p.id === projectId)
  const [tab, setTab] = useState<Tab>('sessions')
  const [instructions, setInstructions] = useState(project?.instructions ?? '')
  const [dirty, setDirty] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [editing, setEditing] = useState<{
    name: string
    emoji: string
    description: string
  } | null>(null)
  const [memoryDraft, setMemoryDraft] = useState<MemoryEntry | null>(null)
  const [uploading, setUploading] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)
  const [urlOpen, setUrlOpen] = useState(false)
  const [urlDraft, setUrlDraft] = useState('')
  const [urlBusy, setUrlBusy] = useState(false)
  const [expandedFile, setExpandedFile] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  // Hook must sit above the early return; the uploader is defined after it.
  const addKnowledgeRef = useRef<(files: File[]) => void>(() => {})
  const knowledgeDrop = useFileDrop(
    (files) => addKnowledgeRef.current(files),
    tab === 'knowledge',
  )

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])

  // Fill the instructions box once the project arrives, unless already edited.
  useEffect(() => {
    if (project && !dirty) setInstructions(project.instructions)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.id, project?.instructions])

  if (!project) {
    return (
      <>
        <TopBar />
        <PageBody>
          <EmptyState icon={<FileText size={18} />} title={t('프로젝트를 찾을 수 없습니다')} />
        </PageBody>
      </>
    )
  }

  const projectSessions = sessions.filter((c) => c.projectId === project.id)
  const outsideSessions = [...sessions.filter((c) => c.projectId !== project.id)].sort(
    (a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt),
  )
  const projectSkills = skills.filter((s) => project.skillIds.includes(s.id))
  const validProjectSkillIds = project.skillIds.filter((id) =>
    skills.some((skill) => skill.id === id && skill.enabled),
  )
  const projectMemories = memories.filter((m) => m.scope === project.id)
  const selectedDesign = designs.find((d) => d.id === project.designSystemId)
  // Only document-shaped templates are offered as formats.
  const english = currentLang() === 'en'
  const formats = designTemplates.filter((row) => row.kind === 'deck' || row.kind === 'document')
  const formatSurfaces = kindOrder.filter((kind) => formats.some((row) => row.surface === kind))
  const totalTokens = project.files.reduce((sum, f) => sum + f.tokens, 0)

  const addKnowledgeFiles = async (picked: File[]) => {
    if (!picked.length) return
    setUploading(true)
    try {
      for (const file of picked) {
        await uploadFile(file, { projectId: project.id }).catch(() => null)
      }
    } finally {
      setUploading(false)
    }
  }
  addKnowledgeRef.current = addKnowledgeFiles

  const openFile = async (id: string, name: string) => {
    setFileError(null)
    try {
      await downloadFile(id, name)
    } catch (err) {
      setFileError(errorMessage(err, t('파일을 내려받지 못했습니다.')))
    }
  }

  const saveUrl = async () => {
    const url = urlDraft.trim()
    if (!/^https?:\/\//i.test(url)) return
    setUrlBusy(true)
    setFileError(null)
    try {
      await addProjectUrl(project.id, url)
      setUrlDraft('')
      setUrlOpen(false)
    } catch (err) {
      setFileError(errorMessage(err, t('웹페이지를 읽어 오지 못했습니다.')))
    } finally {
      setUrlBusy(false)
    }
  }

  return (
    <>
      <TopBar
        left={
          <button
            onClick={() => navigate('/projects')}
            className="flex items-center gap-1.5 text-base text-muted hover:text-fg"
          >
            <ArrowLeft size={14} />
            {t('프로젝트')}
          </button>
        }
      />
      <Modal
        open={urlOpen}
        onClose={() => !urlBusy && setUrlOpen(false)}
        title={t('웹 자료 추가')}
        description={t('페이지를 지금 읽어 프로젝트에 보관합니다. 이후 원문이 바뀌어도 작업에는 저장된 내용이 사용됩니다.')}
        footer={
          <>
            <Button onClick={() => setUrlOpen(false)} disabled={urlBusy}>{t('취소')}</Button>
            <Button variant="primary" onClick={() => void saveUrl()} disabled={urlBusy || !/^https?:\/\//i.test(urlDraft.trim())}>
              {urlBusy ? t('읽는 중…') : t('읽어서 보관')}
            </Button>
          </>
        }
      >
        <Field label={t('웹페이지 주소')}>
          <Input value={urlDraft} onChange={(event) => setUrlDraft(event.target.value)} placeholder="https://example.com/report" autoFocus />
        </Field>
      </Modal>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6 sm:py-8">
        <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <Dropdown
              trigger={() => (
                <button
                  aria-label={t('아이콘 바꾸기')}
                  title={t('아이콘 바꾸기')}
                  className="grid size-11 shrink-0 place-items-center rounded-card border border-line bg-elevated text-xl transition-colors hover:border-line-strong hover:bg-panel"
                >
                  {project.emoji}
                </button>
              )}
            >
              <EmojiGrid onPick={(emoji) => void updateProject(project.id, { emoji })} />
            </Dropdown>
            <div className="min-w-0 pt-1">
              <h1 className="truncate text-2xl font-semibold tracking-tight">{project.name}</h1>
              {project.description && (
                <p className="mt-1 text-base text-muted">{project.description}</p>
              )}
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            <Dropdown
              align="right"
              trigger={() => (
                <Button variant="primary" size="sm">
                  <Plus size={14} />
                  {t('이 프로젝트에서 새로 만들기')}
                </Button>
              )}
            >
              <MenuLabel>{t('무엇을 만들까요?')}</MenuLabel>
              {kindOrder
                .filter((k) => enabledKinds.includes(k))
                .map((k) => {
                  const meta = kindMeta[k]
                  const KindIcon = meta.icon
                  return (
                    <MenuItem
                      key={k}
                      icon={<KindIcon size={14} style={{ color: meta.color }} />}
                      onClick={() =>
                        void newSession(k, { projectId: project.id })
                          .then((id) => navigate(`/s/${id}`))
                          .catch((err: unknown) => setNotice(startFailure(err, t)))
                      }
                    >
                      {t(meta.label)}
                    </MenuItem>
                  )
                })}
            </Dropdown>
            <Dropdown
              align="right"
              trigger={() => (
                <Button variant="ghost" size="icon" aria-label={t('더 보기')}>
                  <MoreHorizontal size={16} />
                </Button>
              )}
            >
              <MenuItem
                icon={<Pencil size={14} />}
                onClick={() =>
                  setEditing({
                    name: project.name,
                    emoji: project.emoji,
                    description: project.description,
                  })
                }
              >
                {t('이름 · 설명')}
              </MenuItem>
              <MenuSeparator />
              <MenuItem danger icon={<Trash2 size={14} />} onClick={() => setConfirmDelete(true)}>
                {t('프로젝트 삭제')}
              </MenuItem>
            </Dropdown>
          </div>
        </div>

        <Modal
          open={!!editing}
          onClose={() => setEditing(null)}
          title={t('프로젝트 정보')}
          footer={
            <>
              <Button onClick={() => setEditing(null)}>{t('취소')}</Button>
              <Button
                variant="primary"
                disabled={!editing?.name.trim()}
                onClick={() => {
                  if (editing) updateProject(project.id, editing)
                  setEditing(null)
                }}
              >
                {t('저장')}
              </Button>
            </>
          }
        >
          {editing && (
            <>
              <Field label={t('이름')}>
                <Input
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                />
              </Field>
              <Field label={t('설명')}>
                <Input
                  value={editing.description}
                  onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                />
              </Field>
            </>
          )}
        </Modal>

        <div className="grid gap-6 lg:grid-cols-[1fr_320px] lg:items-start">
        <aside className="order-2 space-y-4 lg:sticky lg:top-6">
        <Card className="p-4">
          <div className="mb-2 flex items-center justify-between">
            <div>
              <p className="text-base font-medium">{t('프로젝트 지침')}</p>
              <p className="text-sm text-muted">
                {t('이 프로젝트의 모든 대화에 시스템 프롬프트로 함께 전달됩니다.')}
              </p>
            </div>
            {dirty && (
              <Button
                variant="primary"
                size="sm"
                onClick={() => {
                  updateProject(project.id, { instructions })
                  setDirty(false)
                }}
              >
                {t('저장')}
              </Button>
            )}
          </div>
          <Textarea
            rows={5}
            value={instructions}
            onChange={(e) => {
              setInstructions(e.target.value)
              setDirty(true)
            }}
          />
        </Card>

        <Card className="space-y-3 p-4">
          <div>
            <p className="text-base font-medium">{t('디자인')}</p>
            <p className="text-sm text-muted">
              {t('말투는 대화·보고서·슬라이드에, 색과 서체는 슬라이드와 보고서 표지에, 스타일은 이미지에 적용됩니다. 오디오·동영상에는 적용되지 않습니다.')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {selectedDesign && (
              <span
                aria-hidden
                className="size-5 shrink-0 rounded-control border border-line"
                style={{ background: selectedDesign.tokens.accent }}
              />
            )}
            <select
              aria-label={t('디자인')}
              value={project.designSystemId ?? ''}
              onChange={(e) =>
                void updateProject(project.id, { designSystemId: e.target.value || null })
              }
              className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
            >
              <option value="">{t('사용 안 함 — 기본 모양')}</option>
              {designs.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
          {selectedDesign?.description && (
            <p className="text-sm text-faint">{selectedDesign.description}</p>
          )}
        </Card>

        {formatSurfaces.length > 0 && (
          <Card className="space-y-3 p-4">
            <div>
              <p className="text-base font-medium">{t('기본 서식')}</p>
              <p className="text-sm text-muted">
                {t('이 프로젝트에서 새로 시작하는 작업이 어떤 모양으로 나올지 정합니다. 대화마다 다시 고를 수 있습니다.')}
              </p>
            </div>
            {formatSurfaces.map((kind) => (
              <div key={kind} className="flex items-center gap-2">
                <span className="w-16 shrink-0 text-base text-muted">
                  {t(kindMeta[kind].label)}
                </span>
                <select
                  aria-label={t('{kind} 서식').replace('{kind}', t(kindMeta[kind].label))}
                  value={project.renderTemplates[kind] ?? ''}
                  onChange={(e) =>
                    void updateProject(project.id, {
                      // Sent whole; an empty value removes the surface from the map.
                      renderTemplates: {
                        ...project.renderTemplates,
                        [kind]: e.target.value,
                      },
                    })
                  }
                  className="h-9 w-full rounded-control border border-line bg-panel px-3 text-base focus:border-accent focus:outline-none"
                >
                  <option value="">{t('사용 안 함 — 기본 모양')}</option>
                  {formats
                    .filter((row) => row.surface === kind)
                    .map((row) => (
                      <option key={row.id} value={row.id}>
                        {templateText(row, english).name}
                      </option>
                    ))}
                </select>
              </div>
            ))}
          </Card>
        )}
        </aside>

        <div className="order-1 min-w-0">
        <Tabs<Tab>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'sessions', label: t('작업'), count: projectSessions.length },
            { id: 'knowledge', label: t('지식'), count: project.files.length },
            { id: 'skills', label: t('스킬'), count: projectSkills.length },
            { id: 'memory', label: t('메모리'), count: projectMemories.length },
          ]}
        />

        <div className="pt-4">
          {tab === 'sessions' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-card border border-dashed border-line-strong px-4 py-3">
                <div>
                  <p className="text-base font-medium">{t('기존 대화 편입')}</p>
                  <p className="text-sm text-muted">
                    {t('다른 곳에서 시작한 대화를 이 프로젝트로 옮깁니다. 지침·지식·메모리를 그때부터 함께 받습니다.')}
                  </p>
                </div>
                <Dropdown
                  align="right"
                  className="max-h-80 min-w-72"
                  trigger={() => (
                    <Button size="sm" disabled={outsideSessions.length === 0}>
                      <Plus size={14} />
                      {t('대화 추가')}
                    </Button>
                  )}
                >
                  <MenuLabel>{t('어떤 대화를 옮길까요?')}</MenuLabel>
                  {outsideSessions.map((c) => {
                    const meta = kindMeta[c.kind]
                    const KindIcon = meta.icon
                    return (
                      <MenuItem
                        key={c.id}
                        icon={<KindIcon size={14} style={{ color: meta.color }} />}
                        hint={relativeTime(c.updatedAt)}
                        onClick={() => void moveSessionToProject(c.id, project.id)}
                      >
                        {c.title || t('제목 없음')}
                      </MenuItem>
                    )
                  })}
                </Dropdown>
              </div>
              {projectSessions.length === 0 ? (
                <div className="flex justify-center py-12">
                  <Button
                    variant="primary"
                    onClick={() =>
                      void newSession('chat', { projectId: project.id })
                        .then((id) => navigate(`/s/${id}`))
                        .catch((err: unknown) => setNotice(startFailure(err, t)))
                    }
                  >
                    <Plus size={16} />
                    {t('새 채팅 시작')}
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                {projectSessions.map((c) => {
                  const meta = kindMeta[c.kind]
                  const KindIcon = meta.icon
                  return (
                    <Card
                      key={c.id}
                      onClick={() => navigate(`/s/${c.id}`)}
                      className="cursor-pointer px-4 py-3 transition-colors hover:bg-elevated"
                    >
                      <div className="flex items-center gap-3">
                        <KindIcon size={15} className="shrink-0" style={{ color: meta.color }} />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-base font-medium">{c.title}</p>
                          <p className="mt-0.5 truncate text-sm text-muted">
                            {c.messages.at(-1)?.content.slice(0, 90) ?? t('내용 없음')}
                          </p>
                        </div>
                        <span className="shrink-0 text-xs text-faint">
                          {relativeTime(c.updatedAt)}
                        </span>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 프로젝트에서 빼기').replace('{name}', c.title)}
                          title={t('프로젝트에서 빼기')}
                          onClick={(e) => {
                            e.stopPropagation()
                            void moveSessionToProject(c.id, null)
                          }}
                        >
                          <X size={13} />
                        </Button>
                      </div>
                    </Card>
                  )
                })}
                </div>
              )}
            </div>
          )}

          {tab === 'knowledge' && (
            <div className="relative space-y-3" {...knowledgeDrop.handlers}>
              {knowledgeDrop.over && (
                <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-card border-2 border-dashed border-accent bg-accent-soft/90 text-base font-medium text-accent">
                  {t('여기에 놓으면 참고 파일로 추가됩니다')}
                </div>
              )}
              <div className="flex items-center justify-between rounded-card border border-dashed border-line-strong px-4 py-3">
                <div>
                  <p className="text-base font-medium">{t('참고 파일')}</p>
                  <p className="text-sm text-muted">
                    {t('총 {n} 토큰').replace('{n}', formatTokens(totalTokens))} · {t('컨텍스트의 약')}{' '}
                    {Math.round((totalTokens / 200_000) * 100)}%
                  </p>
                </div>
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  className="hidden"
                  aria-label={t('지식 파일 선택')}
                  onChange={(e) => {
                    const picked = Array.from(e.target.files ?? [])
                    e.target.value = ''
                    void addKnowledgeFiles(picked)
                  }}
                />
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => setUrlOpen(true)}>
                    <Link2 size={14} />
                    {t('웹 자료')}
                  </Button>
                  <Button size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>
                    <Upload size={14} className={uploading ? 'animate-pulse' : undefined} />
                    {uploading ? t('업로드 중') : t('파일 추가')}
                  </Button>
                </div>
              </div>
              {project.files.length === 0 ? (
                <EmptyState
                  icon={<FileText size={18} />}
                  title={t('참고 파일이 없습니다')}
                  description={t('PDF, 마크다운, CSV를 올려 두면 이 프로젝트의 모든 대화에서 참조합니다.')}
                />
              ) : (
                project.files.map((f) => (
                  <Card key={f.id} className="px-4 py-2.5" data-knowledge={f.id}>
                    <div className="flex items-center gap-3">
                    {f.sourceUrl ? <Link2 size={15} className="shrink-0 text-accent" /> : <FileText size={15} className="shrink-0 text-faint" />}
                    <span className="min-w-0 flex-1">
                      <button
                        onClick={() => f.sourceUrl ? window.open(f.sourceUrl, '_blank', 'noopener,noreferrer') : void openFile(f.id, f.name)}
                        title={t('원본 파일을 내려받습니다')}
                        className="block max-w-full truncate text-left text-base text-accent hover:underline"
                      >
                        {f.name}
                      </button>
                      <span className="block text-xs text-faint">
                        {f.sourceUrl ? t('웹페이지 스냅샷') : f.size} · {t('{n} 토큰').replace('{n}', formatTokens(f.tokens))} · {relativeTime(f.addedAt)}
                      </span>
                    </span>
                    {f.preview && (
                      <Button variant="ghost" size="sm" onClick={() => setExpandedFile((id) => id === f.id ? null : f.id)}>
                        {expandedFile === f.id ? t('미리보기 닫기') : t('읽은 내용 확인')}
                      </Button>
                    )}
                    {f.sourceUrl && <a href={f.sourceUrl} target="_blank" rel="noreferrer" aria-label={t('{name} 원문 열기').replace('{name}', f.name)} className="text-faint hover:text-accent"><ExternalLink size={14} /></a>}
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', f.name)}
                      onClick={() => void deleteFile(f.id)}
                    >
                      <Trash2 size={14} />
                    </Button>
                    </div>
                    {expandedFile === f.id && f.preview && (
                      <div className="mt-2 rounded-control border border-line bg-elevated px-3 py-2 text-sm leading-relaxed text-muted" data-testid="knowledge-preview">
                        {f.preview}
                      </div>
                    )}
                  </Card>
                ))
              )}
              {fileError && <p className="text-base text-danger">{fileError}</p>}
            </div>
          )}

          {tab === 'skills' && (
            <div className="space-y-2">
              {projectSkills.map((s) => (
                <Card key={s.id} className="flex items-start gap-3 px-4 py-3">
                  <Sparkles size={15} className="mt-0.5 shrink-0 text-accent" />
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-medium">{s.name}</p>
                    <p className="text-sm text-muted">{s.description}</p>
                  </div>
                  <Badge tone={s.enabled ? 'success' : 'neutral'}>
                    {s.enabled ? t('추천') : t('사용 중지')}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={t('{name} 추천 해제').replace('{name}', s.name)}
                    onClick={() =>
                      void updateProject(project.id, {
                        skillIds: validProjectSkillIds.filter((id) => id !== s.id),
                      })
                    }
                  >
                    <X size={13} />
                  </Button>
                </Card>
              ))}
              <Dropdown
                className="min-w-72"
                trigger={() => (
                  <Button size="sm">
                    <Plus size={14} />
                    {t('추천 스킬 설정')}
                  </Button>
                )}
              >
                <MenuLabel>{t('입력창에서 먼저 보여 줄 스킬')}</MenuLabel>
                {skills
                  .filter((skill) => skill.enabled)
                  .map((skill) => {
                    const selected = validProjectSkillIds.includes(skill.id)
                    return (
                      <MenuItem
                        key={skill.id}
                        checked={selected}
                        onClick={() =>
                          void updateProject(project.id, {
                            skillIds: selected
                              ? validProjectSkillIds.filter((id) => id !== skill.id)
                              : [...validProjectSkillIds, skill.id],
                          })
                        }
                      >
                        {skill.name}
                      </MenuItem>
                    )
                  })}
              </Dropdown>
            </div>
          )}

          {tab === 'memory' && (
            <div className="space-y-3">
              {/* Agents' `share_note` writes here too. */}
              <div className="flex items-center justify-between rounded-card border border-dashed border-line-strong px-4 py-3">
                <div>
                  <p className="text-base font-medium">{t('공유 메모리')}</p>
                  <p className="text-sm text-muted">
                    {t('이 프로젝트의 모든 대화와 에이전트가 다음 요청부터 이 내용을 함께 받습니다. 에이전트가 남긴 결론도 여기에 쌓입니다.')}
                  </p>
                </div>
                <Button size="sm" onClick={() => setMemoryDraft(emptyMemory(project.id))}>
                  <Plus size={14} />
                  {t('메모리 추가')}
                </Button>
              </div>
              {projectMemories.length === 0 ? (
                <EmptyState
                  icon={<Brain size={18} />}
                  title={t('이 프로젝트에 저장된 메모리가 없습니다')}
                  description={t('대화 중 확인된 사실이 여기에 쌓이고, 이후 대화에서 근거로 쓰입니다.')}
                />
              ) : (
                <div className="space-y-2">
                  {projectMemories.map((m) => (
                    <Card key={m.id} className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="min-w-0 truncate font-mono text-sm text-accent">
                          {m.name}
                        </span>
                        <Badge tone={memoryTypeTone[m.type]}>{m.type}</Badge>
                        <span className="ml-auto flex shrink-0 items-center gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('{name} 편집').replace('{name}', m.name)}
                            onClick={() => setMemoryDraft(m)}
                          >
                            <Pencil size={13} />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('{name} 삭제').replace('{name}', m.name)}
                            onClick={() => void deleteMemory(m.id)}
                          >
                            <Trash2 size={13} />
                          </Button>
                        </span>
                      </div>
                      <p className="mt-1 text-base text-muted">{m.description}</p>
                      {m.body && (
                        <p className="mt-1 line-clamp-3 text-sm whitespace-pre-wrap text-faint">
                          {m.body}
                        </p>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        </div>
        </div>
        </div>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        onConfirm={() => {
          void deleteProject(project.id)
          navigate('/projects')
        }}
        title={t('{name} 삭제').replace('{name}', project.name)}
        description={t(DELETE_SCOPE)}
      />

      <MemoryEditor
        draft={memoryDraft}
        onDraft={setMemoryDraft}
        onClose={() => setMemoryDraft(null)}
        lockScope
      />
    </>
  )
}
