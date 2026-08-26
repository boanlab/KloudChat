import {
  Check,
  SlidersHorizontal,
  KeyRound,
  Loader2,
  RefreshCw,
  Search,
  Shield,
  Trash2,
  UserX,
  X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { PageBody } from '@/components/layout/AppShell'
import { TopBar } from '@/components/layout/TopBar'
import {
  Badge,
  Button,
  Card,
  Field,
  Input,
  Modal,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { adminApi, errorMessage } from '@/lib/api'
import { cn, formatDate, relativeTime } from '@/lib/utils'
import { ShowMore, usePaged } from '@/components/ui/ShowMore'
import { useStore } from '@/store/useStore'
import type { User, UserStatus } from '@/types'
import { useT } from '@/lib/useT'

const statusTone = {
  active: 'success',
  pending: 'warn',
  suspended: 'danger',
} as const

const statusLabel: Record<UserStatus, string> = {
  active: '활성',
  pending: '승인 대기',
  suspended: '정지',
}

/**
 * Preset allowances so an admin rarely has to type a number.
 * 1 credit = $0.00001, so the default is about $10/month of model usage.
 */
const PLANS = [
  { label: '기본', credits: 1_000_000 },
  { label: '연구', credits: 5_000_000 },
  { label: '대규모', credits: 10_000_000 },
]

function CreditBar({ user }: { user: User }) {
  const pct = user.monthlyCredits > 0 ? (user.creditsUsed / user.monthlyCredits) * 100 : 0
  return (
    <div className="w-36">
      <div className="h-1.5 overflow-hidden rounded-full bg-elevated">
        <div
          className={cn('h-full rounded-full', pct > 90 ? 'bg-danger' : 'bg-accent')}
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
      <span className="mt-1 block text-xs tabular-nums text-faint">
        {user.creditsUsed.toLocaleString()} / {user.monthlyCredits.toLocaleString()}
      </span>
    </div>
  )
}

export function AdminUsersPage() {
  const t = useT()
  const {
    user,
    users,
    usersLoading,
    loadUsers,
    approveUser,
    rejectUser,
    suspendUser,
    reinstateUser,
    rotateLitellmKey,
    removeUser,
    setUserModels,
    models,
    setUserCredits,
  } = useStore()
  const [filter, setFilter] = useState<UserStatus | 'all'>('all')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<User | null>(null)
  /** Confirmed in a dialog, never on the button: this one does not come back. */
  const [deleting, setDeleting] = useState<User | null>(null)
  /** Which account's model access is being edited. */
  const [restricting, setRestricting] = useState<User | null>(null)
  const [draftCredits, setDraftCredits] = useState('')
  /** Ids with an in-flight mutation, so a row's buttons cannot be double-fired. */
  const [busy, setBusy] = useState<string[]>([])
  /** Why the last action did not happen. Cleared when the next one starts. */
  const [error, setError] = useState<string | null>(null)
  /** Served by the API. The fallback only shows before the fetch lands — if it
   *  ever became the real answer the dialog would quote a rate nothing applies. */
  const [economics, setEconomics] = useState({ perUsd: 100_000, budgetHeadroom: 0.2 })

  useEffect(() => {
    void loadUsers()
    // A failure here costs a hint line, not the screen.
    adminApi
      .settings()
      .then((s) => s.credits && setEconomics(s.credits))
      .catch(() => {})
  }, [loadUsers])

  /** What lands on the proxy for a given allowance — mirrors `budget_usd` on the API. */
  const proxyBudget = (credits: number) =>
    `$${(Math.ceil((credits / economics.perUsd) * (1 + economics.budgetHeadroom) * 100) / 100).toFixed(2)}`

  /**
   * One row action, with its failure said out loud.
   *
   * Every button on this table goes through here — approve, suspend, rotate a
   * key, delete. Without the catch a refusal from the server (the proxy is
   * down, the account is gone) cleared the spinner and changed nothing, so the
   * administrator read a rotation that never happened as one that had.
   */
  const run = async (id: string, action: () => Promise<void>) => {
    if (busy.includes(id)) return
    setBusy((b) => [...b, id])
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(errorMessage(err, t('요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.')))
    } finally {
      setBusy((b) => b.filter((x) => x !== id))
    }
  }

  const matching = useMemo(() => {
    const q = query.trim().toLowerCase()
    return users
      .filter((u) => filter === 'all' || u.status === filter)
      .filter((u) => !q || u.name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q))
  }, [users, filter, query])
  const { visible, hidden, more } = usePaged(matching, [filter, query, users.length])

  const pendingCount = users.filter((u) => u.status === 'pending').length
  const totalGranted = users.reduce((s, u) => s + u.monthlyCredits, 0)
  const totalUsed = users.reduce((s, u) => s + u.creditsUsed, 0)
  const resetDate = users[0]?.cycleResetsAt

  return (
    <>
      <TopBar left={<span className="text-base font-medium">{t('사용자 · 크레딧')}</span>} />
      <PageBody>
        <PageHeader
          title={t('사용자 · 크레딧')}
          description={t('가입 승인과 월 크레딧 한도를 관리합니다. 한도는 매달 1일에 자동으로 리필되며, 남은 크레딧은 이월되지 않습니다.')}
        />

        {error && (
          <p
            role="alert"
            className="mb-4 rounded-card border border-danger/30 bg-danger/5 px-3 py-2 text-base text-danger"
          >
            {error}
          </p>
        )}

        <div className="mb-5 grid gap-3 sm:grid-cols-4">
          {[
            { label: t('전체 사용자'), value: String(users.length) },
            { label: t('승인 대기'), value: String(pendingCount) },
            {
              label: t('이번 달 사용 / 배정'),
              value: `${(totalUsed / 1_000_000).toFixed(1)}M / ${(totalGranted / 1_000_000).toFixed(1)}M`,
            },
            { label: t('다음 리필'), value: resetDate ? formatDate(resetDate) : '—' },
          ].map((s) => (
            <Card key={s.label} className="px-4 py-3">
              <p className="text-xs tracking-wide text-faint uppercase">{s.label}</p>
              <p className="mt-1 text-xl font-semibold tabular-nums">{s.value}</p>
            </Card>
          ))}
        </div>

        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <Tabs<UserStatus | 'all'>
            value={filter}
            onChange={setFilter}
            tabs={[
              { id: 'all', label: t('전체'), count: users.length },
              { id: 'pending', label: t('승인 대기'), count: pendingCount },
              { id: 'active', label: t('활성') },
              { id: 'suspended', label: t('정지') },
            ]}
          />
          <div className="relative">
            <Search size={14} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-faint" />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('이름 또는 이메일')}
              aria-label={t('사용자 검색')}
              className="h-8 w-56 pl-8 text-base"
            />
          </div>
        </div>

        <Card className="overflow-hidden">
          {/* Scrolls inside its own box rather than being clipped by it. The
              controls that approve, limit and suspend an account are in the last
              column, so a card that hides the overflow is a screen where they
              cannot be reached at all. Same shape as settings/access. */}
          <div className="overflow-x-auto">
            <table className="w-full text-base">
              <thead className="bg-elevated text-xs tracking-wide text-faint uppercase">
                <tr>
                  <th className="px-4 py-2.5 text-left font-semibold">{t('사용자')}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t('상태')}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t('이번 달 크레딧')}</th>
                  <th className="px-4 py-2.5 text-left font-semibold">{t('마지막 활동')}</th>
                  <th className="px-4 py-2.5 text-right font-semibold">{t('관리')}</th>
                </tr>
              </thead>
              <tbody>
                {visible.length === 0 && (
                  <tr className="border-t border-line">
                    <td colSpan={5} className="px-4 py-10 text-center text-base text-faint">
                      {usersLoading ? (
                        <Loader2 size={16} className="mx-auto animate-spin" />
                      ) : users.length === 0 ? (
                        t('사용자를 불러오지 못했습니다.')
                      ) : (
                        t('조건에 맞는 사용자가 없습니다.')
                      )}
                    </td>
                  </tr>
                )}
                {visible.map((u) => (
                  <tr key={u.id} className="border-t border-line">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2.5">
                        <span
                          className="grid size-7 shrink-0 place-items-center rounded-full text-xs font-semibold text-white"
                          style={{ background: u.avatarColor }}
                        >
                          {u.name[0]}
                        </span>
                        <div className="min-w-0">
                          <p className="flex items-center gap-1.5 font-medium">
                            {u.name}
                            {u.role === 'admin' && (
                              <Badge tone="accent">
                                <Shield size={10} />
                                {t('관리자')}
                              </Badge>
                            )}
                          </p>
                          <p className="truncate text-xs text-faint">{u.email}</p>
                          {/* Whether the proxy can tell this person's calls apart
                              from everyone else's. No key means their turns fall
                              back to the shared master key and land unattributed. */}
                          <p className="mt-0.5 flex items-center gap-1 text-2xs text-faint">
                            <KeyRound size={9} />
                            {u.litellmKeyPreview ? (
                              <span className="font-mono">{u.litellmKeyPreview}</span>
                            ) : (
                              <span className="text-warn">{t('전용 키 없음')}</span>
                            )}
                          </p>
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={statusTone[u.status]}>{t(statusLabel[u.status])}</Badge>
                    </td>
                    <td className="px-4 py-3">
                      <CreditBar user={u} />
                    </td>
                    <td className="px-4 py-3 text-xs text-muted">
                      {relativeTime(u.lastActiveAt)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1.5">
                        {u.status === 'pending' && (
                          <Button
                            size="sm"
                            variant="primary"
                            disabled={busy.includes(u.id)}
                            onClick={() => void run(u.id, () => approveUser(u.id, PLANS[0].credits))}
                          >
                            {busy.includes(u.id) ? (
                              <Loader2 size={13} className="animate-spin" />
                            ) : (
                              <Check size={13} />
                            )}
                            {t('승인')}
                          </Button>
                        )}
                        {u.status === 'pending' && (
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={busy.includes(u.id)}
                            onClick={() => void run(u.id, () => rejectUser(u.id))}
                          >
                            <X size={13} />
                            {t('반려')}
                          </Button>
                        )}
                        <Button
                          size="sm"
                          onClick={() => {
                            setEditing(u)
                            setDraftCredits(String(u.monthlyCredits))
                          }}
                        >
                          {t('크레딧')}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('{name} 모델 제한').replace('{name}', u.name)}
                          title={t('이 계정이 쓸 수 있는 모델을 제한합니다')}
                          disabled={busy.includes(u.id)}
                          onClick={() => setRestricting(u)}
                        >
                          <SlidersHorizontal size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={t('계정 삭제')}
                          title={t('계정과 그 계정이 만든 모든 것을 지웁니다. 되돌릴 수 없습니다.')}
                          disabled={busy.includes(u.id) || u.id === user?.id}
                          onClick={() => setDeleting(u)}
                        >
                          <Trash2 size={14} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={u.litellmKeyPreview ? t('LiteLLM 키 재발급') : t('LiteLLM 키 발급')}
                          title={
                            u.litellmKeyPreview
                              ? t('이 사용자의 LiteLLM 키를 새로 발급하고 기존 키를 폐기합니다')
                              : t('이 사용자의 전용 LiteLLM 키를 발급합니다')
                          }
                          disabled={busy.includes(u.id)}
                          onClick={() => void run(u.id, () => rotateLitellmKey(u.id))}
                        >
                          <KeyRound size={14} />
                        </Button>
                        {u.status !== 'suspended' ? (
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('정지')}
                            title={t('이 계정의 접속을 막습니다')}
                            disabled={busy.includes(u.id)}
                            onClick={() => void run(u.id, () => suspendUser(u.id))}
                          >
                            <UserX size={14} />
                          </Button>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('정지 해제')}
                            title={t('다시 접속할 수 있게 합니다')}
                            disabled={busy.includes(u.id)}
                            onClick={() => void run(u.id, () => reinstateUser(u.id))}
                          >
                            <RefreshCw size={14} />
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
        <ShowMore hidden={hidden} onMore={more} />
      </PageBody>

      <Modal
        open={!!restricting}
        onClose={() => setRestricting(null)}
        title={t('쓸 수 있는 모델')}
        description={
          restricting
            ? t('{name} 이(가) 호출할 수 있는 모델입니다. 아무것도 고르지 않으면 전체를 씁니다. 이 계정이 발급한 API 키에도 같은 제한이 걸립니다.').replace('{name}', restricting.name)
            : undefined
        }
        width="max-w-2xl"
        footer={<Button onClick={() => setRestricting(null)}>{t('닫기')}</Button>}
      >
        {restricting && (
          <div className="flex flex-wrap gap-1.5">
            {models.map((m) => {
              const on = restricting.allowedModels.includes(m.id)
              return (
                <button
                  key={m.id}
                  onClick={() => {
                    const next = on
                      ? restricting.allowedModels.filter((x) => x !== m.id)
                      : [...restricting.allowedModels, m.id]
                    setRestricting({ ...restricting, allowedModels: next })
                    void setUserModels(restricting.id, next)
                  }}
                  className={cn(
                    'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                    on
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  {m.label}
                </button>
              )
            })}
          </div>
        )}
      </Modal>

      <Modal
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={t('계정을 삭제할까요?')}
        description={
          deleting
            ? t('{name} ({email}) 의 대화·프로젝트·아티팩트·메모리·크레딧 기록이 모두 사라지고 LiteLLM 키도 폐기됩니다. 되돌릴 수 없습니다.').replace('{name}', deleting.name).replace('{email}', deleting.email)
            : undefined
        }
        footer={
          <>
            <Button onClick={() => setDeleting(null)}>{t('취소')}</Button>
            <Button
              variant="danger"
              onClick={() => {
                const target = deleting
                setDeleting(null)
                if (target) void run(target.id, () => removeUser(target.id))
              }}
            >
              {t('삭제')}
            </Button>
          </>
        }
      >
        <p className="text-base text-muted">
          {t('잠시 막아 두려는 것이라면 정지를 쓰세요. 정지는 되돌릴 수 있고, 기록도 남습니다.')}
        </p>
      </Modal>

      <Modal
        open={!!editing}
        onClose={() => setEditing(null)}
        title={t('월 크레딧 한도')}
        description={
          editing
            ? `${editing.name} · ${t('매달 1일에 이 값으로 리필됩니다. 남은 크레딧은 이월되지 않습니다.')}`
            : undefined
        }
        footer={
          <>
            <Button onClick={() => setEditing(null)}>{t('취소')}</Button>
            <Button
              variant="primary"
              onClick={() => {
                if (editing)
                  void run(editing.id, () =>
                    setUserCredits(editing.id, Number(draftCredits) || 0),
                  )
                setEditing(null)
              }}
            >
              {t('저장')}
            </Button>
          </>
        }
      >
        <Field label={t('프리셋')}>
          <div className="flex flex-wrap gap-1.5">
            {PLANS.map((p) => (
              <button
                key={p.label}
                onClick={() => setDraftCredits(String(p.credits))}
                className={cn(
                  'rounded-control border px-2.5 py-1.5 text-base transition-colors',
                  Number(draftCredits) === p.credits
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:bg-elevated',
                )}
              >
                {t(p.label)}
                <span className="ml-1.5 text-xs text-faint">
                  ${(p.credits / economics.perUsd).toFixed(0)}
                </span>
              </button>
            ))}
          </div>
        </Field>
        <Field label={t('월 크레딧')} hint={t('이번 달 사용량은 그대로 유지됩니다.')}>
          <Input
            type="number"
            value={draftCredits}
            onChange={(e) => setDraftCredits(e.target.value)}
          />
        </Field>
        {editing && (
          <div className="space-y-1.5 rounded-control border border-line bg-elevated px-3 py-2.5 text-base text-muted">
            <p>
              {t('현재 주기 사용량 {n} 크레딧').replace('{n}', editing.creditsUsed.toLocaleString())} ·{' '}
              {t('{date} 리필').replace('{date}', formatDate(editing.cycleResetsAt))}
            </p>
            {/* The limit people hit is this one — KloudChat checks it before every
                turn. The proxy gets a copy so a bug here cannot spend without
                bound, and it sits above the real limit so it never fires first. */}
            <p className="text-xs text-faint">
              {t('LiteLLM 에도 {limit} 한도로 반영됩니다 (여유분 {pct}%).')
                .replace('{limit}', proxyBudget(Number(draftCredits) || 0))
                .replace('{pct}', String(Math.round(economics.budgetHeadroom * 100)))}
            </p>
          </div>
        )}
      </Modal>
    </>
  )
}
