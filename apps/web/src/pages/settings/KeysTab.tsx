import { Copy, KeyRound, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button, Card, Field, Input, Modal } from '@/components/ui'
import { relativeTime } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

/**
 * API keys a user takes away and uses from their own code.
 *
 * Separate from the virtual key kchat uses on their behalf, which never leaves
 * the server. This one is shown here exactly once, at the moment it is
 * created; after that only the last four characters exist, on screen and in
 * the database alike.
 *
 * Spend, the monthly limit and the model allow-list all follow the key, so
 * issuing one grants no new permission.
 */
export function KeysTab() {
  const t = useT()
  const { apiKeys, loadApiKeys, createApiKey, revokeApiKey, user } = useStore()
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [issued, setIssued] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void loadApiKeys()
  }, [loadApiKeys])

  const allowed = user?.allowedModels ?? []

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-[13px] font-semibold">{t('API 키')}</h2>
          <p className="text-xs text-muted">
            {t('내 코드에서 직접 모델을 호출할 때 씁니다. 사용량과 월 한도는 이 계정에 그대로 합산됩니다.')}
          </p>
        </div>
        <Button variant="primary" onClick={() => setCreating(true)}>
          <Plus size={15} />
          {t('새 키')}
        </Button>
      </div>

      {allowed.length > 0 && (
        <Card className="p-3.5">
          <p className="text-[13px] text-muted">
            {t('이 계정은 모델 {n}개로 제한되어 있습니다. 발급한 키도 같은 범위만 호출할 수 있습니다.').replace(
              '{n}',
              String(allowed.length),
            )}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {allowed.map((m) => (
              <Badge key={m}>{m}</Badge>
            ))}
          </div>
        </Card>
      )}

      {apiKeys === null ? (
        <Card className="p-8 text-center text-[13px] text-muted">{t('불러오는 중입니다…')}</Card>
      ) : apiKeys.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-sm font-medium">{t('발급한 키가 없습니다')}</p>
          <p className="mt-1 text-[13px] text-muted">
            {t('키는 만들 때 한 번만 보여 줍니다. 그 뒤로는 서버에도 원문이 남지 않습니다.')}
          </p>
        </Card>
      ) : (
        <div className="space-y-2">
          {apiKeys.map((k) => (
            <Card key={k.id} className="flex items-center gap-3 p-3.5">
              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-elevated text-muted">
                <KeyRound size={15} />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] font-medium">{k.name}</p>
                <p className="text-[11px] text-faint">
                  <span className="font-mono">{k.preview}</span> ·{' '}
                    {t('{when} 발급').replace('{when}', relativeTime(k.createdAt))}
                  {k.lastUsedAt && ` · ${t('{when} 사용').replace('{when}', relativeTime(k.lastUsedAt))}`}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t('{name} 폐기').replace('{name}', k.name)}
                onClick={() => void revokeApiKey(k.id)}
              >
                <Trash2 size={14} />
              </Button>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={creating}
        onClose={() => {
          setCreating(false)
          setName('')
          setIssued(null)
          setError(null)
        }}
        title={issued ? t('키가 발급되었습니다') : t('새 API 키')}
        description={
          issued
            ? t('지금 복사해 두세요. 이 화면을 닫으면 다시 볼 수 없습니다.')
            : t('어디에 쓸 키인지 적어 두면 나중에 어느 것을 폐기할지 알 수 있습니다.')
        }
        footer={
          issued ? (
            <Button
              variant="primary"
              onClick={() => {
                setCreating(false)
                setName('')
                setIssued(null)
              }}
            >
              {t('닫기')}
            </Button>
          ) : (
            <>
              <Button onClick={() => setCreating(false)}>{t('취소')}</Button>
              <Button
                variant="primary"
                disabled={busy || !name.trim()}
                onClick={async () => {
                  setBusy(true)
                  setError(null)
                  try {
                    setIssued(await createApiKey(name.trim()))
                  } catch (err) {
                    setError(err instanceof Error ? err.message : t('발급에 실패했습니다.'))
                  } finally {
                    setBusy(false)
                  }
                }}
              >
                {busy ? t('발급 중…') : t('발급')}
              </Button>
            </>
          )
        }
      >
        {issued ? (
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto rounded-lg border border-line bg-elevated px-3 py-2 font-mono text-[12px]">
              {issued}
            </code>
            <Button
              onClick={async () => {
                if (!(await copyText(issued))) return
                setCopied(true)
              }}
            >
              <Copy size={14} />
              {copied ? t('복사됨') : t('복사')}
            </Button>
          </div>
        ) : (
          <>
            <Field label={t('이름')}>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t('예: 분석 스크립트')}
                autoFocus
              />
            </Field>
            {error && <p className="text-[13px] text-danger">{error}</p>}
          </>
        )}
      </Modal>
    </div>
  )
}
