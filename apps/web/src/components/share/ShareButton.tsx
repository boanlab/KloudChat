import { Check, Copy, Globe, Link2, Loader2, MapPin, Share2, Trash2, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Field, Input, Modal } from '@/components/ui'
import { errorMessage, sharesApi, type ShareRow, type ShareViewRow } from '@/lib/api'
import { browserName, cn } from '@/lib/utils'
import type { Session } from '@/types'
import { copyText } from '@/lib/clipboard'
import { currentLocale } from '@/lib/i18n'
import { useT } from '@/lib/useT'

type Scope = 'workspace' | 'link'

const options: { id: Scope; label: string; description: string; icon: typeof Users }[] = [
  {
    id: 'workspace',
    // Not "workspace members": there are no members. There is no team, no
    // group and no invite anywhere in this product — `ShareScope.workspace`
    // asks whether the reader is signed in and never asks who they are. A
    // label promising a named roster would be read as one by somebody sharing
    // a draft, which is the kind of misreading that does not come back.
    label: '계정이 있는 사람',
    description: '이 인스턴스에 로그인하면 누구나 열 수 있습니다.',
    icon: Users,
  },
  {
    id: 'link',
    label: '링크가 있는 사람',
    description: '계정 없이도 열립니다. 링크를 아는 누구나 읽을 수 있습니다.',
    icon: Link2,
  },
]

//: The top bar's standing statement, in words rather than in colour alone.
const sharedState: Record<Scope, { label: string; title: string; tone: 'accent' | 'warn' }> = {
  workspace: {
    label: '공유 중',
    title: '이 인스턴스에 로그인한 사람은 누구나 이 대화를 열 수 있습니다.',
    tone: 'accent',
  },
  link: {
    label: '링크 공개 중',
    title: '링크를 아는 누구나 계정 없이 이 대화를 읽을 수 있습니다.',
    tone: 'warn',
  },
}

/**
 * Read-only sharing of one conversation and the artifacts it produced.
 *
 * Nothing is rendered until the link actually exists: it is minted when the
 * create button is pressed, so the URL on screen is always a working URL.
 *
 * The state stands in the top bar beside the button, not behind it. A
 * conversation anybody with the URL can read must not look like a private one,
 * and somebody who has forgotten they shared it never opens the dialog to find
 * out.
 */
