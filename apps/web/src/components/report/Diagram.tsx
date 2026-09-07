import { useEffect, useRef, useState } from 'react'
import { artifactsApi } from '@/lib/api'
import { FRAMES, drawIntoFitting, frameElement, rasterise, theme } from '@/lib/mermaid'
import { useT } from '@/lib/useT'

/**
 * Renders a mermaid diagram and posts the rasterised PNG back for the
 * exporters (the server has no browser). A render failure shows the source
 * and stores nothing.
 */
export function Diagram({
  source,
  artifactId,
  sectionId,
  diagramKey,
  stored,
}: {
  source: string
  /** Absent while streaming; nothing to store onto yet. */
  artifactId?: string
  sectionId?: string
  /** `report_export.diagram_key` of this source. */
  diagramKey?: string
  /** Picture already stored on the section. */
  stored?: string
}) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    const node = host.current
    if (!node || stored) return
    // A new source is a new attempt; a streamed fragment may have failed before.
    setFailed(false)
    if (!source.trim()) return

    void (async () => {
      try {
        const drawn = await drawIntoFitting(node, source, theme(node), FRAMES.page.aspect)
        if (!live || !node || !drawn) {
          if (live) setFailed(true)
          return
        }
        // Every figure on a page has the same 4:3 footprint; the frame is shown and stored.
        const frame = frameElement(drawn, FRAMES.page.aspect, FRAMES.page.width)
        const picture = new XMLSerializer().serializeToString(frame)
        frame.removeAttribute('width')
        frame.removeAttribute('height')
        frame.style.width = '100%'
        frame.style.height = 'auto'
        node.replaceChildren(frame)
        if (artifactId && sectionId && diagramKey) {
          const png = await rasterise(picture, 1)
          if (png && live) {
            await artifactsApi
              .storeDiagram(artifactId, sectionId, diagramKey, png)
              .catch(() => undefined)
          }
        }
      } catch {
        if (live) setFailed(true)
      }
    })()

    return () => {
      live = false
    }
  }, [source, artifactId, sectionId, diagramKey, stored])

  if (stored) {
    return <img src={stored} alt="" className="mx-auto my-5 block max-w-full" />
  }
  if (failed) {
    return (
      <figure>
        <pre className="overflow-x-auto rounded-card bg-elevated p-3 text-sm">
          <code>{source}</code>
        </pre>
        <figcaption className="mt-1 text-xs text-danger">
          {t('이 다이어그램을 그리지 못했습니다.')}
        </figcaption>
      </figure>
    )
  }
  return <div ref={host} className="my-5 flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" />
}
