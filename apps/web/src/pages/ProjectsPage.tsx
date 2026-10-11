import { Boxes, FileText, MessageSquare, Plus } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Button,
  Card,
  EmptyState,
  Field,
  Input,
  LoadingState,
  Modal,
  PageHeader,
  ReloadNotice,
  RequiredNote,
  Textarea,
} from '@/components/ui'
import { BulkBar, PickBox, useBulkSelect } from '@/components/ui/BulkSelect'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'
import { errorMessage } from '@/lib/api'
import { missingMessage } from '@/lib/formCheck'

export function ProjectsPage() {
  const t = useT()
  const navigate = useNavigate()
  const { projects, createProject, deleteMany, loadWorkspace, workspaceLoading, workspaceFailed } =
    useStore()
  const pick = useBulkSelect(projects)

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    instructions: '',
  })
  const lang = useStore((s) => s.lang)
  const nameRef = useRef<HTMLInputElement>(null)
  const [nameError, setNameError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const create = async () => {
    if (creating) return
    if (!draft.name.trim()) {
      setNameError(missingMessage(lang, t('이름')))
      nameRef.current?.focus()
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      const id = await createProject(draft)
      setOpen(false)
      setDraft({ name: '', description: '', instructions: '' })
      navigate(`/projects/${id}`)
    } catch (err) {
      setCreateError(errorMessage(err, t('프로젝트를 만들지 못했습니다. 잠시 후 다시 시도하세요.')))
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('프로젝트')}</span>} />
      <PageBody>
        <PageHeader
          title={t('프로젝트')}
          description={t('지침과 참고 파일을 묶어 대화 맥락을 고정합니다. 프로젝트 안의 모든 대화가 같은 지침을 공유합니다.')}
          action={
            <Button variant="primary" onClick={() => setOpen(true)}>
              <Plus size={16} />
          {t('새 프로젝트')}
            </Button>
          }
        />

        {workspaceFailed && <ReloadNotice onRetry={() => void loadWorkspace()} />}

        {workspaceLoading && projects.length === 0 ? (
          <LoadingState />
        ) : projects.length === 0 ? (
          <EmptyState
            icon={<Boxes size={18} />}
            title={t('아직 프로젝트가 없습니다')}
            description={t('반복되는 작업 맥락을 프로젝트로 묶어 두면 매번 설명하지 않아도 됩니다.')}
            action={
              <Button variant="primary" onClick={() => setOpen(true)}>
                <Plus size={16} />
          {t('새 프로젝트')}
              </Button>
            }
          />
        ) : (
          <>
          <BulkBar
            count={pick.count}
            allPicked={pick.allPicked}
            onToggleAll={pick.toggleAll}
            onClear={pick.clear}
            title={t('프로젝트')}
            note={t('붙여 둔 자료도 함께 지워집니다. 대화는 남고, 프로젝트에서만 빠집니다.')}
            onDelete={async () => {
              await deleteMany('projects', pick.ids)
              pick.clear()
            }}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {projects.map((p) => (
              <Card
                key={p.id}
                onClick={() => navigate(`/projects/${p.id}`)}
                className="cursor-pointer p-4 transition-colors hover:border-line-strong hover:bg-elevated"
              >
                <div className="flex items-start gap-3">
                  <PickBox
                    checked={pick.picked.has(p.id)}
                    onChange={() => pick.toggle(p.id)}
                    label={t('{name} 선택').replace('{name}', p.name)}
                    className="mt-2.5"
                  />
                  <span className="grid size-9 shrink-0 place-items-center rounded-card bg-elevated text-lg">
                    {p.emoji}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-base font-medium">{p.name}</p>
                    <p className="mt-0.5 line-clamp-2 text-base text-muted">{p.description}</p>
                    <div className="mt-3 flex items-center gap-3 text-xs text-faint">
                      <span className="flex items-center gap-1">
                        <MessageSquare size={11} />
                        {p.sessionIds.length}
                      </span>
                      <span className="flex items-center gap-1">
                        <FileText size={11} />
                        {p.files.length}
                      </span>
                      <span>{relativeTime(p.updatedAt)}</span>
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
          </>
        )}
      </PageBody>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('새 프로젝트')}
        description={t('이 프로젝트의 모든 대화에 적용될 지침을 정합니다.')}
        footer={
          <>
            <Button onClick={() => setOpen(false)}>{t('취소')}</Button>
            <Button variant="primary" aria-busy={creating || undefined} onClick={() => void create()}>
              {t('만들기')}
            </Button>
          </>
        }
      >
        <form
          noValidate
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault()
            void create()
          }}
        >
        <RequiredNote />
        <Field label={t('이름')} required error={nameError}>
          <Input
            ref={nameRef}
            value={draft.name}
            maxLength={NAME_LIMIT}
            onChange={(e) => {
              setNameError(null)
              setDraft((d) => ({ ...d, name: e.target.value }))
            }}
            placeholder={t('예: 제품 출시 준비')}
          />
        </Field>
        <Field label={t('설명')}>
          <Input
            value={draft.description}
            onChange={(e) => setDraft((d) => ({ ...d, description: e.target.value }))}
            placeholder={t('한 줄로 무엇에 대한 프로젝트인지')}
          />
        </Field>
        <Field
          label={t('프로젝트 지침')}
          hint={t('모델에게 매번 함께 전달됩니다. 말투, 지켜야 할 것, 기본 전제를 적어 두세요.')}
        >
          <Textarea
            rows={5}
            value={draft.instructions}
            onChange={(e) => setDraft((d) => ({ ...d, instructions: e.target.value }))}
            placeholder={t('예: 모든 코드는 PyTorch 2.x 기준으로 작성한다. 수식은 LaTeX로.')}
          />
        </Field>
        {createError && (
          <p role="alert" className="text-base text-danger">
            {createError}
          </p>
        )}
        </form>
      </Modal>
    </>
  )
}