export function ShareButton({ session }: { session: Session }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [scope, setScope] = useState<Scope>('workspace')
  const [share, setShare] = useState<ShareRow | null>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const url = share ? `${window.location.origin}/share/${share.token}` : ''

  // Asked on arrival rather than when the dialog opens, and asked again for
  // each conversation: the top bar is where the answer has to be.
  useEffect(() => {
    let live = true
    setShare(null)
    void sharesApi
      .list()
      .then((rows) => {
        if (live) setShare(rows.find((r) => r.sessionId === session.id) ?? null)
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [session.id])

  const load = async () => {
    setOpen(true)
    setError(null)
    try {
      const rows = await sharesApi.list()
      setShare(rows.find((r) => r.sessionId === session.id) ?? null)
    } catch {
      // Offline: the button keeps saying what it last knew, which is truer
      // than saying nothing is shared.
    }
  }

  const create = async () => {
    setBusy(true)
    setError(null)
    try {
      setShare(await sharesApi.create({ sessionId: session.id, scope }))
    } catch (err) {
      setError(errorMessage(err, t('링크를 만들지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const revoke = async () => {
    if (!share) return
    setBusy(true)
    try {
      await sharesApi.revoke(share.id)
      setShare(null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {share && (
        <Badge tone={sharedState[share.scope].tone} title={t(sharedState[share.scope].title)}>
          {share.scope === 'link' ? <Globe size={10} /> : <Users size={10} />}
          {t(sharedState[share.scope].label)}
        </Badge>
      )}
      <Button size="sm" onClick={() => void load()} aria-label={t('공유')}>
        <Share2 size={14} />
        {t('공유')}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('공유')}
        description={session.title}
        footer={<Button variant="primary" onClick={() => setOpen(false)}>{t('완료')}</Button>}
      >
        <p className="text-sm text-muted">
          {t('공유한 뒤에 오가는 대화도 링크에 그대로 나타납니다. 지금까지만 보이게 하려면 링크를 철회하세요.')}
        </p>
        {share ? (
          <>
            <Field label={t('공유 링크')}>
              <div className="flex gap-2">
                <Input readOnly value={url} className="font-mono text-sm" aria-label={t('공유 링크')} />
                <Button
                  onClick={async () => {
                    if (!(await copyText(url))) return
                    setCopied(true)
                    setTimeout(() => setCopied(false), 1500)
                  }}
                >
                  {copied ? <Check size={14} /> : <Copy size={14} />}
                  {copied ? t('복사됨') : t('복사')}
                </Button>
              </div>
            </Field>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
              <Badge tone={share.scope === 'link' ? 'warn' : 'neutral'}>
                {share.scope === 'link' ? <Globe size={10} /> : <Users size={10} />}
                {share.scope === 'link' ? t('계정 없이 열림') : t('계정 필요')}
              </Badge>
              <span>{t('{n}회 열람').replace('{n}', String(share.views))}</span>
              <span className="text-faint">{t('대화와 결과물만 보입니다 — 프로젝트 파일·메모리는 포함되지 않습니다.')}</span>
            </div>
            <ShareViews shareId={share.id} />
            <Button variant="danger" size="sm" disabled={busy} onClick={() => void revoke()}>
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              {t('링크 철회')}
            </Button>
          </>
        ) : (
          <>
            <div className="space-y-1.5">
              {options.map((o) => {
                const Icon = o.icon
                const active = scope === o.id
                return (
                  <button
                    key={o.id}
                    onClick={() => setScope(o.id)}
                    className={cn(
                      'flex w-full items-start gap-2.5 rounded-card border p-3 text-left transition-colors',
                      active
                        ? 'border-accent bg-accent-soft'
                        : 'border-line hover:bg-elevated',
                    )}
                  >
                    <Icon size={15} className={cn('mt-0.5 shrink-0', active && 'text-accent')} />
                    <span className="min-w-0 flex-1">
                      <span className="block text-base font-medium">{o.label}</span>
                      <span className="block text-sm text-muted">{o.description}</span>
                    </span>
                  </button>
                )
              })}
            </div>
            {error && <p className="text-sm text-danger">{error}</p>}
            <Button variant="primary" disabled={busy} onClick={() => void create()}>
              {busy && <Loader2 size={13} className="animate-spin" />}
              {t('링크 만들기')}
            </Button>
          </>
        )}
      </Modal>
    </>
  )
}


/**
 * Who has opened this link.
 *
 * The count above answers "is anyone reading it". This answers "who", which is
 * the question somebody actually asks — usually right after realising they
 * shared the wrong thing, or the right thing with the wrong scope.
 *
 * What each row can say depends on how the reader arrived. Signed in, it names
 * them. Not signed in — which is the entire point of a link share — the
 * address is the only thing this server ever learned, so the address is what
 * it says. A row with neither is a reader behind a proxy that forwards
 * nothing, and it says that too rather than inventing a label.
 */
function ShareViews({ shareId }: { shareId: string }) {
  const t = useT()
  const [rows, setRows] = useState<ShareViewRow[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    setRows(null)
    setFailed(false)
    void sharesApi
      .views(shareId)
      .then((r) => live && setRows(r))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [shareId])

  if (failed) return <p className="text-sm text-muted">{t('열람 기록을 불러오지 못했습니다.')}</p>
  if (rows === null) return <Loader2 size={14} className="animate-spin text-faint" />
  if (rows.length === 0) {
    return <p className="text-sm text-muted">{t('아직 아무도 열지 않았습니다.')}</p>
  }

  const when = (iso: string) =>
    new Date(iso).toLocaleString(currentLocale(), {
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })

  return (
    <Field label={t('열람 기록')}>
      <ul className="divide-y divide-line rounded-card border border-line">
        {rows.map((v) => {
          const browser = browserName(v.userAgent)
          return (
            <li key={v.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-3 py-2">
              <span className="text-base font-medium">
                {v.name || v.email || t('계정 없는 방문자')}
              </span>
              {v.name && v.email && <span className="text-sm text-muted">{v.email}</span>}
              <span className="ml-auto text-sm text-faint tabular-nums">
                {when(v.lastAt)}
                {v.opens > 1 && ` · ${t('{n}회').replace('{n}', String(v.opens))}`}
              </span>
              <span className="flex w-full flex-wrap items-center gap-x-2 text-sm text-muted">
                {v.region && (
                  <span className="inline-flex items-center gap-1">
                    <MapPin size={11} className="text-faint" />
                    {v.region}
                  </span>
                )}
                {/* The raw string on hover: the short form drops exactly what
                    would matter if this ever became a serious question. */}
                {browser && <span title={v.userAgent}>{browser}</span>}
                <span className="font-mono text-faint tabular-nums">
                  {v.ip || t('주소 없음')}
                </span>
              </span>
            </li>
          )
        })}
      </ul>
    </Field>
  )
}
