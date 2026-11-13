import { ArrowDown, ArrowUp, GripVertical, ShieldCheck, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Button, Card, Input, PageHeader, Switch, Tabs } from '@/components/ui'
import { AuditLog } from '@/pages/AuditLog'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import type { ModelInfo } from '@/types'

/**
 * strict-local safe models as a priority list: checked models in the order used, moved with
 * the arrow buttons or by dragging; unchecked ones below. Ids the admin's own catalogue does
 * not show (another account's models) keep their place at the end.
 */
function SafeModelPriority({
  models,
  selected,
  onChange,
}: {
  models: ModelInfo[]
  selected: string[]
  onChange: (ids: string[]) => void
}) {
  const t = useT()
  const byId = new Map(models.map((m) => [m.id, m]))
  const ordered = selected.filter((id) => byId.has(id))
  const hidden = selected.filter((id) => !byId.has(id))
  const rest = models.filter((m) => !selected.includes(m.id))
  const [dragged, setDragged] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const [said, setSaid] = useState('')
  const listRef = useRef<HTMLOListElement>(null)

  const save = (ids: string[]) => onChange([...ids, ...hidden])
  const move = (id: string, to: number, focus = false) => {
    const from = ordered.indexOf(id)
    if (from < 0 || to < 0 || to >= ordered.length || from === to) return
    const next = [...ordered]
    next.splice(from, 1)
    next.splice(to, 0, id)
    save(next)
    setSaid(
      t('{name}: {n}번째로 옮겼습니다')
        .replace('{name}', byId.get(id)?.label ?? id)
        .replace('{n}', String(to + 1)),
    )
    // The button pressed may have moved away under the pointer; keep the keyboard on the row.
    if (focus) {
      window.requestAnimationFrame(() =>
        listRef.current
          ?.querySelector<HTMLButtonElement>(`[data-model="${CSS.escape(id)}"] [data-move="${to > from ? 'down' : 'up'}"]:not(:disabled)`)
          ?.focus(),
      )
    }
  }

  return (
    <div className="border-t border-line pt-3">
      <p className="text-base font-medium">{t('strict-local 안전 모델')}</p>
      <p className="text-sm text-muted">
        {t('내부 자료 때문에 strict-local로 옮길 때는 사용자가 고른 모델의 strict-local 판을 먼저 쓰고, 그것이 없으면 이 목록의 첫 번째 모델을 씁니다. 개인정보 확인 창에서는 체크된 모델 중 하나를 사용자가 고를 수 있으며, 처음에는 첫 번째 모델이 골라져 있습니다.')}
      </p>
      <p className="mt-0.5 text-xs text-faint">
        {t('프록시가 self-hosted이며 외부 대체 모델이 없다고 명시한 모델만 선택할 수 있습니다. 화살표나 끌어서 순서를 바꿉니다.')}
      </p>
      <p className="sr-only" role="status" aria-live="polite">
        {said}
      </p>
      {ordered.length > 0 && (
        <ol ref={listRef} className="mt-2 space-y-1.5" aria-label={t('우선순위')} data-testid="safe-model-order">
          {ordered.map((id, i) => {
            const model = byId.get(id)!
            return (
              <li
                key={id}
                data-model={id}
                draggable
                onDragStart={(e) => {
                  setDragged(id)
                  e.dataTransfer.effectAllowed = 'move'
                  e.dataTransfer.setData('text/plain', id)
                }}
                onDragOver={(e) => {
                  if (!dragged) return
                  e.preventDefault()
                  setOver(id)
                }}
                onDragLeave={() => setOver((o) => (o === id ? null : o))}
                onDrop={(e) => {
                  e.preventDefault()
                  if (dragged) move(dragged, i)
                  setDragged(null)
                  setOver(null)
                }}
                onDragEnd={() => {
                  setDragged(null)
                  setOver(null)
                }}
                className={cn(
                  'flex items-center gap-2 rounded-control border border-line bg-panel px-2 py-1.5 text-base',
                  dragged === id && 'opacity-50',
                  over === id && dragged !== id && 'border-accent',
                )}
              >
                <GripVertical size={14} className="shrink-0 cursor-grab text-faint" aria-hidden />
                <span className="grid size-5 shrink-0 place-items-center rounded-full bg-elevated text-xs font-semibold tabular-nums">
                  {i + 1}
                </span>
                <label className="flex min-w-0 flex-1 items-center gap-2">
                  <input type="checkbox" checked onChange={() => save(ordered.filter((x) => x !== id))} />
                  <span className="min-w-0 flex-1 truncate">{model.label}</span>
                </label>
                {model.privacyOnly && <Badge tone="accent">{t('개인정보 전용')}</Badge>}
                <Button
                  variant="ghost"
                  size="icon"
                  data-move="up"
                  disabled={i === 0}
                  aria-label={t('{name} 위로').replace('{name}', model.label)}
                  title={i === 0 ? t('이미 첫 번째입니다') : t('위로')}
                  onClick={() => move(id, i - 1, true)}
                >
                  <ArrowUp size={14} />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  data-move="down"
                  disabled={i === ordered.length - 1}
                  aria-label={t('{name} 아래로').replace('{name}', model.label)}
                  title={i === ordered.length - 1 ? t('이미 마지막입니다') : t('아래로')}
                  onClick={() => move(id, i + 1, true)}
                >
                  <ArrowDown size={14} />
                </Button>
              </li>
            )
          })}
        </ol>
      )}
      <div className="mt-1.5 space-y-1.5">
        {rest.map((model) => (
          <label
            key={model.id}
            className="flex items-center gap-2 rounded-control border border-dashed border-line px-3 py-2 text-base text-muted"
          >
            <input type="checkbox" checked={false} onChange={() => save([...ordered, model.id])} />
            <span className="min-w-0 flex-1 truncate">{model.label}</span>
            {model.privacyOnly && <Badge tone="accent">{t('개인정보 전용')}</Badge>}
          </label>
        ))}
        {models.length === 0 && (
          <p className="rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-warn">
            {t('현재 프록시가 strict-local로 선언한 모델이 없습니다. 사용자는 마스킹 또는 편집만 선택할 수 있습니다.')}
          </p>
        )}
      </div>
    </div>
  )
}

