import { LayoutTemplate, Paperclip, Plus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { TemplateForm } from '@/components/chat/TemplateGallery'
import { Badge, Button } from '@/components/ui'
import { kindMeta } from '@/lib/kinds'
import { templatesApi, type TemplateRow } from '@/lib/api'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** Surfaces a template can start. Chat included: a prompt is a starting point too. */
const KINDS: readonly SessionKind[] = ['chat', 'report', 'slides', 'image', 'av']

/**
 * Templates the whole instance sees.
 *
 * The gallery already lets anybody write one for themselves. This is the other
 * case: an organisation's own 공문 or 발표 양식, entered once and offered to
 * every account. Shared rather than copied, so a correction to the form reaches
 * everybody who has not started yet.
 */
export function TemplatesSection() {
  const t = useT()
  const [rows, setRows] = useState<TemplateRow[]>([])
  const [adding, setAdding] = useState(false)

  const load = async () => setRows(await templatesApi.list().catch(() => []))
  useEffect(() => {
    void load()
  }, [])

  // Only the shared ones. An administrator's own private templates belong in
  // the gallery with everybody else's.
  const shared = rows.filter((r) => r.shared)

  const remove = async (id: string) => {
    setRows((r) => r.filter((x) => x.id !== id))
    try {
      await templatesApi.remove(id)
    } catch {
      await load()
    }
  }

  // Named region: three inputs on this screen answer to "이름" (branding, SMTP,
  // and this form), so the section has to be addressable on its own.
  if (adding) {
    return (
      <section aria-label={t('공용 템플릿')}>
        <TemplateForm
          kinds={KINDS}
          shared
          onCancel={() => setAdding(false)}
          onSaved={(row) => {
            setRows((r) => [row, ...r])
            setAdding(false)
          }}
        />
      </section>
    )
  }

  return (
    <section aria-label={t('공용 템플릿')} className="space-y-3">
      {shared.length === 0 ? (
        <p className="text-[13px] text-faint">
          {t('아직 공용 템플릿이 없습니다. 추가하면 모든 사용자의 템플릿 목록에 함께 보입니다.')}
        </p>
      ) : (
        <ul className="space-y-1">
          {shared.map((row) => (
            <li
              key={row.id}
              className="flex items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[13px]"
            >
              <LayoutTemplate size={13} className="shrink-0 text-faint" />
              <span className="min-w-0 flex-1 truncate">{row.title}</span>
              {row.fileName && (
                <span className="flex shrink-0 items-center gap-1 text-[11px] text-accent">
                  <Paperclip size={10} />
                  {row.fileName}
                </span>
              )}
              <Badge>{t(kindMeta[row.kind].label)}</Badge>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t('{name} 삭제').replace('{name}', row.title)}
                onClick={() => void remove(row.id)}
              >
                <Trash2 size={13} />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button onClick={() => setAdding(true)}>
        <Plus size={13} />
        {t('공용 템플릿 추가')}
      </Button>
    </section>
  )
}
