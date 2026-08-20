import { LayoutTemplate, Loader2, Paperclip, Pencil, Plus, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Button, Field, Input, Modal, Textarea } from '@/components/ui'
import {
  errorMessage,
  filesApi,
  promptTemplatesApi,
  templatesApi,
  type FileRow,
  type PromptTemplateRow,
  type TemplateRow,
} from '@/lib/api'
import { kindMeta } from '@/lib/kinds'
import { cn, upsertById } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { SessionKind } from '@/types'
import { useT } from '@/lib/useT'

/** A card in the gallery, whichever list it came from. */
type Card = PromptTemplateRow & { rowId?: string; form?: FileRow; shared?: boolean; mine?: boolean }

/**
 * Whether picking this card fills the composer or attaches to it.
 *
 * On the two media surfaces the sentence *is* the prompt — the person edits a
 * description of a picture and sends that, and a turn carrying an unseen
 * framing would leave them with nothing to edit. Everywhere else the framing
 * is the machinery's, and it goes with the turn instead of into their mouth.
 */
const fillsTheComposer = (kind: SessionKind) => kind === 'image' || kind === 'av'

const asCard = (row: TemplateRow): Card => ({
  id: row.id,
  rowId: row.id,
  kind: row.kind,
  group: row.group || '내 템플릿',
  title: row.title,
  description: row.description,
  fills: row.fills,
  prompt: row.prompt,
  shared: row.shared,
  mine: row.mine,
  // Enough of the file to stand as an attachment chip. The composer renders a
  // name, a token count and an extraction error; the rest it never reads.
  form: row.fileId
    ? {
        id: row.fileId,
        name: row.fileName,
        size: 0,
        mime: '',
        tokens: row.fileTokens,
        projectId: null,
        sessionId: null,
        preview: '',
        error: row.fileError,
        createdAt: row.updatedAt,
      }
    : undefined,
})

/**
 * 시작점 for each surface. Opened as a modal so it does not clutter an empty
 * screen, but the button that opens it is always visible — the point is that
 * somebody who does not know how to ask can still begin.
 *
 * A card shows **what you need to bring**, not a prompt to paste. Picking one
 * attaches it to the next turn rather than typing it out: the framing is the
 * product's sentence, and a transcript that reads it back as the person's own
 * cannot afterwards be untangled by anybody, including them.
 *
 * The built-in twenty-four come from the server; the rest are the ones this
 * person wrote. Both render as the same card, because "where did this come
 * from" is the product's problem and not the reader's — the only difference is
 * that their own can be rewritten and thrown away.
 */
