import { Sparkles, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Dropdown, MenuLabel } from '@/components/ui'
import { artifactsApi, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact, Critique, LintFinding } from '@/types'
import { useT } from '@/lib/useT'

function Finding({ finding }: { finding: LintFinding }) {
  return (
    <li className="flex gap-2 rounded-control px-1.5 py-1.5 text-sm">
      <span
        className={cn(
          'mt-0.5 h-fit shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium',
          finding.severity === 'P0' ? 'bg-warn/10 text-warn' : 'bg-elevated text-faint',
        )}
      >
        {finding.severity}
      </span>
      <span className="min-w-0">
        {finding.where && <span className="block text-xs text-faint">{finding.where}</span>}
        {finding.message}
      </span>
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
 * and a list they can act on.
 *
 * The count is the `P0` one when there are any, because "two things are wrong"
 * and "five things could read better" are different sentences and only the
 * first should look urgent.
 */
export function LintFindings({
  findings,
  artifact,
}: {
  findings?: LintFinding[]
  /** Given, the review can be asked for and shown. Omitted, this is lint only. */
  artifact?: Artifact
}) {
  const t = useT()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)

  const lint = findings ?? []
  const critique: Critique | undefined = artifact?.critique
  const all = [...lint, ...(critique?.findings ?? [])]
  const reviewable = Boolean(artifact)
  if (all.length === 0 && !reviewable) return null

  const wrong = all.filter((f) => f.severity === 'P0')
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
        {lint.length > 0 && (
          <>
            <MenuLabel>{t('자동 검사')}</MenuLabel>
            <ul className="max-h-48 overflow-auto px-1">
              {lint.map((finding, index) => (
                <Finding key={`lint-${finding.rule}-${index}`} finding={finding} />
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
                    <Finding key={`critique-${index}`} finding={finding} />
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
