import { Loader2 } from 'lucide-react'
import { lazy, Suspense, useState, type ComponentType, type LazyExoticComponent } from 'react'
import { LoadingState } from '@/components/ui'
import type { OfficeKind } from '@/lib/api'
import { useT } from '@/lib/useT'
import type { OfficePreviewProps } from './officePreview'

// Each editor is its own chunk, loaded only when a file of its kind is shown.
export const PREVIEWS: Record<OfficeKind, LazyExoticComponent<ComponentType<OfficePreviewProps>>> = {
  hwp: lazy(() => import('./HwpPreview')),
  docx: lazy(() => import('./DocxPreview')),
  pptx: lazy(() => import('./PptxPreview')),
}

/** What each kind's read-only view is called, for assistive technology. */
export const PREVIEW_LABEL: Record<OfficeKind, string> = {
  hwp: '한글 문서 미리보기',
  docx: 'Word 문서 미리보기',
  pptx: 'PowerPoint 미리보기',
}

/**
 * An office file (HWP, Word, PowerPoint) drawn read only by its editor, as the file box
 * previews one: for a shared document and the artifacts page. `onFail` when the editor
 * cannot draw it (the caller shows its text instead). Fills its parent, which is positioned.
 */
export function OfficeView({
  kind, bytes, name, onFail,
}: {
  kind: OfficeKind
  bytes: ArrayBuffer
  name: string
  onFail: (reason: string) => void
}) {
  const t = useT()
  const [drawn, setDrawn] = useState(false)
  const Preview = PREVIEWS[kind]
  return (
    <div className="absolute inset-0" data-office-view={kind} data-preview-ready={String(drawn)}>
      <Suspense fallback={<LoadingState />}>
        <Preview
          bytes={bytes}
          name={name}
          label={t(PREVIEW_LABEL[kind])}
          onReady={() => setDrawn(true)}
          onFail={onFail}
        />
        {!drawn && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-panel/70 text-sm text-muted">
            <span className="flex items-center gap-2">
              <Loader2 size={15} className="animate-spin" />
              {t('문서를 여는 중…')}
            </span>
          </div>
        )}
      </Suspense>
    </div>
  )
}
