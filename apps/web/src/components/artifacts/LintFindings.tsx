import { Check, Loader2, Sparkles, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Dropdown, MenuLabel } from '@/components/ui'
import { artifactsApi, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact, Critique, LintFinding } from '@/types'
import { useT } from '@/lib/useT'

/**
 * The findings of one pass, gathered under the part each one is about.
 *
 * Exported because both surfaces need it and both would otherwise get it
 * subtly wrong. Fixing findings one at a time rewrites the same section once
 * per finding, and a rewrite works on the text the *last* rewrite produced —
 * so the second one is asked to fix a problem in a passage that no longer
 * exists, and routinely undoes the first. Three findings about one paragraph
 * is one instruction naming three things, not three instructions.
 *
 * Keyed by `where`, which is what a finding says about its own location. The
 * ones that name nothing are gathered under `''` and belong to the document as
 * a whole; the caller decides what to do with those.
 */
export function byWhere(findings: LintFinding[]): Map<string, LintFinding[]> {
  const groups = new Map<string, LintFinding[]>()
  for (const finding of findings) {
    const key = finding.where ?? ''
    const held = groups.get(key)
    if (held) held.push(finding)
    else groups.set(key, [finding])
  }
  return groups
}

/** The instruction for one part, naming everything found in it. */
export function fixNote(findings: LintFinding[], one: string, many: string): string {
  if (findings.length === 1) return one.replace('{message}', findings[0].message)
  return many.replace(
    '{list}',
    findings.map((f, i) => `${i + 1}. ${f.message}`).join('\n'),
  )
}

