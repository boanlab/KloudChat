import { Boxes, FileText, MessageSquare, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'
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
  Textarea,
} from '@/components/ui'
import { relativeTime } from '@/lib/utils'
import { PROJECT_EMOJIS } from '@/lib/kinds'
import { useStore } from '@/store/useStore'
import { NAME_LIMIT } from '@/lib/limits'
import { useT } from '@/lib/useT'



export function ProjectsPage() {
  const t = useT()
  const navigate = useNavigate()
  const { projects, createProject, loadWorkspace, workspaceLoading, workspaceFailed } = useStore()

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState({
    name: '',
    description: '',
    emoji: '🧪',
    instructions: '',
  })

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
          <div className="grid gap-3 sm:grid-cols-2">
            {projects.map((p) => (
              <Card
                key={p.id}
                onClick={() => navigate(`/projects/${p.id}`)}
                className="cursor-pointer p-4 transition-colors hover:border-line-strong hover:bg-elevated"
              >
                <div className="flex items-start gap-3">
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
            <Button
              variant="primary"
              disabled={!draft.name.trim()}
              onClick={() => {
                // The id comes from the server now, so the navigation has to
                // wait for it — otherwise the route gets a pending Promise.
                void createProject(draft).then((id) => {
                  setOpen(false)
                  setDraft({ name: '', description: '', emoji: '🧪', instructions: '' })
                  navigate(`/projects/${id}`)
                })
              }}
            >
              {t('만들기')}
            </Button>
          </>
        }
      >
        <Field label={t('아이콘')}>
          <div className="flex flex-wrap gap-1.5">
            {PROJECT_EMOJIS.map((e) => (
              <button
                key={e}
                onClick={() => setDraft((d) => ({ ...d, emoji: e }))}
                className={`grid size-9 place-items-center rounded-control border text-lg transition-colors ${
                  draft.emoji === e ? 'border-accent bg-accent-soft' : 'border-line hover:bg-elevated'
                }`}
              >
                {e}
              </button>
            ))}
          </div>
        </Field>
        <Field label={t('이름')}>
          <Input
            value={draft.name}
            maxLength={NAME_LIMIT}
            onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
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
      </Modal>
    </>
  )
}
