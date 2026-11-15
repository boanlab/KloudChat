import { useEffect, useState } from 'react'
import { Bot, Loader2 } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/components/ui'
import { errorMessage, userTemplatesApi } from '@/lib/api'
import type { UserTemplate } from '@/types'
import { useT } from '@/lib/useT'
import { useStore } from '@/store/useStore'

/**
 * A person's own templates, retired on 2026-11-15: each can become an agent of theirs, with its
 * format, questions, checks and process carried over. Shown only while there are any.
 */
export function OldTemplates() {
  const t = useT()
  const navigate = useNavigate()
  const loadWorkspace = useStore((s) => s.loadWorkspace)
  const [rows, setRows] = useState<UserTemplate[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    void userTemplatesApi.list().then((found) => { if (live) setRows(found.mine) }).catch(() => {})
    return () => { live = false }
  }, [])
  if (!rows.length) return null
  return (
    <section className="space-y-2 rounded-card border border-line px-4 py-3" data-testid="old-templates">
      <div>
        <p className="text-base font-medium">{t('내가 만든 템플릿')}</p>
        <p className="text-sm text-muted">
          {t('템플릿은 에이전트로 옮겨 씁니다. 옮기면 문서 형식·물어볼 빈칸·검사·진행 과정이 그대로 에이전트에 들어가고, 템플릿은 남겨 둡니다.')}
        </p>
      </div>
      <ul className="divide-y divide-line">
        {rows.map((row) => (
          <li key={row.id} className="flex items-center gap-2 py-2">
            <span className="min-w-0 flex-1">
              <span className="block truncate text-base">{row.name}</span>
              {row.description && <span className="block truncate text-xs text-faint">{row.description}</span>}
            </span>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy !== null}
              aria-label={t('{name} 템플릿을 에이전트로 옮기기').replace('{name}', row.name)}
              onClick={async () => {
                setBusy(row.id)
                setError('')
                try {
                  const agent = await userTemplatesApi.toAgent(row.id)
                  await loadWorkspace()
                  navigate(`/customize/agents/${agent.id}`)
                } catch (err) {
                  setError(errorMessage(err, t('에이전트로 옮기지 못했습니다. 다시 시도하세요.')))
                } finally {
                  setBusy(null)
                }
              }}
            >
              {busy === row.id ? <Loader2 size={13} className="animate-spin" /> : <Bot size={13} />}
              {t('에이전트로 옮기기')}
            </Button>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </section>
  )
}