function Finding({ finding, onFix }: { finding: LintFinding; onFix?: () => Promise<void> }) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  const fix = async () => {
    if (!onFix) return
    setBusy(true)
    setFailed(null)
    try {
      await onFix()
      setDone(true)
    } catch (err) {
      setFailed(errorMessage(err, t('고치지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="group flex gap-2 rounded-control px-1.5 py-1.5 text-sm hover:bg-elevated">
      <span
        className={cn(
          'mt-0.5 h-fit shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium',
          finding.severity === 'P0' ? 'bg-warn/10 text-warn' : 'bg-elevated text-faint',
        )}
      >
        {finding.severity}
      </span>
      <span className="min-w-0 flex-1">
        {finding.where && <span className="block text-xs text-faint">{finding.where}</span>}
        {finding.message}
        {failed && <span className="mt-0.5 block text-xs text-danger">{failed}</span>}
      </span>
      {/* A list of problems with no way to act on one puts the whole job back
          on the reader: find the passage, work out what it should say, type it.
          The finding already knows which section and what is wrong with it, and
          the document already rewrites a section on request — this is the wire
          between the two.

          It rewrites rather than asking the chat to. A sentence sent to the
          conversation looks like an action and is a request: the reader still
          has to watch for a reply and work out whether anything changed. The
          rewrite path edits the document, keeps a snapshot, and is one press of
          되돌리기 from undone. */}
      {onFix &&
        (done ? (
          <Check size={13} className="mt-1 shrink-0 text-success" />
        ) : (
          <button
            type="button"
            onClick={() => void fix()}
            disabled={busy}
            className={cn(
              'mt-0.5 h-fit shrink-0 rounded-control px-1.5 py-0.5 text-xs transition-opacity',
              busy
                ? 'text-faint opacity-100'
                : 'text-muted opacity-0 hover:text-fg focus-visible:opacity-100 group-hover:opacity-100',
            )}
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : t('고치기')}
          </button>
        ))}
    </li>
  )
}

/**
 * Everything worth looking at before this document goes anywhere.
 *
 * Two sources, one list. The linter is free and certain — it ran when the
 * document was written and found what it found. The review costs a model call
 * and is an opinion, so it is asked for rather than run, and its score is a
 * reading rather than a gate: nothing here blocks anything.
 *
 * A badge and a list rather than markers threaded through the text: nothing is
 * corrected automatically, so what the reader needs is a count they can ignore
 * and a list they can act on — literally, where the surface has somewhere to
 * send a correction. 다시 검토 was the only button here for a while, which made
 * the panel a place to be told about problems twice rather than once.
 *
 * The count is the `P0` one when there are any, because "two things are wrong"
 * and "five things could read better" are different sentences and only the
 * first should look urgent.
 */
export function LintFindings({
  findings,
  artifact,
  onFix,
  onFixAll,
}: {
  findings?: LintFinding[]
  /** Given, the review can be asked for and shown. Omitted, this is lint only. */
  artifact?: Artifact
  /**
   * Hands one finding back as an instruction to fix it.
   *
   * Absent on surfaces with no revision path of their own, where the list is
   * still worth reading — the button simply does not appear rather than
   * appearing and doing nothing.
   */
  onFix?: (finding: LintFinding) => Promise<void>
  /**
   * Fixes everything in the list at once.
   *
   * Separate from `onFix` rather than a loop over it, because the two are not
   * the same job: a loop rewrites one part once per finding about it, and each
   * rewrite lands on the text the last one produced. The surface groups them
   * and sends one instruction per part — see `byWhere`.
   */
  onFixAll?: (findings: LintFinding[]) => Promise<void>
}) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fixingAll, setFixingAll] = useState(false)
  const [fixAllError, setFixAllError] = useState<string | null>(null)
  const [fixedAll, setFixedAll] = useState(false)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const lint = findings ?? []
  const critique: Critique | undefined = artifact?.critique
  const all = [...lint, ...(critique?.findings ?? [])]
  const reviewable = Boolean(artifact)
  if (all.length === 0 && !reviewable) return null

  const wrong = all.filter((f) => f.severity === 'P0')
  const fixAll = async () => {
    if (!onFixAll) return
    setFixingAll(true)
    setFixAllError(null)
    try {
      await onFixAll(all)
      setFixedAll(true)
    } catch (err) {
      setFixAllError(errorMessage(err, t('고치지 못했습니다.')))
    } finally {
      setFixingAll(false)
    }
  }
  const run = async () => {
    if (!artifact) return
    setBusy(true)
    setError(null)
    try {
      const row = await artifactsApi.critique(artifact.id)
      // Written onto the artifact this panel was handed, not only into the
      // store: the artifacts screen opens its modal on a copy it took when the
      // card was clicked, so a store refresh alone leaves the review invisible
      // exactly where it was asked for. Same move the report panel makes after
      // a section rewrite.
      artifact.critique = (row.data as { critique?: Critique } | null)?.critique
      void loadArtifacts()
    } catch (err) {
      setError(errorMessage(err, t('검토하지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dropdown
      // Opens rightward, into the room that exists. The badge sits early in a
      // toolbar packed against the right edge, so there is always half a panel
      // to its right and only a couple of hundred pixels to its left — and a
      // 320px list opening leftward hangs off the panel, where the panel's own
      // clipping cuts 검토 받기 in half and nothing can press it.
      align="left"
      trigger={() => (
        <button type="button" aria-label={t('검사 결과')}>
          <Badge tone={wrong.length > 0 ? 'warn' : undefined}>
            {wrong.length > 0 ? <TriangleAlert size={10} /> : <Sparkles size={10} />}
            {wrong.length > 0
              ? t('고칠 곳 {n}').replace('{n}', String(wrong.length))
              : all.length > 0
                ? t('볼 곳 {n}').replace('{n}', String(all.length))
                : t('검토')}
          </Badge>
        </button>
      )}
    >
      <div className="w-80">
        {/* 하나씩 누르는 것과 다른 일이다. 지적이 열 줄이면 열 번을 눌러야 하고,
            그 사이 문서는 열 번 다시 쓰인다 — 여기서는 절마다 한 번씩만 쓴다.

            목록 위에 둔다. 아래에 두면 스크롤되는 목록 뒤로 밀려서, 지적이 많을
            때, 즉 이 버튼이 가장 필요할 때 보이지 않는다. */}
        {onFixAll && all.length > 1 && (
          <div className="border-b border-line px-2 py-2">
            {fixedAll ? (
              <p className="flex items-center gap-1.5 px-0.5 text-sm text-success">
                <Check size={13} />
                {t('모두 고쳤습니다.')}
              </p>
            ) : (
              <Button size="sm" onClick={() => void fixAll()} disabled={fixingAll}>
                {fixingAll && <Loader2 size={13} className="animate-spin" />}
                {fixingAll
                  ? t('고치는 중입니다…')
                  : t('모두 고치기 ({n})').replace('{n}', String(all.length))}
              </Button>
            )}
            {fixAllError && <p className="mt-1 px-0.5 text-sm text-danger">{fixAllError}</p>}
          </div>
        )}

        {lint.length > 0 && (
          <>
            <MenuLabel>{t('자동 검사')}</MenuLabel>
            <ul className="max-h-48 overflow-auto px-1">
              {lint.map((finding, index) => (
                <Finding
                  key={`lint-${finding.rule}-${index}`}
                  finding={finding}
                  onFix={onFix && (() => onFix(finding))}
                />
              ))}
            </ul>
          </>
        )}

        {reviewable && (
          <>
            <MenuLabel>
              {critique
                ? `${t('검토')} ${critique.score.toFixed(1)}/10`
                : t('검토')}
            </MenuLabel>
            {critique ? (
              critique.findings.length > 0 ? (
                <ul className="max-h-48 overflow-auto px-1">
                  {critique.findings.map((finding, index) => (
                    <Finding
                      key={`critique-${index}`}
                      finding={finding}
                      onFix={onFix && (() => onFix(finding))}
                    />
                  ))}
                </ul>
              ) : (
                <p className="px-2.5 pb-2 text-sm text-faint">
                  {t('고칠 곳을 찾지 못했습니다.')}
                </p>
              )
            ) : (
              <p className="px-2.5 pb-1.5 text-sm text-faint">
                {t('쓰지 않은 사람의 눈으로 한 번 읽습니다. 모델을 한 번 호출합니다.')}
              </p>
            )}
            <div className="px-2 pt-1 pb-2">
              <Button size="sm" onClick={() => void run()} disabled={busy}>
                {busy ? t('읽는 중입니다…') : critique ? t('다시 검토') : t('검토 받기')}
              </Button>
            </div>
            {error && <p className="px-2.5 pb-2 text-sm text-danger">{error}</p>}
          </>
        )}
      </div>
    </Dropdown>
  )
}
