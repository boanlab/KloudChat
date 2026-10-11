import { ShieldCheck, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { Badge, Button, Card, Input, PageHeader, Switch, Tabs } from '@/components/ui'
import { AuditLog } from '@/pages/AuditLog'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

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
                          '첨부 파일·프로젝트 파일·메모리가 들어간 턴은 선택한 모델 대신 같은 모델의 strict-local 판으로 답하고, 그 세션은 이후에도 외부 모델로 승격되지 않습니다. 쌍이 없으면 strict-local 안전 모델이 맡고, 그것도 없으면 전송을 거절합니다.',
                        )}
                      </p>
                    </div>
                    <Switch
                      checked={governance.internalDataStrictLocal}
                      onChange={(value) => void apply({ internalDataStrictLocal: value })}
                      label={t('내부 자료는 strict-local로만')}
                    />
                  </div>

                  <div className="border-t border-line pt-3">
                    <p className="text-base font-medium">{t('strict-local 안전 모델')}</p>
                    <p className="text-sm text-muted">
                      {t('프록시가 self-hosted이며 외부 fallback이 없다고 명시한 모델만 선택할 수 있습니다. 현재 모델 카탈로그의 위→아래 순서로 우선 사용합니다.')}
                    </p>
                    <div className="mt-2 space-y-1.5">
                      {strictModels.map((model) => {
                        const checked = governance.privacySafeModelIds.includes(model.id)
                        return (
                          <label
                            key={model.id}
                            className="flex items-center gap-2 rounded-control border border-line px-3 py-2 text-base"
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const selected = new Set(governance.privacySafeModelIds)
                                if (checked) selected.delete(model.id)
                                else selected.add(model.id)
                                void apply({
                                  privacySafeModelIds: strictModels
                                    .filter((candidate) => selected.has(candidate.id))
                                    .map((candidate) => candidate.id),
                                })
                              }}
                            />
                            <span className="min-w-0 flex-1 truncate">{model.label}</span>
                            {model.privacyOnly && <Badge tone="accent">{t('개인정보 전용')}</Badge>}
                          </label>
                        )
                      })}
                      {strictModels.length === 0 && (
                        <p className="rounded-control border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-warn">
                          {t('현재 프록시가 strict-local로 선언한 모델이 없습니다. 사용자는 마스킹 또는 편집만 선택할 수 있습니다.')}
                        </p>
                      )}
                    </div>
                  </div>

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
