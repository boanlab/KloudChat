import { CircleAlert, FileText, Globe, Loader2, Paperclip, RefreshCw, Trash2, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Field, Input } from '@/components/ui'
import { agentsApi, downloadFile, errorMessage, type FileRow } from '@/lib/api'
import { useFileDrop } from '@/lib/useFileDrop'
import { useT } from '@/lib/useT'

/**
 * Documents an agent searches via tool call (unlike project files, which are
 * pushed into every turn). Attaching requires a saved agent id.
 */
export function AgentKnowledge({
  agentId,
  onKnowledgeChange,
}: {
  agentId: string | null
  onKnowledgeChange?: (available: boolean) => void
}) {
  const t = useT()
  const [rows, setRows] = useState<FileRow[]>([])
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<'file' | 'url' | 'reindex' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const rowsRef = useRef<FileRow[]>([])
  // The drop hook must run above the unsaved-agent early return; the uploader
  // it calls is defined below it, so a ref bridges the two.
  const addFilesRef = useRef<(files: File[]) => void>(() => {})
  const { over: dragging, handlers: dropHandlers } = useFileDrop(
    (files) => addFilesRef.current(files),
    !!agentId,
  )

  const replaceRows = useCallback(
    (next: FileRow[]) => {
      rowsRef.current = next
      setRows(next)
      onKnowledgeChange?.(next.some((row) => !row.error && row.tokens > 0))
    },
    [onKnowledgeChange],
  )

  useEffect(() => {
    if (!agentId) {
      rowsRef.current = []
      setRows([])
      return
    }
    let live = true
    void agentsApi.knowledge
      .list(agentId)
      .then((r) => live && replaceRows(r))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [agentId, replaceRows])

  if (!agentId) {
    return (
      <Field label={t('자료')} hint={t('에이전트를 저장하면 자료를 붙일 수 있습니다')}>
        <p className="text-base text-faint">
          {t('파일이나 URL 을 붙이면, 이 에이전트가 그 안에서 찾아 답합니다.')}
        </p>
      </Field>
    )
  }

  const addFile = async (picked: File) => {
    setBusy('file')
    setError(null)
    try {
      const row = await agentsApi.knowledge.upload(agentId, picked)
      replaceRows([row, ...rowsRef.current])
    } catch (err) {
      setError(errorMessage(err, t('파일을 올리지 못했습니다.')))
    } finally {
      setBusy(null)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  // Sequential so rows keep drop order.
  const addFiles = async (picked: File[]) => {
    for (const file of picked) await addFile(file)
  }
  addFilesRef.current = addFiles

  const addUrl = async () => {
    setBusy('url')
    setError(null)
    try {
      const row = await agentsApi.knowledge.addUrl(agentId, url.trim())
      replaceRows([row, ...rowsRef.current])
      setUrl('')
    } catch (err) {
      setError(errorMessage(err, t('페이지를 읽지 못했습니다.')))
    } finally {
      setBusy(null)
    }
  }

  const reindex = async () => {
    setBusy('reindex')
    setError(null)
    try {
      // Force all rows, so vectors from a previous embedding model are rebuilt too.
      await agentsApi.knowledge.reindex(agentId, true)
      replaceRows(await agentsApi.knowledge.list(agentId))
    } catch (err) {
      setError(errorMessage(err, t('색인하지 못했습니다.')))
    } finally {
      setBusy(null)
    }
  }

  const openFile = async (id: string, name: string) => {
    setError(null)
    try {
      await downloadFile(id, name)
    } catch (err) {
      setError(errorMessage(err, t('파일을 내려받지 못했습니다.')))
    }
  }

  const remove = async (id: string) => {
    replaceRows(rowsRef.current.filter((f) => f.id !== id))
    try {
      await agentsApi.knowledge.remove(agentId, id)
    } catch {
      replaceRows(await agentsApi.knowledge.list(agentId).catch(() => []))
    }
  }

  return (
    <Field
      label={t('자료')}
      hint={t('이 에이전트가 검색해서 근거로 쓸 문서입니다. 대화마다 통째로 들어가지 않고, 필요할 때 찾아 씁니다.')}
    >
      <div className="relative space-y-2" {...dropHandlers}>
        {dragging && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-card border-2 border-dashed border-accent bg-accent-soft/90 text-base font-medium text-accent">
            {t('여기에 놓으면 자료로 추가됩니다')}
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          <input
            ref={fileInput}
            type="file"
            multiple
            aria-label={t('자료 파일')}
            className="block text-base file:mr-3 file:rounded-control file:border file:border-line file:bg-elevated file:px-3 file:py-1.5 file:text-base"
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? [])
              if (picked.length) void addFiles(picked)
            }}
          />
          {busy === 'file' && <Loader2 size={14} className="animate-spin self-center text-faint" />}
        </div>

        <div className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && url.trim()) {
                e.preventDefault()
                void addUrl()
              }
            }}
            placeholder="https://…"
            aria-label={t('자료 URL')}
            className="font-mono text-sm"
          />
          <Button disabled={busy !== null || !url.trim()} onClick={() => void addUrl()}>
            {busy === 'url' ? <Loader2 size={13} className="animate-spin" /> : <Globe size={13} />}
            {t('URL 추가')}
          </Button>
        </div>

        {error && <p className="text-base text-danger">{error}</p>}

        {rows.length === 0 ? (
          <p className="text-base text-faint">{t('아직 붙인 자료가 없습니다.')}</p>
        ) : (
          <ul className="space-y-1">
            {rows.map((f) => (
              <li
                key={f.id}
                className="flex items-center gap-2 rounded-control border border-line bg-panel px-2.5 py-1.5 text-base"
              >
                {f.sourceUrl ? (
                  <Globe size={13} className="shrink-0 text-faint" />
                ) : (
                  <FileText size={13} className="shrink-0 text-faint" />
                )}
                {/* A URL row has no blob; its name links to the source page. */}
                {f.sourceUrl ? (
                  <a
                    href={f.sourceUrl}
                    target="_blank"
                    rel="noreferrer"
                    title={f.sourceUrl}
                    className="min-w-0 flex-1 truncate text-accent hover:underline"
                  >
                    {f.name}
                  </a>
                ) : (
                  <button
                    onClick={() => void openFile(f.id, f.name)}
                    title={t('원본 파일을 내려받습니다')}
                    className="min-w-0 flex-1 truncate text-left text-accent hover:underline"
                  >
                    {f.name}
                  </button>
                )}
                {f.error ? (
                  <span className="flex items-center gap-1 text-xs text-warn">
                    <TriangleAlert size={11} />
                    {t('읽지 못함')}
                  </span>
                ) : (
                  <>
                    {/* Un-indexed rows are found by word search only. */}
                    {f.indexed === false && (
                      <span
                        title={t('낱말 검색으로는 찾지만, 뜻으로 찾는 검색에는 아직 안 들어갔습니다')}
                        className="flex shrink-0 items-center gap-1 text-xs text-faint"
                      >
                        <CircleAlert size={11} />
                        {t('색인 안 됨')}
                      </span>
                    )}
                    <span className="shrink-0 text-xs text-faint tabular-nums">
                      {t('{n} 토큰').replace('{n}', f.tokens.toLocaleString())}
                    </span>
                  </>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t('{name} 삭제').replace('{name}', f.name)}
                  onClick={() => void remove(f.id)}
                >
                  <Trash2 size={13} />
                </Button>
              </li>
            ))}
          </ul>
        )}
        {rows.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <p className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-faint">
              <Paperclip size={10} />
              {t('URL 은 추가한 시점의 내용을 저장합니다. 페이지가 바뀌어도 따라가지 않습니다.')}
            </p>
            {rows.some((f) => f.indexed === false && !f.error) && (
              <Button size="sm" disabled={busy !== null} onClick={() => void reindex()}>
                {busy === 'reindex' ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <RefreshCw size={13} />
                )}
                {t('색인하기')}
              </Button>
            )}
          </div>
        )}
      </div>
    </Field>
  )
}
