import { ArrowLeft, Brain, FileText, Pencil, Plus, Sparkles, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  Dropdown,
  EmptyState,
  Field,
  Input,
  MenuItem,
  MenuLabel,
  Modal,
  PageHeader,
  Tabs,
  Textarea,
} from '@/components/ui'
import { downloadFile, errorMessage, templateText } from '@/lib/api'
import { currentLang } from '@/lib/i18n'
import { PROJECT_EMOJIS, kindMeta, kindOrder } from '@/lib/kinds'
import { cn, formatTokens, relativeTime } from '@/lib/utils'
import {
  MemoryEditor,
  emptyMemory,
  memoryTypeTone,
} from '@/components/memory/MemoryEditor'
import { useFileDrop } from '@/lib/useFileDrop'
import { useStore } from '@/store/useStore'
import type { MemoryEntry } from '@/types'
import { useT } from '@/lib/useT'

type Tab = 'sessions' | 'knowledge' | 'skills' | 'memory'

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
    memories,
    updateProject,
    deleteProject,
    newSession,
    loadWorkspace,
    uploadFile,
    deleteFile,
    moveSessionToProject,
    deleteMemory,
  } = useStore()
  const project = projects.find((p) => p.id === projectId)
  const [tab, setTab] = useState<Tab>('sessions')
  const [instructions, setInstructions] = useState(project?.instructions ?? '')
  const [dirty, setDirty] = useState(false)
  const [editing, setEditing] = useState<{
    name: string
    emoji: string
    description: string
  } | null>(null)
  const [memoryDraft, setMemoryDraft] = useState<MemoryEntry | null>(null)
  const [uploading, setUploading] = useState(false)
  const [fileError, setFileError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  /**
   * The drop target for 지식.
   *
   * Declared up here because the page returns early when the project has not
   * arrived yet, and the uploader it calls is defined past that return — it
   * needs the project's id. The ref is what lets the hook sit above the branch
   * without moving a function that cannot move.
   */
  const addKnowledgeRef = useRef<(files: File[]) => void>(() => {})
  const knowledgeDrop = useFileDrop(
    (files) => addKnowledgeRef.current(files),
    tab === 'knowledge',
  )

  // Reached directly by URL as often as by click, so it loads its own data.
  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])

  // The instructions box is uncontrolled until the project arrives; without this
  // a page opened by URL shows an empty textarea over saved instructions.
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
  // What can still be brought in. Newest first, because the conversation
  // somebody wants to file is almost always the one they just had.
  const outsideSessions = [...sessions.filter((c) => c.projectId !== project.id)].sort(
    (a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt),
  )
  const projectSkills = skills.filter((s) => project.skillIds.includes(s.id))
  const validProjectSkillIds = project.skillIds.filter((id) =>
    skills.some((skill) => skill.id === id && skill.enabled),
  )
  const projectMemories = memories.filter((m) => m.scope === project.id)
  // A design system the account lost access to leaves the select on its first
  // entry; the project keeps the id until somebody changes it.
  const selectedDesign = designs.find((d) => d.id === project.designSystemId)
  // A format is a document shape, so only the two catalogue kinds that
  // produce one are offered here — an image template shapes a prompt, and a
  // picker over an empty list is a promise the catalogue cannot keep.
  const english = currentLang() === 'en'
  const formats = designTemplates.filter((row) => row.kind === 'deck' || row.kind === 'document')
  const formatSurfaces = kindOrder.filter((kind) => formats.some((row) => row.surface === kind))
  const totalTokens = project.files.reduce((sum, f) => sum + f.tokens, 0)

  /** The picker and a drop take the same path; only the source differs. */
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
        right={
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
            {kindOrder.map((k) => {
              const meta = kindMeta[k]
              const KindIcon = meta.icon
              return (
                <MenuItem
                  key={k}
                  icon={<KindIcon size={14} style={{ color: meta.color }} />}
                  onClick={() =>
                    void newSession(k, { projectId: project.id }).then((id) =>
                      navigate(`/s/${id}`),
                    )
                  }
                >
                  {t(meta.label)}
                </MenuItem>
              )
            })}
          </Dropdown>
        }
      />
      <PageBody>
        <PageHeader
          title={`${project.emoji} ${project.name}`}
          description={project.description}
          action={
            <div className="flex gap-2">
              {/* Name, icon and description were fixed at creation: the detail
                  page could edit the instructions and nothing else, so a typo in
                  a project's name was permanent. */}
              <Button
                size="sm"
                onClick={() =>
                  setEditing({
                    name: project.name,
                    emoji: project.emoji,
                    description: project.description,
                  })
                }
              >
                <Pencil size={14} />
                {t('이름 · 설명')}
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  deleteProject(project.id)
                  navigate('/projects')
                }}
              >
                <Trash2 size={14} />
                {t('프로젝트 삭제')}
              </Button>
            </div>
          }
        />

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
              <Field label={t('아이콘')}>
                <div className="flex flex-wrap gap-1.5">
                  {PROJECT_EMOJIS.map((e) => (
                    <button
                      key={e}
                      onClick={() => setEditing({ ...editing, emoji: e })}
                      className={cn(
                        'grid size-9 place-items-center rounded-control border text-lg transition-colors',
                        editing.emoji === e
                          ? 'border-accent bg-accent-soft'
                          : 'border-line hover:bg-elevated',
                      )}
                    >
                      {e}
                    </button>
                  ))}
                </div>
              </Field>
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

        <Card className="mb-6 p-4">
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
            {/* Named, because the alternative is a picker whose effect nobody
                can predict: this changes four surfaces and leaves two alone.
                The voice is listed first and 대화 with it, because the voice
                is the half that reaches the chat — a model writing a sentence
                cannot act on a hex code, but it can be told how to sound, and
                a person who reads only "슬라이드 색과 서체" is surprised when
                this afternoon's design edit changes how the chat answers. */}
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
              {/* The pair to the design above, and the distinction is the whole
                  reason there are two cards: that one is the look, this one is
                  the shape the look is poured into. */}
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
                      // Sent whole, because the server stores it whole: an
                      // empty value is this surface leaving the map.
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
              {/* Filing work that already exists. Until now a project could
                  only be filled by starting inside it, so anything begun the
                  ordinary way was stranded outside — and using a project meant
                  redoing the work, which is why nobody did. */}
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
                <EmptyState icon={<Plus size={18} />} title={t('아직 작업이 없습니다')} />
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
                        {/* Out again, from the row it is on. A conversation
                            filed into the wrong project was otherwise stuck
                            there. */}
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
                <Button size="sm" disabled={uploading} onClick={() => fileInput.current?.click()}>
                  <Upload size={14} className={uploading ? 'animate-pulse' : undefined} />
                  {uploading ? t('업로드 중') : t('파일 추가')}
                </Button>
              </div>
              {project.files.length === 0 ? (
                <EmptyState
                  icon={<FileText size={18} />}
                  title={t('참고 파일이 없습니다')}
                  description={t('PDF, 마크다운, CSV를 올려 두면 이 프로젝트의 모든 대화에서 참조합니다.')}
                />
              ) : (
                project.files.map((f) => (
                  <Card key={f.id} className="flex items-center gap-3 px-4 py-2.5">
                    <FileText size={15} className="shrink-0 text-faint" />
                    <span className="min-w-0 flex-1">
                      {/* The name is the button that opens it. Until it was,
                          the only way to see what a file held was to delete it
                          and upload it again. */}
                      <button
                        onClick={() => void openFile(f.id, f.name)}
                        title={t('원본 파일을 내려받습니다')}
                        className="block max-w-full truncate text-left text-base text-accent hover:underline"
                      >
                        {f.name}
                      </button>
                      <span className="block text-xs text-faint">
                        {f.size} · {t('{n} 토큰').replace('{n}', formatTokens(f.tokens))} · {relativeTime(f.addedAt)}
                      </span>
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('{name} 삭제').replace('{name}', f.name)}
                      onClick={() => void deleteFile(f.id)}
                    >
                      <Trash2 size={14} />
                    </Button>
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
                        hint={selected ? '✓' : undefined}
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
              {/* The tab used to be a read-only list, which made a project a
                  place facts landed in and could not be corrected. It is also
                  where an agent's `share_note` writes, so this is the screen
                  where one agent's finding becomes something a person can
                  check, fix, or hand on deliberately. */}
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
      </PageBody>

      <MemoryEditor
        draft={memoryDraft}
        onDraft={setMemoryDraft}
        onClose={() => setMemoryDraft(null)}
        // The project is on the page; asking which one is a way to get it wrong.
        lockScope
      />
    </>
  )
}