export function TemplateGallery({ kind }: { kind: SessionKind }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [group, setGroup] = useState<string | 'all'>('all')
  const [mine, setMine] = useState<TemplateRow[]>([])
  const [composing, setComposing] = useState(false)
  /** The row being rewritten. Null while writing a new one or browsing. */
  const [editing, setEditing] = useState<TemplateRow | null>(null)
  const setDraft = useStore((s) => s.setDraft)
  const setPendingAttachment = useStore((s) => s.setPendingAttachment)
  const setPendingStartingTemplate = useStore((s) => s.setPendingStartingTemplate)

  // The workspace load already has them, so the gallery opens full. The fetch
  // below is the fallback for a screen reached before that landed — the same
  // arrangement the 서식 catalogue uses.
  const cached = useStore((s) => s.promptTemplates)
  const [builtIn, setBuiltIn] = useState<PromptTemplateRow[]>(cached)
  const items = useMemo<Card[]>(
    () => [
      ...mine.filter((r) => r.kind === kind).map(asCard),
      ...builtIn.filter((r) => r.kind === kind),
    ],
    [mine, builtIn, kind],
  )
  const groups = useMemo(() => [...new Set(items.map((i) => i.group))], [items])
  const visible = group === 'all' ? items : items.filter((i) => i.group === group)

  // Loaded when the gallery opens, not on mount: this sits under every empty
  // composer, and a request per surface visit buys nothing until it is read.
  useEffect(() => {
    if (!open) return
    let live = true
    void templatesApi
      .list()
      .then((rows) => live && setMine(rows))
      .catch(() => undefined)
    // The built-in catalogue is the server's now, so it arrives the same way
    // the person's own does. An instance that cannot serve it still opens on
    // whatever they wrote themselves, which is the half they would miss.
    if (cached.length) setBuiltIn(cached)
    else
      void promptTemplatesApi
        .list()
        .then((rows) => live && setBuiltIn(rows))
        .catch(() => undefined)
    return () => {
      live = false
    }
  }, [open, cached])

  const closeForm = () => {
    setComposing(false)
    setEditing(null)
  }

  const remove = async (id: string) => {
    // Optimistic: the card is theirs and the list is short, so a spinner on a
    // delete they just asked for is only a delay.
    setMine((rows) => rows.filter((r) => r.id !== id))
    try {
      await templatesApi.remove(id)
    } catch {
      setMine(await templatesApi.list().catch(() => []))
    }
  }

  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        <LayoutTemplate size={14} />
        {t('템플릿에서 시작')}
      </Button>

      <Modal
        open={open}
        onClose={() => {
          setOpen(false)
          closeForm()
        }}
        title={editing ? t('템플릿 수정') : t('무엇을 만드나요')}
        description={
          editing
            ? t('고친 내용은 다음에 이 템플릿을 고를 때부터 반영됩니다.')
            : t('고르면 이번 요청에 붙습니다. 무엇을 만들지는 직접 적으면 됩니다.')
        }
        width="max-w-2xl"
      >
        {composing || editing ? (
          <TemplateForm
            kind={kind}
            template={editing ?? undefined}
            onCancel={closeForm}
            onSaved={(row) => {
              setMine((rows) => upsertById(rows, row))
              closeForm()
            }}
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              {(['all', ...groups] as const).map((g) => (
                <button
                  key={g}
                  onClick={() => setGroup(g)}
                  className={cn(
                    'rounded-control border px-2.5 py-1 text-base transition-colors',
                    group === g
                      ? 'border-accent bg-accent-soft text-accent'
                      : 'border-line text-muted hover:bg-elevated',
                  )}
                >
                  {g === 'all' ? t('전체') : g}
                </button>
              ))}
              {/* The way in. Without it the gallery reads as a fixed menu, and
                  the form an organisation actually uses has nowhere to live. */}
              <Button size="sm" className="ml-auto" onClick={() => setComposing(true)}>
                <Plus size={13} />
                {t('템플릿 추가')}
              </Button>
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              {visible.map((item) => (
                <div key={item.id} className="group relative">
                  <button
                    onClick={() => {
                      setOpen(false)
                      // Carried by the turn, not typed into it — the composer
                      // shows a chip and asks for what this card asks for, and
                      // the sentence never becomes something the person said.
                      if (fillsTheComposer(item.kind)) setDraft(item.prompt)
                      else
                        setPendingStartingTemplate({
                          id: item.id,
                          title: item.title,
                          fills: item.fills,
                        })
                      // The form rides along as an attachment, which is what
                      // makes "이 양식대로 써 줘" mean anything: the model reads
                      // the document's actual shape rather than a description
                      // of it. Cleared when nothing is attached, so a plain
                      // template does not inherit the last one's form.
                      setPendingAttachment(item.form ?? null)
                    }}
                    className="w-full rounded-card border border-line bg-panel p-3.5 text-left transition-colors hover:border-accent hover:bg-elevated"
                  >
                    <p className="pr-14 text-base font-medium">
                      {item.title}
                      {/* Whose it is, because only one of the two can be
                          changed and the buttons appear on hover. */}
                      {item.shared && (
                        <span className="ml-1.5 align-middle text-xs font-normal text-faint">
                          {t('공용')}
                        </span>
                      )}
                    </p>
                    <p className="mt-1 text-sm text-muted">{item.description}</p>
                    <div className="mt-2.5 flex flex-wrap items-center gap-1">
                      {item.fills.map((f) => (
                        <span
                          key={f}
                          className="rounded-control bg-elevated px-1.5 py-0.5 text-xs text-faint transition-colors group-hover:bg-panel"
                        >
                          {f}
                        </span>
                      ))}
                      {/* The attached form, named. A template that writes into
                          a document behaves differently from one that does not,
                          and that has to be visible before it is chosen. */}
                      {item.form && (
                        <span className="flex items-center gap-1 rounded-control bg-accent-soft px-1.5 py-0.5 text-xs text-accent">
                          <Paperclip size={9} />
                          {item.form.name}
                        </span>
                      )}
                      {/* What the click does, on the card that does it. The
                          two answers are different enough — one hands you a
                          sentence to edit, the other hands the turn a 시작점 —
                          that guessing from a card is guessing. */}
                      <span className="ml-auto text-xs text-accent">
                        {fillsTheComposer(item.kind)
                          ? t('입력창에 채우기')
                          : t('시작점으로 붙이기')}
                      </span>
                    </div>
                  </button>
                  {item.rowId && item.mine !== false && (
                    <div className="absolute top-2 right-2 flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                      {/* Next to the delete button, because until it was here a
                          typo in a form used across the organisation had to be
                          fixed by throwing the template away and retyping it. */}
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 수정').replace('{name}', item.title)}
                        onClick={() => setEditing(mine.find((r) => r.id === item.rowId) ?? null)}
                      >
                        <Pencil size={13} />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t('{name} 삭제').replace('{name}', item.title)}
                        onClick={() => void remove(item.rowId!)}
                      >
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </>
        )}
      </Modal>
    </>
  )
}

/**
 * Writing one down, or putting it right. Shared by the gallery and the admin
 * screen.
 *
 * `prompt` is the framing the turn carries — it reaches the model above what
 * the person types, and never the composer. The form says so rather than
 * leaving somebody to write a half sentence for a box that no longer fills.
 *
 * Given a `template` it opens on that row's wording and saves over it. The
 * same fields either way — a correction is the same act as writing it, and a
 * separate screen for it would only be this one with the boxes filled in.
 */
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
      <Field label={t('준비물')} hint={t('쉼표로 구분. 고르기 전에 보이는 항목입니다')}>
        <Input
          value={fills}
          onChange={(e) => setFills(e.target.value)}
          placeholder={t('수신처, 제목, 본문 요지')}
          aria-label={t('준비물')}
        />
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
