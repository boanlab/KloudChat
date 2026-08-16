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
import { PROJECT_EMOJIS, kindMeta, kindOrder } from '@/lib/kinds'
import { cn, formatTokens, relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
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
    memories,
    updateProject,
    deleteProject,
    newSession,
    loadWorkspace,
    uploadFile,
    deleteFile,
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
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

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
  const projectSkills = skills.filter((s) => project.skillIds.includes(s.id))
  const validProjectSkillIds = project.skillIds.filter((id) =>
    skills.some((skill) => skill.id === id && skill.enabled),
  )
  const projectMemories = memories.filter((m) => m.scope === project.id)
  const totalTokens = project.files.reduce((sum, f) => sum + f.tokens, 0)

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
                        'grid size-9 place-items-center rounded-lg border text-lg transition-colors',
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
          {tab === 'sessions' &&
            (projectSessions.length === 0 ? (
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
                      </div>
                    </Card>
                  )
                })}
              </div>
            ))}

          {tab === 'knowledge' && (
            <div className="space-y-3">
              <div className="flex items-center justify-between rounded-xl border border-dashed border-line-strong px-4 py-3">
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
                  onChange={async (e) => {
                    const picked = Array.from(e.target.files ?? [])
                    e.target.value = ''
                    if (!picked.length) return
                    setUploading(true)
                    try {
                      for (const file of picked) {
                        await uploadFile(file, { projectId: project.id }).catch(() => null)
                      }
                    } finally {
                      setUploading(false)
                    }
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
                      <span className="block truncate text-base">{f.name}</span>
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

          {tab === 'memory' &&
            (projectMemories.length === 0 ? (
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
                      <span className="font-mono text-sm text-accent">{m.name}</span>
                      <Badge>{m.type}</Badge>
                    </div>
                    <p className="mt-1 text-base text-muted">{m.description}</p>
                  </Card>
                ))}
              </div>
            ))}
        </div>
      </PageBody>
    </>
  )
}
