import { useEffect, useRef, useState } from 'react'
import { artifactsApi } from '@/lib/api'
import { draw, rasterise, theme } from '@/lib/mermaid'
import { useT } from '@/lib/useT'

/**
 * A mermaid diagram, drawn here and kept as a picture.
 *
 * Mermaid is a JavaScript renderer and the API image has no headless browser —
 * `report_export` chose reportlab over an HTML engine on purpose. So a diagram
 * written into a report showed as a block of source everywhere: in the panel,
 * and then in the `.docx` somebody submitted.
 *
 * The browser is the only thing in this system that can draw one, so it does
 * both jobs. It renders the diagram for the reader, and the moment it has, it
 * rasterises what it drew and posts it back — after which the three exporters
 * have a real figure to place. Nobody is asked to do anything: a diagram
 * becomes a picture by being looked at.
 *
 * Three things this is careful about:
 *
 * * **It stores once.** The server compares before writing and answers
 *   unchanged, so a document with ten readers is written to once.
 * * **It takes no version.** Opening a document is not editing it, and a
 *   version per reader would bury the edits somebody actually made.
 * * **A failure is silent to the file and loud to the reader.** A diagram the
 *   renderer rejects shows its source, which is what the writer can fix; it
 *   does not post a broken picture that would then be in every export.
 */
export function Diagram({
  source,
  artifactId,
  sectionId,
  diagramKey,
  stored,
}: {
  source: string
  /** Absent while a document is still streaming — nothing to store onto yet. */
  artifactId?: string
  sectionId?: string
  /** `report_export.diagram_key` of this source, computed by the caller. */
  diagramKey?: string
  /** The picture already on the section, if a reader has been here before. */
  stored?: string
}) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    const node = host.current
    if (!node || stored) return
    /*
     * A new source is a new attempt, and the last one's verdict does not carry.
     * `failed` was set and never cleared: a diagram in a streaming report
     * mounts on the first token with a fragment of a source, that draw fails,
     * and the finished diagram arriving a second later was drawn into a
     * component that had already decided to show its source instead.
     */
    setFailed(false)
    // Nothing written yet is not a failure. Most of what a streamed source is
    // on the way to being does not parse, and an empty one never does.
    if (!source.trim()) return

    void (async () => {
      try {
        const svg = await draw(source, theme(node))
        if (!live || !node || !svg) {
          if (live) setFailed(true)
          return
        }
        node.innerHTML = svg
        fill(node)
        if (artifactId && sectionId && diagramKey) {
          const png = await rasterise(svg)
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
    // Already drawn once, by whoever opened this before. Shown as the picture
    // the file carries, so the screen and the export agree.
    return <img src={stored} alt="" className="mx-auto my-5 block max-w-full" />
  }
  if (failed) {
    // The source, which is what the writer can act on. A diagram that will not
    // render is a mistake in the text, and hiding it hides the mistake.
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
  // Centred, with the space a figure gets in the templates — the seeds put
  // the same margin around `<figure>`.
  return <div ref={host} className="my-5 flex justify-center [&_svg]:h-auto [&_svg]:max-w-full" />
}

/**
 * How tall a figure may get while being widened to fill the column.
 *
 * Roughly a third of an A4 text block. Past that a figure stops being
 * something the eye takes in beside the prose and becomes a page of its own.
 */
const MAX_GROWN_HEIGHT = 420

/**
 * Let a narrow diagram fill the column, unless doing so makes it a page tall.
 *
 * `useMaxWidth` caps a diagram at its *intrinsic* width, so it never grows —
 * a figure mermaid happened to lay out at 380px sits in a 700px column with a
 * band of white down each side, and reads as a mistake rather than as a
 * choice. Growing it is the obvious fix and is only right some of the time:
 * scaling a tall narrow graph to the full column multiplies its height by the
 * same factor, and a figure that was merely too narrow becomes one that runs
 * off the bottom of the page.
 *
 * So the test is what the height *would become*. Widening is free for a flat
 * diagram and refused for a tall one — which leaves the margins on exactly the
 * figures whose real problem is their shape, and those are fixed by writing
 * them differently rather than by scaling them.
 */
function fill(node: HTMLElement) {
  const svg = node.querySelector('svg')
  if (!svg) return
  const box = svg.viewBox?.baseVal
  const width = box?.width || svg.getBoundingClientRect().width
  const height = box?.height || svg.getBoundingClientRect().height
  const column = node.getBoundingClientRect().width
  if (!width || !height || !column || column <= width) return
  if ((column * height) / width > MAX_GROWN_HEIGHT) return
  svg.style.maxWidth = 'none'
  svg.style.width = '100%'
}
