import { useRef, useState } from 'react'
import { SlidesEditor, type SlidesApi } from '@/components/pptx/SlidesEditor'
import type { OfficePreviewProps } from './officePreview'

const noop = () => undefined

/** A `.pptx` in the PowerPoint editor's slide canvas and thumbnails, read only: no toolbar. */
export default function PptxPreview({ bytes, label, onReady, onFail }: OfficePreviewProps) {
  const api = useRef<SlidesApi | null>(null)
  const [content] = useState(() => new Uint8Array(bytes))
  return (
    <div className="absolute inset-0" data-pptx-preview="">
      <SlidesEditor
        content={content}
        version={1}
        canEdit={false}
        label={label}
        apiRef={api}
        onDirty={noop}
        onReady={onReady}
        onError={onFail}
      />
    </div>
  )
}
