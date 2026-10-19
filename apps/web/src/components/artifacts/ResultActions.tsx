import { Bot, ChevronDown, Download, FilePlus2, Loader2, Paperclip, TriangleAlert } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Dropdown, MenuItem, MenuLabel } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { reusable, useReuseArtifact } from '@/lib/reuse'
import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'
import { useStore } from '@/store/useStore'
import type { Artifact } from '@/types'
import { exportChoices, takeArtifact, type ExportFormat } from './artifactExport'

/** Pre-export warning and where pressing it goes. */
export interface DownloadCheck {
  label: string
  icon?: ReactNode
  onClick: () => void
}

/** 「⤓ 내려받기 ▾」: every export format, warnings (unsupported numbers, cut-off slide) above. */
export function DownloadMenu({
  artifact,
  checks,
  checksHeading,
  take,
  extra,
  compact,
  disabled,
  onError,
  label,
}: {
  artifact: Artifact
  /** Accessible name when 「내려받기」 is ambiguous (a card per answer). */
  label?: string
  checks?: DownloadCheck[]
  /** Heading over `checks`, e.g. 「내보내기 전 근거 확인 2건」. */
  checksHeading?: string
  /** Replaces the plain download (e.g. save pending edits first). */
  take?: (format: ExportFormat) => Promise<void>
  /** Extra items under the formats (인쇄). */
  extra?: ReactNode
  /** Icon only; label kept as accessible name. */
  compact?: boolean
  disabled?: boolean
  onError?: (message: string) => void
}) {
  const t = useT()
  const setNotice = useStore((s) => s.setNotice)
  const templateKind = useStore((s) =>
    'templateId' in artifact && artifact.templateId
      ? s.designTemplates.find((row) => row.id === artifact.templateId)?.kind
      : undefined,
  )
  const [busy, setBusy] = useState<ExportFormat | null>(null)
  const choices = exportChoices(artifact, templateKind)
  if (choices.length === 0) return null

  const run = async (format: ExportFormat) => {
    setBusy(format)
    try {
      await (take ? take(format) : takeArtifact(artifact, format))
    } catch (err) {
      const message = errorMessage(err, t('내보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.'))
      if (onError) onError(message)
      else setNotice(message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dropdown
      align="right"
      trigger={() => (
        <Button
          size="sm"
          disabled={disabled || busy !== null}
          aria-label={label ?? t('내려받기')}
          title={t('파일로 내려받기')}
          data-labelled={compact ? undefined : ''}
          className={cn(compact && 'size-8 px-0 max-sm:size-10')}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
          {!compact && (
            <>
              {t('내려받기')}
              <ChevronDown size={13} className="text-muted max-sm:hidden" />
            </>
          )}
        </Button>
      )}
    >
      {checks && checks.length > 0 && (
        <>
          <MenuLabel>{checksHeading ?? t('내보내기 전 확인')}</MenuLabel>
          {checks.map((check) => (
            <MenuItem key={check.label} icon={check.icon ?? <TriangleAlert size={14} />} onClick={check.onClick}>
              {check.label}
            </MenuItem>
          ))}
        </>
      )}
      <MenuLabel>{t('형식 선택')}</MenuLabel>
      {choices.map((choice) => (
        <MenuItem key={choice.format} hint={choice.hint || undefined} onClick={() => void run(choice.format)}>
          {t(choice.label)}
        </MenuItem>
      ))}
      {extra}
    </Dropdown>
  )
}

/** 「이걸로 새로 시작」 and 「새 대화에 첨부」 for any artifact ⋯ menu. */
export function ReuseItems({ artifact }: { artifact: Artifact }) {
  const t = useT()
  const navigate = useNavigate()
  const reuse = useReuseArtifact()
  if (!reusable(artifact)) return null
  return (
    <>
      <MenuItem icon={<FilePlus2 size={14} />} onClick={() => void reuse(artifact, 'restart')}>
        {t('이걸로 새로 시작')}
      </MenuItem>
      <MenuItem icon={<Paperclip size={14} />} onClick={() => void reuse(artifact, 'attach')}>
        {t('새 대화에 첨부')}
      </MenuItem>
      {artifact.sessionId && (
        // The conversation as an agent form to review; nothing saved yet.
        <MenuItem icon={<Bot size={14} />} onClick={() => navigate(`/customize/agents/new?from=session:${artifact.sessionId}`)}>
          {t('에이전트로 저장')}
        </MenuItem>
      )}
    </>
  )
}
