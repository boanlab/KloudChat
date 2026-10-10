import { Globe, Layers, Lock, Pencil, Plus, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { PageBody } from '@/components/layout/AppShell'
import { BackLink, HubTopBar } from '@/components/customize/DetailLayout'
import { StepList } from '@/components/agents/PipelineEditor'
import { Badge, Button, Card, ConfirmDialog, EmptyState, Field, Input, LoadingState, Modal, Textarea } from '@/components/ui'
import { bundlesApi, errorMessage } from '@/lib/api'
import { bareSteps, reloadProcessCatalogue } from '@/lib/process'
import { REVIEW_LABEL, REVIEW_TONE } from '@/lib/userPipelines'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'
import type { Bundle, PipelineStep } from '@/types'

type Lists = { shipped: Bundle[]; mine: Bundle[]; shared: Bundle[] }

/** A bundle's items as one line: 근거 확인 · 상투어 걷어내기 · 내보내기(PDF). */
function itemsLine(b: Bundle, t: (s: string) => string) {
  return b.items
    .map((i) => {
      const label = i.kind === 'agent' && i.ref === '@self' ? t('이 에이전트') : t(i.label || i.ref || i.kind)
      return i.kind === 'export' && i.settings.formats?.length ? `${label}(${i.settings.formats.join(', ').toUpperCase()})` : label
    })
    .join(' · ')
}

/** The bundle editor: a name, and its items picked from the same steps 직접 구성 offers. */
function BundleEditor({
  bundle,
  onClose,
  onSaved,
}: {
  bundle: Bundle | null
  onClose: () => void
  onSaved: () => void
}) {
  const t = useT()
  const [name, setName] = useState(bundle?.name ?? '')
  const [description, setDescription] = useState(bundle?.description ?? '')
  const [steps, setSteps] = useState<PipelineStep[]>(() => bareSteps(bundle?.stages ?? []))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    setError(null)
    try {
      const payload = { name: name.trim(), description, stages: steps }
      if (bundle?.id) await bundlesApi.update(bundle.id, payload)
      else await bundlesApi.create(payload)
      reloadProcessCatalogue()
      onSaved()
    } catch (err) {
      setError(errorMessage(err, t('묶음을 저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={bundle ? t('묶음 고치기') : t('새 묶음')}
      width="max-w-2xl"
      footer={
        <>
          <Button onClick={onClose}>{t('취소')}</Button>
          <Button variant="primary" disabled={busy || !name.trim() || steps.length === 0} onClick={() => void save()}>
            {t('저장')}
          </Button>
        </>
      }
    >
      <div className="space-y-4" data-testid="bundle-editor">
        <Field label={t('묶음 이름')}>
          <Input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder={t('예: 두 번 검토하고 PDF로')} />
        </Field>
        <Field label={t('설명')}>
          <Textarea rows={2} maxLength={400} value={description} onChange={(e) => setDescription(e.target.value)} />
        </Field>
        <div className="space-y-1.5">
          <p className="text-base font-medium">{t('할 일')}</p>
          <p className="text-sm text-faint">
            {t('질문·계산·검사는 에이전트가 쓰는 과정 안에서, 검토·내보내기·다른 에이전트는 그 뒤에 합니다. 「이 에이전트」를 넣으면 그 앞뒤 순서를 정할 수 있습니다.')}
          </p>
          <StepList steps={steps} onChange={setSteps} selfName={t('이 에이전트')} />
        </div>
        {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      </div>
    </Modal>
  )
}

/**
 * /customize/bundles — 묶음 관리: the shipped bundles, mine and the ones others shared. A bundle
 * shows as one more checkbox in every agent form's 「추가로 할 일」. Sharing works as a skill's
 * does; one that exports, reviews or runs another agent waits for an administrator.
 */
export function BundlesPage() {
  const t = useT()
  const [params, setParams] = useSearchParams()
  const setNotice = useStore((s) => s.setNotice)
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const [lists, setLists] = useState<Lists | null>(null)
  const [editing, setEditing] = useState<Bundle | null | 'new'>(params.get('new') ? 'new' : null)
  const [removing, setRemoving] = useState<Bundle | null>(null)
  const reload = useCallback(() => bundlesApi.list().then(setLists).catch(() => setLists({ shipped: [], mine: [], shared: [] })), [])
  useEffect(() => {
    void loadWorkspace()
    void reload()
  }, [loadWorkspace, reload])

  const share = async (b: Bundle, visibility: 'org' | 'private') => {
    try {
      const out = await bundlesApi.share(b.id!, visibility)
      setNotice(
        visibility === 'private'
          ? t('나만 쓰도록 바꿨습니다.')
          : out.review === 'pending'
            ? t('관리자에게 공유 승인을 요청했습니다.')
            : t('모두에게 공개했습니다.'),
      )
      reloadProcessCatalogue()
      void reload()
    } catch (err) {
      setNotice(errorMessage(err, t('공유 설정을 바꾸지 못했습니다.')))
    }
  }

  const card = (b: Bundle, own: boolean) => (
    <Card key={b.ref} className="space-y-2 p-4" data-testid="bundle-card">
      <div className="flex items-start justify-between gap-2">
        <p className="min-w-0 text-base font-semibold">{t(b.name)}</p>
        {b.shipped ? (
          <Badge>{t('기본 제공')}</Badge>
        ) : own ? (
          <Badge tone={b.visibility === 'org' ? 'success' : REVIEW_TONE[b.review]}>
            {b.visibility === 'org' ? t('모두에게 공개') : b.review ? t(REVIEW_LABEL[b.review]) : t('나만 쓰기')}
          </Badge>
        ) : null}
      </div>
      {b.description && <p className="text-sm text-muted">{t(b.description)}</p>}
      <p className="text-sm text-faint">{itemsLine(b, t)}</p>
      {b.reviewNote && b.review === 'rejected' && <p className="text-sm text-danger">{t('반려 사유')}: {b.reviewNote}</p>}
      {own && (
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => setEditing(b)}>
            <Pencil size={13} />
            {t('고치기')}
          </Button>
          {b.visibility === 'org' || b.review === 'pending' ? (
            <Button size="sm" onClick={() => void share(b, 'private')}>
              <Lock size={13} />
              {t('나만 쓰기')}
            </Button>
          ) : (
            <Button size="sm" onClick={() => void share(b, 'org')}>
              <Globe size={13} />
              {t('모두에게 공개')}
            </Button>
          )}
          <Button size="sm" variant="danger" onClick={() => setRemoving(b)}>
            <Trash2 size={13} />
            <span className="max-sm:sr-only">{t('삭제')}</span>
          </Button>
        </div>
      )}
    </Card>
  )

  return (
    <>
      <HubTopBar tab="agents" tabLabel="에이전트" title={t('묶음')} />
      <PageBody>
        <div className="mx-auto max-w-4xl space-y-6">
          <BackLink tab="agents" label="에이전트" />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">{t('묶음')}</h1>
              <p className="text-base text-muted">
                {t('에이전트 설정의 「추가로 할 일」에서 체크하는 할 일 묶음입니다. 내가 만든 묶음은 내 모든 에이전트에 나옵니다.')}
              </p>
            </div>
            <Button variant="primary" onClick={() => setEditing('new')}>
              <Plus size={15} />
              {t('묶음 만들기')}
            </Button>
          </div>
          {lists === null ? (
            <LoadingState />
          ) : (
            <>
              <section className="space-y-3" aria-label={t('기본 제공 묶음')}>
                <h2 className="text-md font-semibold">{t('기본 제공 묶음')}</h2>
                <div className="grid gap-3 lg:grid-cols-3">{lists.shipped.map((b) => card(b, false))}</div>
              </section>
              <section className="space-y-3" aria-label={t('내 묶음')}>
                <h2 className="text-md font-semibold">{t('내 묶음')}</h2>
                {lists.mine.length === 0 ? (
                  <EmptyState
                    icon={<Layers size={18} />}
                    title={t('아직 내 묶음이 없습니다')}
                    description={t('직접 구성한 순서를 「이 순서를 묶음으로 저장」하거나, 여기서 만들어 보세요.')}
                  />
                ) : (
                  <div className="grid gap-3 lg:grid-cols-2">{lists.mine.map((b) => card(b, true))}</div>
                )}
              </section>
              {lists.shared.length > 0 && (
                <section className="space-y-3" aria-label={t('공유된 묶음')}>
                  <h2 className="text-md font-semibold">{t('공유된 묶음')}</h2>
                  <div className="grid gap-3 lg:grid-cols-2">{lists.shared.map((b) => card(b, false))}</div>
                </section>
              )}
            </>
          )}
        </div>
      </PageBody>
      {editing && (
        <BundleEditor
          bundle={editing === 'new' ? null : editing}
          onClose={() => {
            setEditing(null)
            if (params.get('new')) setParams({}, { replace: true })
          }}
          onSaved={() => {
            setEditing(null)
            if (params.get('new')) setParams({}, { replace: true })
            void reload()
          }}
        />
      )}
      <ConfirmDialog
        open={!!removing}
        onClose={() => setRemoving(null)}
        onConfirm={async () => {
          if (!removing?.id) return
          try {
            await bundlesApi.remove(removing.id)
            reloadProcessCatalogue()
            void reload()
          } catch (err) {
            setNotice(errorMessage(err, t('삭제하지 못했습니다.')))
          }
          setRemoving(null)
        }}
        title={t('{name} 삭제').replace('{name}', removing?.name ?? '')}
        description={t('이 묶음을 체크해 둔 에이전트는 다음에 저장할 때 이 묶음 없이 저장됩니다.')}
      />
    </>
  )
}
