import { Search, ShieldCheck, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import { formatDateTime } from '@/lib/utils'
import { Badge, Button, Card, Input, PageHeader, Switch, Tabs } from '@/components/ui'
import { useStore } from '@/store/useStore'
import { useT } from '@/lib/useT'

/**
 * The audit trail, as written.
 *
 * Everything shown here is backed by an endpoint. On a security screen a toggle
 * that persists nothing and enforces nothing is worse than an absent feature,
 * so nothing appears until there is something behind it.
 */
const SEVERITY: Record<string, 'neutral' | 'warn' | 'danger'> = {
  info: 'neutral',
  warn: 'warn',
  alert: 'danger',
}

/** Written by the auth and admin routes. Unlisted actions show their raw name. */
const ACTION_LABEL: Record<string, string> = {
  login: '로그인',
  signup: '회원가입',
  logout: '로그아웃',
  'user.approve': '가입 승인',
  'user.reject': '가입 반려',
  'user.suspend': '계정 정지',
  'user.reinstate': '정지 해제',
  'user.role': '권한 변경',
  'user.litellm_key': 'LiteLLM 키 재발급',
  'credits.set': '크레딧 한도 변경',
  'settings.update': '시스템 설정 변경',
  'token.reuse': '토큰 재사용 감지',
}

export function AdminGovernancePage() {
  const t = useT()
  const { audit, loadAudit, governance, loadGovernance, setGovernance, models } = useStore()
  const [query, setQuery] = useState('')
  const [tab, setTab] = useState<'policy' | 'log'>('policy')
  const [category, setCategory] = useState('')
  const [swept, setSwept] = useState<number | null>(null)

  useEffect(() => {
    void loadAudit()
    void loadGovernance()
  }, [loadAudit, loadGovernance])

  const apply = async (patch: Partial<NonNullable<typeof governance>>) => {
    const cleared = await setGovernance(patch)
    // Shortening retention reaches back through what is already stored, so say
    // how much it took rather than leaving it to be discovered.
    if (cleared > 0) setSwept(cleared)
  }

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return audit ?? []
    return (audit ?? []).filter((e) =>
      [e.actor, e.action, ACTION_LABEL[e.action] ?? '', e.target, e.detail, e.ip]
        .join(' ')
        .toLowerCase()
        .includes(q),
    )
  }, [audit, query])
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
          description={t('누가 언제 무엇을 했는지 남은 기록입니다. 로그인, 승인, 정지, 권한 변경, 설정 변경이 남습니다.')}
        />

        <Tabs<'policy' | 'log'>
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'policy', label: t('정책') },
            { id: 'log', label: t('감사 로그'), count: audit?.length },
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
                        {t('채팅과 모델 비교의 전체 전송 맥락을 검사하고, 개인정보가 있으면 모델 전환·마스킹·편집 중 하나를 선택하게 합니다.')}
                      </p>
                    </div>
                    <Switch
                      checked={governance.externalDataGuard}
                      onChange={(value) => void apply({ externalDataGuard: value })}
                      label={t('외부 모델 개인정보 보호')}
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
          <>
            <div className="mb-3 mt-4 flex items-center gap-2">
          <div className="relative max-w-sm flex-1">
            <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('계정 · 행위 · IP 검색')}
              className="pl-8"
            />
          </div>
          {audit && (
            <span className="text-sm text-faint">
              {t('{shown} / {total}건').replace('{shown}', rows.length.toLocaleString()).replace('{total}', audit.length.toLocaleString())}
            </span>
          )}
        </div>

        {!audit ? (
          <Card className="p-10 text-center text-base text-muted">{t('기록을 불러오는 중입니다…')}</Card>
        ) : rows.length === 0 ? (
          <Card className="p-10 text-center text-base text-muted">
            {query ? t('검색 결과가 없습니다') : t('아직 기록된 사건이 없습니다')}
          </Card>
        ) : (
          <Card className="overflow-hidden">
            <table className="w-full text-base">
              <thead className="border-b border-line text-left text-xs tracking-wide text-faint uppercase">
                <tr>
                  <th className="px-4 py-2.5 font-medium">{t('시각')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('계정')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('행위')}</th>
                  <th className="px-4 py-2.5 font-medium">{t('대상')}</th>
                  <th className="px-4 py-2.5 font-medium">IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => (
                  <tr key={e.id} className="hover:bg-elevated">
                    <td className="px-4 py-2.5 whitespace-nowrap tabular-nums text-muted">
                      {formatDateTime(e.at)}
                    </td>
                    <td className="max-w-[220px] truncate px-4 py-2.5">{t(e.actor)}</td>
                    <td className="px-4 py-2.5">
                      <Badge tone={SEVERITY[e.severity] ?? 'neutral'}>
                        {t(ACTION_LABEL[e.action] ?? e.action)}
                      </Badge>
                      {e.detail && <span className="ml-2 text-xs text-faint">{e.detail}</span>}
                    </td>
                    <td className="max-w-[240px] truncate px-4 py-2.5 text-muted">{e.target}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-faint">{e.ip}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}

        </>
        )}

        <p className="mt-3 flex items-center gap-1.5 text-sm text-faint">
          <ShieldCheck size={13} />
          {t('정책은 서버에서 강제되고, 기록은 서버가 작성합니다. 이 화면에서 기록을 고치거나 지울 수 없습니다.')}
        </p>
      </PageBody>
    </>
  )
}
