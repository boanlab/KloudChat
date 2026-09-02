import { Loader2, Paperclip, X } from 'lucide-react'
import { useState } from 'react'
import { Button, Field, Input, Textarea } from '@/components/ui'
import { errorMessage, filesApi, templatesApi, type TemplateRow } from '@/lib/api'
import { kindMeta } from '@/lib/kinds'
import { cn } from '@/lib/utils'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

export function TemplateForm({
  kind,
  kinds,
  shared = false,
  template,
  onCancel,
  onSaved,
}: {
  /** Fixed surface. Omitted when the caller offers `kinds` to pick from. */
  kind?: SessionKind
  /** Surfaces to choose between — the admin screen writes for any of them. */
  kinds?: readonly SessionKind[]
  /** Offered to every account. Refused by the server for non-administrators. */
  shared?: boolean
  /** The row being corrected. Absent when this is a new one. */
  template?: TemplateRow
  onCancel: () => void
  onSaved: (row: TemplateRow) => void
}) {
  const t = useT()
  // Seeded once: the form is mounted afresh for each template, so a row that
  // arrives later is a row the person is no longer editing.
  const [surface, setSurface] = useState<SessionKind>(
    template?.kind ?? kind ?? kinds?.[0] ?? 'report',
  )
  const [title, setTitle] = useState(template?.title ?? '')
  const [description, setDescription] = useState(template?.description ?? '')
  const [fills, setFills] = useState(template?.fills.join(', ') ?? '')
  //: One example per blank, kept beside the blank it belongs to. A card
  //: that names a blank without showing how to fill it asks for a format
  //: nobody has been shown — the built-ins carry these, so this can too.
  const [examples, setExamples] = useState<string[]>(template?.examples ?? [])
  const [needsWeb, setNeedsWeb] = useState(template?.needs?.includes('web') ?? false)
  const [needsFile, setNeedsFile] = useState(template?.needs?.includes('file') ?? false)
  const [prompt, setPrompt] = useState(template?.prompt ?? '')
  const [group, setGroup] = useState(template?.group ?? '')
  const [file, setFile] = useState<{ id: string; name: string } | null>(
    template?.fileId ? { id: template.fileId, name: template.fileName } : null,
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A shared template stays shared through a correction. Its author is the
  // administrator either way, and quietly making it private on a typo fix
  // would take it out of everybody else's gallery.
  const forEverybody = template?.shared ?? shared

  const attach = async (picked: File) => {
    setBusy(true)
    setError(null)
    try {
      const row = await filesApi.upload(picked)
      setFile({ id: row.id, name: row.name })
    } catch (err) {
      setError(errorMessage(err, t('파일을 올리지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  const save = async () => {
    setBusy(true)
    setError(null)
    const payload = {
      kind: kind ?? surface,
      shared: forEverybody,
      group: group.trim() || (forEverybody ? '공용' : '내 템플릿'),
      title: title.trim(),
      description: description.trim(),
      fills: fills
        .split(',')
        .map((f) => f.trim())
        .filter(Boolean),
      examples: fills
        .split(',')
        .map((f) => f.trim())
        .filter(Boolean)
        .map((_, index) => (examples[index] ?? '').trim()),
      needs: [...(needsWeb ? ['web'] : []), ...(needsFile ? ['file'] : [])],
      prompt,
      fileId: file?.id ?? null,
    }
    try {
      onSaved(
        template
          ? await templatesApi.update(template.id, payload)
          : await templatesApi.create(payload),
      )
    } catch (err) {
      setError(errorMessage(err, t('템플릿을 저장하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      {kinds && (
        <Field label={t('화면')} hint={t('이 템플릿이 시작할 화면입니다')}>
          <div className="flex flex-wrap gap-1.5">
            {kinds.map((k) => (
              <button
                key={k}
                onClick={() => setSurface(k)}
                className={cn(
                  'rounded-control border px-2.5 py-1 text-base transition-colors',
                  surface === k
                    ? 'border-accent bg-accent-soft text-accent'
                    : 'border-line text-muted hover:bg-elevated',
                )}
              >
                {t(kindMeta[k].label)}
              </button>
            ))}
          </div>
        </Field>
      )}
      <Field label={t('이름')}>
        <Input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t('예: 공문 초안')}
          aria-label={t('이름')}
          autoFocus
        />
      </Field>
      <Field label={t('설명')} hint={t('무엇이 나오는지 한 줄로')}>
        <Input
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          aria-label={t('설명')}
        />
      </Field>
      <Field label={t('분류')} hint={forEverybody ? t('비우면 "공용"') : t('비우면 "내 템플릿"')}>
        <Input value={group} onChange={(e) => setGroup(e.target.value)} aria-label={t('분류')} />
      </Field>
      <Field label={t('적어 달라고 할 것')} hint={t('쉼표로 구분. 카드에 빈칸으로 나옵니다')}>
        <Input
          value={fills}
          onChange={(e) => setFills(e.target.value)}
          placeholder={t('수신처, 제목, 본문 요지')}
          aria-label={t('준비물')}
        />
      </Field>
      {fills.split(',').map((f) => f.trim()).filter(Boolean).length > 0 && (
        <Field label={t('빈칸마다 예시')} hint={t('어떻게 적으면 되는지 보여 줍니다. 빈칸 안에 흐리게 나옵니다')}>
          <div className="space-y-1.5">
            {fills.split(',').map((f) => f.trim()).filter(Boolean).map((fill, index) => (
              <div key={`${fill}-${index}`} className="flex items-center gap-2">
                <span className="w-28 shrink-0 truncate text-sm text-muted" title={fill}>{fill}</span>
                <Input
                  value={examples[index] ?? ''}
                  onChange={(e) => setExamples((all) => { const next = [...all]; next[index] = e.target.value; return next })}
                  placeholder={t('예: …')}
                  aria-label={t('{name} 예시').replace('{name}', fill)}
                  className="h-8 text-sm"
                />
              </div>
            ))}
          </div>
        </Field>
      )}
      <Field label={t('이 일에 필요한 것')} hint={t('카드에 미리 적히고, 고르면 입력창이 맞춥니다')}>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="inline-flex items-center gap-1.5">
            <input type="checkbox" checked={needsWeb} onChange={(e) => setNeedsWeb(e.target.checked)} />
            {t('웹 검색')}
          </label>
          <label className="inline-flex items-center gap-1.5">
            <input type="checkbox" checked={needsFile} onChange={(e) => setNeedsFile(e.target.checked)} />
            {t('파일 첨부')}
          </label>
        </div>
      </Field>
      <Field label={t('문구')} hint={t('요청과 함께 전달됩니다. 입력창에는 나타나지 않습니다')}>
        <Textarea
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          rows={4}
          aria-label={t('문구')}
        />
      </Field>
      <Field label={t('양식 파일')} hint={t('올리면 그 양식에 맞춰 씁니다. 선택 사항입니다')}>
        {file ? (
          <div className="flex items-center gap-2 rounded-card border border-line bg-panel px-3 py-2 text-base">
            <Paperclip size={13} className="shrink-0 text-accent" />
            <span className="min-w-0 flex-1 truncate">{file.name}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('양식 파일 떼기')}
              onClick={() => setFile(null)}
            >
              <X size={13} />
            </Button>
          </div>
        ) : (
          <input
            type="file"
            aria-label={t('양식 파일')}
            className="block w-full text-base file:mr-3 file:rounded-control file:border file:border-line file:bg-elevated file:px-3 file:py-1.5 file:text-base"
            onChange={(e) => {
              const picked = e.target.files?.[0]
              if (picked) void attach(picked)
            }}
          />
        )}
      </Field>
      {error && <p className="text-base text-danger">{error}</p>}
      <div className="flex gap-2">
        <Button variant="primary" disabled={busy || !title.trim()} onClick={() => void save()}>
          {busy && <Loader2 size={13} className="animate-spin" />}
          {t('저장')}
        </Button>
        <Button onClick={onCancel}>{t('취소')}</Button>
      </div>
    </div>
  )
}
