import { CircleAlert, FileText, Globe, Loader2, Paperclip, RefreshCw, Trash2, TriangleAlert } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, Field, Input } from '@/components/ui'
import { agentsApi, downloadFile, errorMessage, type FileRow } from '@/lib/api'
import { useFileDrop } from '@/lib/useFileDrop'
import { isReading, ocrNote, useOcrProgress } from '@/lib/ocr'
import { useT } from '@/lib/useT'

/** Documents the agent searches by tool call (project files go into every turn); needs a saved agent. */
export function AgentKnowledge({
  agentId,
  onKnowledgeChange,
  onNeedAgent,
}: {
  agentId: string | null
  onKnowledgeChange?: (available: boolean) => void
  /** Saves an unsaved agent for its first file; resolves to its id, or null. */
  onNeedAgent?: () => Promise<string | null>
}) {
  const t = useT()
  const [rows, setRows] = useState<FileRow[]>([])
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<'file' | 'url' | 'reindex' | null>(null)
  const [error, setError] = useState<string | null>(null)
  /** Shown once the first file saved the agent. */
  const [savedNote, setSavedNote] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const rowsRef = useRef<FileRow[]>([])
  // Drop hook above the early return; its uploader below, bridged by a ref.
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

  // Scanned document read in the background; row follows the pages.
  useOcrProgress(rows, (row) =>
    replaceRows(rowsRef.current.map((item) => (item.id === row.id ? row : item))),
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
    if (onNeedAgent) {
      // First file saves the agent, then attaches; saved id re-renders.
      const attachFirst = async (picked: File) => {
        setBusy('file')
        setError(null)
        try {
          const id = await onNeedAgent()
          if (id) {
            await agentsApi.knowledge.upload(id, picked)
            setSavedNote(true)
          }
        } catch (err) {
          setError(errorMessage(err, t('파일을 올리지 못했습니다.')))
        } finally {
          setBusy(null)
          if (fileInput.current) fileInput.current.value = ''
        }
      }
      return (
        <div className="space-y-2">
          <p className="text-sm text-faint">
            {t('파일을 붙이면 에이전트를 먼저 저장합니다. 이 에이전트가 그 안에서 찾아 답합니다.')}
          </p>
          <input
            ref={fileInput}
            type="file"
            className="hidden"
            aria-label={t('자료 파일')}
            onChange={(e) => {
              const picked = e.target.files?.[0]
              if (picked) void attachFirst(picked)
            }}
          />
          <Button size="sm" disabled={busy !== null} onClick={() => fileInput.current?.click()}>
            {busy === 'file' ? <Loader2 size={13} className="animate-spin" /> : <Paperclip size={13} />}
            {t('파일 붙이기')}
          </Button>
          {error && <p role="status" className="text-sm text-danger">{error}</p>}
        </div>
      )
    }
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

  // Sequential: rows keep drop order.
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
      // All rows: rebuilds vectors from an older embedding model.
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
      hint={t('이 에이전트가 근거로 쓸 문서입니다. 질문마다 먼저 이 자료에서 찾아 답합니다. 짧은 자료는 통째로, 긴 자료는 질문과 맞는 대목만 들어갑니다.')}
    >
      <div className="relative space-y-2" {...dropHandlers}>
        {savedNote && (
          <p role="status" className="text-sm text-muted" data-testid="agent-saved-for-file">
            {t('자료를 붙이려고 에이전트를 저장했습니다. 이 화면을 떠나도 에이전트는 남습니다.')}
          </p>
        )}
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
                {/* URL row: no blob; name links to the source. */}
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
                    {(() => {
                      const reading = ocrNote(f, t)
                      return reading ? (
                        <span
                          data-ocr={f.ocrState ?? undefined}
                          className={
                            reading.tone === 'warn'
                              ? 'flex shrink-0 items-center gap-1 text-xs text-warn'
                              : 'flex shrink-0 items-center gap-1 text-xs text-faint'
                          }
                        >
                          {isReading(f) && <Loader2 size={11} className="animate-spin" />}
                          {reading.text}
                        </span>
                      ) : null
                    })()}
                    {/* Un-indexed rows: word search only. */}
                    {f.indexed === false && !isReading(f) && (
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
