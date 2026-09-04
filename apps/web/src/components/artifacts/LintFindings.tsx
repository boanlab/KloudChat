import { Check, Loader2, Sparkles, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Dropdown, MenuLabel } from '@/components/ui'
import { artifactsApi, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact, Critique, LintFinding } from '@/types'
import { useT } from '@/lib/useT'

/**
 * Groups findings by `where` so each part is rewritten once with all its findings.
 * Findings with no location land under `''`.
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

/** One fix instruction naming every finding for a part. */
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
 * Lint findings plus the on-demand model review, as a badge and a list.
 * The badge counts P0 findings when there are any.
 */
export function LintFindings({
  findings,
  artifact,
  onFix,
  onFixAll,
}: {
  findings?: LintFinding[]
  /** Enables the model review; omitted, this shows lint only. */
  artifact?: Artifact
  /** Sends one finding to the surface's revision path; omitted where there is none. */
  onFix?: (finding: LintFinding) => Promise<void>
  /** Fixes all findings, one instruction per part (see `byWhere`). */
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
      // Also written onto the prop: the artifacts screen renders a copy, not the store row.
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
      // Opens rightward: leftward, the 320px list would be clipped by the panel.
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
        {/* Above the list so it stays visible when the list scrolls. */}
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