export function AdminGovernancePage() {
  const t = useT()
  const { governance, loadGovernance, setGovernance, models } = useStore()
  const [auditTotal, setAuditTotal] = useState<number>()
  const [tab, setTab] = useState<'policy' | 'log'>('policy')
  const [category, setCategory] = useState('')
  const [swept, setSwept] = useState<number | null>(null)

  useEffect(() => {
    void loadGovernance()
  }, [loadGovernance])

  const apply = async (patch: Partial<NonNullable<typeof governance>>) => {
    const cleared = await setGovernance(patch)
    // Shortening retention deletes stored bodies; report the count.
    if (cleared > 0) setSwept(cleared)
  }

  const strictModels = models.filter(
    (model) =>
      model.dataBoundary === 'self_hosted' && model.strictLocal && model.kinds.includes('chat'),
  )

  return (
    <>
      <TopBar left={t('보안 · 감사')} />
      <PageBody>
        <PageHeader
          title={t('보안 · 감사')}
          description={t('정책: 개인정보 마스킹과 내부 자료 처리처럼 대화가 지켜야 할 규칙을 정합니다. 감사 로그: 누가 언제 무엇을 했는지 남은 기록입니다.')}
        />

        <Tabs<'policy' | 'log'>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'policy', label: t('정책') },
            { id: 'log', label: t('감사 로그'), count: auditTotal },
          ]}
        />

        {tab === 'policy' ? (
          <div className="space-y-3 pt-4">
            {!governance ? (
              <Card className="p-10 text-center text-base text-muted">{t('불러오는 중입니다…')}</Card>
            ) : (
              <>
                <Card className="flex items-start gap-3 p-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-base font-medium">{t('개인정보 마스킹')}</p>
                    <p className="text-base text-muted">
                      {t('주민등록번호·카드번호·전화번호·이메일을 모델로 보내기 전에 가립니다. 가려진 상태로 저장되므로 원문은 서버에도 남지 않습니다.')}
                    </p>
                  </div>
                  <Switch
                    checked={governance.piiMasking}
                    onChange={(v) =>
                      void apply({
                        piiMasking: v,
                        ...(v ? { allowUserRawExternal: false } : {}),
                      })
                    }
                    label={t('개인정보 마스킹')}
                  />
                </Card>

                <Card className="space-y-4 p-4">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-base font-medium">{t('외부 모델 개인정보 보호')}</p>
                      <p className="text-base text-muted">
                        {t('대화의 전체 전송 맥락을 검사하고, 개인정보가 있으면 모델 전환·마스킹·편집 중 하나를 선택하게 합니다.')}
                      </p>
                    </div>
                    <Switch
                      checked={governance.externalDataGuard}
                      onChange={(value) => void apply({ externalDataGuard: value })}
                      label={t('외부 모델 개인정보 보호')}
                    />
                  </div>

                  <div className="flex items-start gap-3 border-t border-line pt-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-base font-medium">{t('내부 자료는 strict-local로만')}</p>
                      <p className="text-base text-muted">
                        {t(
                          '첨부 파일·프로젝트 파일·메모리가 들어간 턴은 선택한 모델 대신 같은 모델의 strict-local 판으로 답하고, 그 세션은 이후에도 외부 모델로 승격되지 않습니다. 쌍이 없으면 아래 안전 모델 목록의 첫 번째 모델이 맡고, 목록이 비어 있으면 전송을 거절합니다.',
                        )}
                      </p>
                    </div>
                    <Switch
                      checked={governance.internalDataStrictLocal}
                      onChange={(value) => void apply({ internalDataStrictLocal: value })}
                      label={t('내부 자료는 strict-local로만')}
                    />
                  </div>

                  <SafeModelPriority
                    models={strictModels}
                    selected={governance.privacySafeModelIds}
                    onChange={(ids) => void apply({ privacySafeModelIds: ids })}
                  />

                  <div className="flex items-start gap-3 border-t border-line pt-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-base font-medium">{t('원문 외부 전송 허용')}</p>
                      <p className="text-sm text-muted">
                        {t('사용자가 경고 후 원문 전송을 선택할 수 있게 합니다. 개인정보 마스킹 정책이 켜져 있으면 항상 금지됩니다.')}
                      </p>
                    </div>
                    <Switch
                      checked={governance.allowUserRawExternal && !governance.piiMasking}
                      disabled={governance.piiMasking}
                      onChange={(value) => void apply({ allowUserRawExternal: value })}
                      label={t('원문 외부 전송 허용')}
                    />
                  </div>
                </Card>

                <Card className="p-4">
                  <div className="flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-base font-medium">{t('의도 기반 필터')}</p>
                      <p className="text-base text-muted">
                        {t('아래 범주에 해당하는 요청을 모델에 보내기 전에 거절합니다. 거절된 요청은 크레딧을 쓰지 않고 감사 로그에 남습니다.')}
                      </p>
                    </div>
                    <Switch
                      checked={governance.intentFilter}
                      onChange={(v) => void apply({ intentFilter: v })}
                      label={t('의도 기반 필터')}
                    />
                  </div>
                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {governance.blockedCategories.map((c) => (
                      <Badge key={c}>
                        {c}
                        <button
                          aria-label={t('{name} 제거').replace('{name}', c)}
                          className="ml-0.5 text-faint hover:text-fg"
                          onClick={() =>
                            void apply({
                              blockedCategories: governance.blockedCategories.filter((x) => x !== c),
                            })
                          }
                        >
                          <X size={11} />
                        </button>
                      </Badge>
                    ))}
                    {governance.blockedCategories.length === 0 && (
                      <span className="text-sm text-faint">{t('범주가 없어 아무것도 걸리지 않습니다')}</span>
                    )}
                  </div>
                  <div className="mt-2 flex gap-2">
                    <Input
                      value={category}
                      onChange={(e) => setCategory(e.target.value)}
                      placeholder={t('예: 시험 부정행위')}
                      aria-label={t('차단할 주제')}
                      className="max-w-xs"
                      onKeyDown={(e) => {
                        if (e.key !== 'Enter' || !category.trim()) return
                        void apply({
                          blockedCategories: [...governance.blockedCategories, category.trim()],
                        })
                        setCategory('')
                      }}
                    />
                    <Button
                      disabled={!category.trim()}
                      title={!category.trim() ? t('추가할 주제를 입력하세요') : undefined}
                      onClick={() => {
                        void apply({
                          blockedCategories: [...governance.blockedCategories, category.trim()],
                        })
                        setCategory('')
                      }}
                    >
                      {t('추가')}
                    </Button>
                  </div>
                </Card>

                <Card className="p-4">
                  <p className="text-base font-medium">{t('유휴 자동 로그아웃')}</p>
                  <p className="text-base text-muted">
                    {t('지정한 시간 동안 아무 조작이 없으면 브라우저가 스스로 로그아웃합니다. 실습실·도서관처럼 여러 사람이 쓰는 PC에서 이전 사용자의 대화가 그대로 남는 것을 막습니다. 0 은 사용하지 않음.')}
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <Input
                      type="number"
                      min={0}
                      max={1440}
                      defaultValue={governance.idleTimeoutMinutes}
                      aria-label={t('유휴 시간(분)')}
                      className="max-w-28"
                      onBlur={(e) => {
                        const mins = Math.min(1440, Math.max(0, Number(e.target.value) || 0))
                        if (mins !== governance.idleTimeoutMinutes) {
                          void apply({ idleTimeoutMinutes: mins })
                        }
                      }}
                    />
                    <span className="text-base text-muted">{t('분')}</span>
                  </div>
                </Card>

                <Card className="p-4">
                  <p className="text-base font-medium">{t('대화 보존 기간')}</p>
                  <p className="text-base text-muted">
                    {t('지정한 일수가 지난 대화 본문을 지웁니다. 모델·토큰·크레딧 기록과 감사 로그는 남습니다 — 무엇이 있었는지가 아니라 내용만 지우는 것입니다. 0 은 계속 보관.')}
                  </p>
                  <div className="mt-2 flex items-center gap-2">
                    <Input
                      type="number"
                      min={0}
                      defaultValue={governance.retentionDays}
                      aria-label={t('보관 기간(일)')}
                      className="max-w-28"
                      onBlur={(e) => {
                        const days = Math.max(0, Number(e.target.value) || 0)
                        if (days !== governance.retentionDays) void apply({ retentionDays: days })
                      }}
                    />
                    <span className="text-base text-muted">{t('일')}</span>
                  </div>
                  {swept !== null && (
                    <p className="mt-2 text-base text-warn">
                      {t('기간이 지난 본문 {n}건을 지웠습니다.').replace('{n}', swept.toLocaleString())}
                    </p>
                  )}
                </Card>
              </>
            )}
          </div>
        ) : (
          <AuditLog onTotal={setAuditTotal} />
        )}

        <p className="mt-3 flex items-center gap-1.5 text-sm text-faint">
          <ShieldCheck size={13} />
          {t('정책은 서버에서 강제되고, 기록은 서버가 작성합니다. 이 화면에서 기록을 고치거나 지울 수 없습니다.')}
        </p>
      </PageBody>
    </>
  )
}
