import { Check, Copy, Globe, Link2, Loader2, Share2, Trash2, Users } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Field, Input, Modal } from '@/components/ui'
import { errorMessage, sharesApi, type ShareRow } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { Session } from '@/types'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

type Scope = 'workspace' | 'link'

const options: { id: Scope; label: string; description: string; icon: typeof Users }[] = [
  {
    id: 'workspace',
    label: '워크스페이스 구성원',
    description: '이 인스턴스에 로그인한 사람만 열 수 있습니다.',
    icon: Users,
  },
  {
    id: 'link',
    label: '링크가 있는 사람',
    description: '계정 없이도 열립니다. 링크를 아는 누구나 읽을 수 있습니다.',
    icon: Link2,
  },
]

/**
 * Read-only sharing of one conversation and the artifacts it produced.
 *
 * Nothing is rendered until the link actually exists: it is minted when the
 * create button is pressed, so the URL on screen is always a working URL.
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

  const load = async () => {
    setOpen(true)
    setError(null)
    try {
      const rows = await sharesApi.list()
      setShare(rows.find((r) => r.sessionId === session.id) ?? null)
    } catch {
      setShare(null)
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
                {share.scope === 'link' ? t('계정 없이 열림') : t('구성원만')}
              </Badge>
              <span>{t('{n}회 열람').replace('{n}', String(share.views))}</span>
              <span className="text-faint">{t('대화와 결과물만 보입니다 — 프로젝트 파일·메모리는 포함되지 않습니다.')}</span>
            </div>
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
                      'flex w-full items-start gap-2.5 rounded-xl border p-3 text-left transition-colors',
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
