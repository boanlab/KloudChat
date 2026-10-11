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

/** One thing the menu asks about before a file leaves, and where pressing it goes. */
export interface DownloadCheck {
  label: string
  icon?: ReactNode
  onClick: () => void
}

/**
 * 「⤓ 내려받기 ▾」: every format the artifact leaves as, through the export endpoint. What to
 * look at first (unsupported numbers, a slide that may be cut off) is listed above the formats.
 */
export function DownloadMenu({
  artifact,
  checks,
  checksHeading,
  take,
  extra,
  compact,
  disabled,
  onError,
  align = 'right',
  className,
  label,
}: {
  artifact: Artifact
  /** Accessible name when 「내려받기」 alone would be ambiguous on the page (a card per answer). */
  label?: string
  checks?: DownloadCheck[]
  /** Translated heading over `checks`, e.g. 「내보내기 전 근거 확인 2건」. */
  checksHeading?: string
  /** Replaces the plain download, e.g. a panel that saves pending edits first. */
  take?: (format: ExportFormat) => Promise<void>
  /** Further items under the formats (인쇄). */
  extra?: ReactNode
  /** Icon only; the label stays as the accessible name. */
  compact?: boolean
  disabled?: boolean
  onError?: (message: string) => void
  align?: 'left' | 'right'
  className?: string
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
      align={align}
      className={className}
      trigger={() => (
        <Button
          size="sm"
          disabled={disabled || busy !== null}
          aria-label={label ?? t('내려받기')}
          title={t('파일로 내려받기')}
          className={cn(compact && 'size-8 px-0 max-sm:size-10')}
        >
          {busy ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
          {!compact && (
            <>
              {t('내려받기')}
              <ChevronDown size={13} className="text-muted" />
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

/** 「이걸로 새로 시작」 and 「새 대화에 첨부」 as menu items, for any ⋯ menu that holds an artifact. */
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
        // The conversation that made it, as an agent's form to review; nothing is saved yet.
        <MenuItem icon={<Bot size={14} />} onClick={() => navigate(`/customize/agents/new?from=session:${artifact.sessionId}`)}>
          {t('에이전트로 저장')}
        </MenuItem>
      )}
    </>
  )
}
