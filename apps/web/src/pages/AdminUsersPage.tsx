import {
  Check,
  SlidersHorizontal,
  KeyRound,
  UserPen,
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
  ConfirmDialog,
  Field,
  Input,
  Modal,
  PageHeader,
  Tabs,
} from '@/components/ui'
import { adminApi, errorMessage, type AdminKeys } from '@/lib/api'
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

/** Preset allowances; 1 credit = $0.00001. */
const PLANS = [
  { label: '기본', credits: 500_000 },
  { label: '연구', credits: 2_000_000 },
  { label: '대규모', credits: 5_000_000 },
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
    updateUser,
    resetUserPassword,
    replaceLitellmKey,
  } = useStore()
  const [filter, setFilter] = useState<UserStatus | 'all'>('all')
  const [query, setQuery] = useState('')
  const [editing, setEditing] = useState<User | null>(null)
  // 정보 수정: name, address and a password reset, one dialog.
  const [profile, setProfile] = useState<User | null>(null)
  const [draftName, setDraftName] = useState('')
  const [draftEmail, setDraftEmail] = useState('')
  const [draftPassword, setDraftPassword] = useState('')
  const [profileNote, setProfileNote] = useState<string | null>(null)
  const [profileError, setProfileError] = useState<string | null>(null)
  // 키 관리: KloudChat's own key (reissue or replace) and the keys the person issued (revoke).
  const [keysFor, setKeysFor] = useState<User | null>(null)
  const [keys, setKeys] = useState<AdminKeys | null>(null)
  const [keyDraft, setKeyDraft] = useState('')
  const [keyNote, setKeyNote] = useState<string | null>(null)
  const [keyError, setKeyError] = useState<string | null>(null)
  const [confirmRotate, setConfirmRotate] = useState(false)
  const [revokingKey, setRevokingKey] = useState<AdminKeys['named'][number] | null>(null)
  const [rotated, setRotated] = useState<{ id: string; preview: string | null } | null>(null)
  const loadKeys = async (id: string) => {
    try {
      setKeys(await adminApi.userKeys(id))
    } catch (err) {
      setKeyError(errorMessage(err, t('키 목록을 불러오지 못했습니다.')))
    }
  }
  const openKeys = (u: User) => {
    setKeysFor(u)
    setKeys(null)
    setKeyDraft('')
    setKeyNote(null)
    setKeyError(null)
    setConfirmRotate(false)
    void loadKeys(u.id)
  }
  // The whole catalogue for the restriction picker; the store's list is the caller's own.
  const [catalogue, setCatalogue] = useState<{ id: string; label: string }[] | null>(null)
  const [deleting, setDeleting] = useState<User | null>(null)
  const [purgeFiles, setPurgeFiles] = useState(true)
  const [restricting, setRestricting] = useState<User | null>(null)
  const [draftCredits, setDraftCredits] = useState('')
  // Ids with an in-flight mutation.
  const [busy, setBusy] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  // Placeholder until the API answers.
  const [economics, setEconomics] = useState({ perUsd: 100_000, budgetHeadroom: 0.2 })

  useEffect(() => {
    void loadUsers()
    adminApi.catalogue().then(setCatalogue).catch(() => setCatalogue(null))
    adminApi
      .settings()
      .then((s) => s.credits && setEconomics(s.credits))
      .catch(() => {})
  }, [loadUsers])

  /** Proxy budget for an allowance; mirrors `budget_usd` on the API. */
  const proxyBudget = (credits: number) =>
    `$${(Math.ceil((credits / economics.perUsd) * (1 + economics.budgetHeadroom) * 100) / 100).toFixed(2)}`

  /** Runs one row action, guarding against double-fire and surfacing failure. */
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
  // Pending accounts have no cycle.
  const resetDate = users.find((u) => u.cycleResetsAt)?.cycleResetsAt

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
          {/* The scroll box sits inside the card: the card keeps `overflow-hidden` for its
              rounded corners, and a narrow screen scrolls the row to its last button. */}
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
                        {/* No key: turns fall back to the master key, unattributed. */}
                        <p className="mt-0.5 flex items-center gap-1 text-2xs text-faint">
                          <KeyRound size={9} />
                          {u.litellmKeyPreview ? (
                            <span className="font-mono">{u.litellmKeyPreview}</span>
                          ) : (
                            <span className="text-warn">{t('전용 키 없음')}</span>
                          )}
                          {rotated?.id === u.id && (
                            <span className="text-success">{t('방금 재발급됨')}</span>
                          )}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge tone={statusTone[u.status]}>{t(statusLabel[u.status])}</Badge>
                    {u.status === 'pending' && u.emailVerifiedAt === null && (
                      <Badge
                        className="ml-1"
                        title={t('확인 메일의 링크를 아직 누르지 않았습니다. 승인하면 확인한 것으로 칩니다.')}
                      >
                        {t('메일 미확인')}
                      </Badge>
                    )}
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
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 정보 수정').replace('{name}', u.name)}
                        title={t('이름·이메일을 고치거나 비밀번호를 초기화합니다')}
                        disabled={busy.includes(u.id)}
                        onClick={() => {
                          setProfile(u)
                          setDraftName(u.name)
                          setDraftEmail(u.email)
                          setDraftPassword('')
                          setProfileNote(null)
                          setProfileError(null)
                        }}
                      >
                        <UserPen size={14} />
                      </Button>
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
                        className="text-danger hover:bg-danger/10 hover:text-danger"
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
                        aria-label={t('{name} 키 관리').replace('{name}', u.name)}
                        title={t('KloudChat 키를 재발급·교체하고, 이 사용자가 발급한 키를 확인·삭제합니다')}
                        disabled={busy.includes(u.id)}
                        onClick={() => openKeys(u)}
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
            {(catalogue ?? models).map((m) => {
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
                if (target) void run(target.id, () => removeUser(target.id, purgeFiles))
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
        <label className="mt-3 flex items-start gap-2 text-base">
          <input
            type="checkbox"
            checked={purgeFiles}
            onChange={(e) => setPurgeFiles(e.target.checked)}
            className="mt-1"
          />
          <span>
            {t('올린 파일과 만든 그림·클립도 디스크에서 지웁니다')}
            <span className="block text-xs text-faint">
              {t('끄면 파일은 남고, 디스크가 차면 저장소 정리가 오래된 것부터 지웁니다.')}
            </span>
          </span>
        </label>
      </Modal>

      <Modal
        open={!!profile}
        onClose={() => setProfile(null)}
        title={t('정보 수정')}
        description={profile ? `${profile.email}` : undefined}
        footer={<Button onClick={() => setProfile(null)}>{t('닫기')}</Button>}
      >
        {profile && (
          <div className="space-y-4">
            <Field label={t('이름')}>
              <Input value={draftName} onChange={(e) => setDraftName(e.target.value)} aria-label={t('이름')} />
            </Field>
            <Field label={t('이메일')}>
              <Input type="email" value={draftEmail} onChange={(e) => setDraftEmail(e.target.value)} aria-label={t('이메일')} />
            </Field>
            <div className="flex justify-end">
              <Button
                variant="primary"
                size="sm"
                disabled={busy.includes(profile.id) || (!draftName.trim() || draftName.trim() === profile.name) && draftEmail.trim().toLowerCase() === profile.email}
                onClick={() => {
                  const id = profile.id
                  const patch: { name?: string; email?: string } = {}
                  if (draftName.trim() && draftName.trim() !== profile.name) patch.name = draftName.trim()
                  if (draftEmail.trim().toLowerCase() !== profile.email) patch.email = draftEmail.trim()
                  setProfileError(null)
                  void run(id, async () => {
                    try {
                      await updateUser(id, patch)
                      setProfileNote(t('저장했습니다.'))
                      setProfile((current) => current ? { ...current, ...patch } : current)
                    } catch (err) {
                      setProfileError(errorMessage(err, t('저장하지 못했습니다.')))
                    }
                  })
                }}
              >
                {t('저장')}
              </Button>
            </div>
            <div className="border-t border-line pt-4">
              <Field label={t('비밀번호 초기화')} hint={t('새 비밀번호를 정해 본인에게 따로 전달하세요. 저장하면 이 계정의 모든 로그인이 풀립니다.')}>
                <div className="flex gap-2">
                  <Input
                    value={draftPassword}
                    onChange={(e) => setDraftPassword(e.target.value)}
                    placeholder={t('8자 이상')}
                    aria-label={t('새 비밀번호')}
                    autoComplete="off"
                    className="font-mono"
                  />
                  <Button
                    size="sm"
                    onClick={() => {
                      const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
                      const bytes = crypto.getRandomValues(new Uint8Array(14))
                      setDraftPassword(Array.from(bytes, (b) => alphabet[b % alphabet.length]).join(''))
                    }}
                  >
                    {t('임의 생성')}
                  </Button>
                  <Button
                    variant="danger"
                    size="sm"
                    disabled={busy.includes(profile.id) || draftPassword.length < 8}
                    onClick={() => {
                      const id = profile.id
                      const password = draftPassword
                      setProfileError(null)
                      void run(id, async () => {
                        try {
                          await resetUserPassword(id, password)
                          setProfileNote(t('비밀번호를 바꿨습니다. 이 계정의 기존 로그인은 모두 풀렸습니다.'))
                        } catch (err) {
                          setProfileError(errorMessage(err, t('비밀번호를 바꾸지 못했습니다.')))
                        }
                      })
                    }}
                  >
                    {t('초기화')}
                  </Button>
                </div>
              </Field>
            </div>
            {profileNote && <p className="text-base text-success">{profileNote}</p>}
            {profileError && <p className="text-base text-danger">{profileError}</p>}
          </div>
        )}
      </Modal>

      <Modal
        open={!!keysFor}
        onClose={() => setKeysFor(null)}
        title={t('키 관리')}
        description={keysFor ? `${keysFor.name} · ${keysFor.email}` : undefined}
        width="max-w-2xl"
        footer={<Button onClick={() => setKeysFor(null)}>{t('닫기')}</Button>}
      >
        {keysFor && (
          <div className="space-y-5">
            <section>
              <h3 className="text-sm font-semibold text-muted">{t('KloudChat 키')}</h3>
              <p className="mt-0.5 text-sm text-faint">{t('이 사용자의 모든 호출이 이 키로 프록시를 지납니다. 브라우저에는 끝 네 자리만 옵니다.')}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="font-mono text-base">{keys?.kloudchat?.preview ?? keysFor.litellmKeyPreview ?? t('없음')}</span>
                {keys?.kloudchat?.issuedAt && <span className="text-sm text-faint">{t('발급')} {formatDate(keys.kloudchat.issuedAt)}</span>}
                {!confirmRotate ? (
                  <Button size="sm" disabled={busy.includes(keysFor.id)} onClick={() => setConfirmRotate(true)}>
                    {keys?.kloudchat ? t('재발급') : t('발급')}
                  </Button>
                ) : (
                  <span className="flex items-center gap-1.5 text-sm">
                    <span className="text-danger">{t('기존 키는 즉시 폐기됩니다.')}</span>
                    <Button
                      variant="danger"
                      size="sm"
                      onClick={() => {
                        const id = keysFor.id
                        setConfirmRotate(false)
                        setKeyError(null)
                        void run(id, async () => {
                          try {
                            await rotateLitellmKey(id)
                            const fresh = useStore.getState().users.find((x) => x.id === id)
                            setRotated({ id, preview: fresh?.litellmKeyPreview ?? null })
                            setKeyNote(t('새 키를 발급했습니다.'))
                            await loadKeys(id)
                          } catch (err) {
                            setKeyError(errorMessage(err, t('키를 발급하지 못했습니다.')))
                          }
                        })
                      }}
                    >
                      {t('확인')}
                    </Button>
                    <Button size="sm" onClick={() => setConfirmRotate(false)}>{t('취소')}</Button>
                  </span>
                )}
              </div>
              <Field label={t('교체')} hint={t('이미 갖고 있는 LiteLLM 키를 이 계정의 키로 씁니다. 프록시가 아는 키여야 하고, 기존 키는 폐기됩니다.')}>
                <div className="flex gap-2">
                  <Input value={keyDraft} onChange={(e) => setKeyDraft(e.target.value)} placeholder="sk-…" aria-label={t('교체할 키')} autoComplete="off" className="font-mono" />
                  <Button
                    size="sm"
                    disabled={busy.includes(keysFor.id) || keyDraft.trim().length < 8}
                    onClick={() => {
                      const id = keysFor.id
                      const key = keyDraft.trim()
                      setKeyError(null)
                      void run(id, async () => {
                        try {
                          await replaceLitellmKey(id, key)
                          setKeyDraft('')
                          setKeyNote(t('키를 교체했습니다.'))
                          await loadKeys(id)
                        } catch (err) {
                          setKeyError(errorMessage(err, t('키를 교체하지 못했습니다. 프록시가 아는 키인지 확인하세요.')))
                        }
                      })
                    }}
                  >
                    {t('교체')}
                  </Button>
                </div>
              </Field>
            </section>
            <section>
              <h3 className="text-sm font-semibold text-muted">{t('이 사용자가 발급한 키')}</h3>
              {keys === null ? (
                <p className="mt-2 text-sm text-faint">{t('불러오는 중…')}</p>
              ) : keys.named.length === 0 ? (
                <p className="mt-2 text-sm text-faint">{t('발급한 키가 없습니다.')}</p>
              ) : (
                <ul className="mt-2 divide-y divide-line rounded-card border border-line">
                  {keys.named.map((k) => (
                    <li key={k.id} className="flex items-center gap-3 px-3 py-2 text-base">
                      <span className="min-w-0 flex-1 truncate">{k.name}</span>
                      <span className="font-mono text-sm text-muted">{k.preview}</span>
                      <span className="text-sm text-faint">{t('발급')} {formatDate(k.createdAt)}</span>
                      <span className="text-sm text-faint">{k.lastUsedAt ? `${t('마지막 사용')} ${relativeTime(k.lastUsedAt)}` : t('사용 기록 없음')}</span>
                      <Button variant="ghost" size="icon" aria-label={t('{name} 키 삭제').replace('{name}', k.name)} title={t('삭제')} className="text-danger hover:bg-danger/10 hover:text-danger" disabled={busy.includes(keysFor.id)} onClick={() => setRevokingKey(k)}>
                        <Trash2 size={14} />
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            {keyNote && <p className="text-base text-success">{keyNote}</p>}
            {keyError && <p className="text-base text-danger">{keyError}</p>}
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={!!revokingKey}
        onClose={() => setRevokingKey(null)}
        onConfirm={() => {
          const target = revokingKey
          const owner = keysFor
          setRevokingKey(null)
          if (!target || !owner) return
          setKeyError(null)
          void run(owner.id, async () => {
            try {
              await adminApi.revokeUserKey(owner.id, target.id)
              setKeyNote(t('「{name}」 키를 삭제했습니다.').replace('{name}', target.name))
              await loadKeys(owner.id)
            } catch (err) {
              setKeyError(errorMessage(err, t('키를 삭제하지 못했습니다.')))
            }
          })
        }}
        title={t('「{name}」 키를 삭제할까요?').replace('{name}', revokingKey?.name ?? '')}
        description={t('되돌릴 수 없습니다. 이 키로 호출하던 도구나 스크립트는 즉시 멈춥니다.')}
      />

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
            {/* The proxy copy sits above the real limit as a backstop. */}
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
