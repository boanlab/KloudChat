/**
 * Mermaid rendering shared by the web view (draws to show, stores the picture)
 * and the page view (draws off-screen to get the picture). One implementation,
 * so the stored digest matches on both surfaces.
 */

/** The diagram as an SVG string, or `null` if mermaid would not draw it. `look` is the surface's theme. */
export async function draw(source: string, look: object): Promise<string | null> {
  try {
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({
      startOnLoad: false,
      // The source comes from a model through no sanitiser of ours.
      securityLevel: 'strict',
      // Otherwise mermaid appends its own error box to `document.body` on a parse error.
      suppressErrorRendering: true,
      ...look,
    })
    // The counter keeps overlapping draws of the same source from sharing a scratch element.
    draws += 1
    const id = `d${Math.abs(hash(source))}x${draws}`
    try {
      const { svg } = await mermaid.render(id, plain(source))
      return svg
    } finally {
      // Mermaid's scratch element; some versions prefix the id with `d`.
      document.getElementById(id)?.remove()
      document.getElementById(`d${id}`)?.remove()
    }
  } catch {
    return null
  }
}

/**
 * Draws the diagram into `node` and returns the SVG element, or `null` if mermaid would not
 * draw it. Mermaid writes the element itself (`run`), so no string of markup passes through
 * the caller; the caller styles and sizes the element with DOM calls.
 */
export async function drawInto(node: HTMLElement, source: string, look: object): Promise<SVGSVGElement | null> {
  try {
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      suppressErrorRendering: true,
      ...look,
    })
    const easel = document.createElement('div')
    easel.className = 'mermaid'
    easel.textContent = plain(source)
    node.replaceChildren(easel)
    await mermaid.run({ nodes: [easel], suppressErrors: true })
    const svg = easel.querySelector('svg')
    if (!svg) {
      node.replaceChildren()
      return null
    }
    return svg
  } catch {
    node.replaceChildren()
    return null
  }
}

/** `draw`, but a parse failure comes back as mermaid's message so the writer can be asked for a repair. */
export async function drawOrExplain(
  source: string,
  look: object,
): Promise<{ svg: string } | { error: string }> {
  try {
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({ startOnLoad: false, suppressErrorRendering: true, ...look })
    const id = `fig-${Math.random().toString(36).slice(2)}`
    try {
      const { svg } = await mermaid.render(id, plain(source))
      return { svg }
    } finally {
      document.getElementById(id)?.remove()
      document.getElementById(`d${id}`)?.remove()
    }
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) }
  }
}

/** Strips `style`, `classDef` and `linkStyle` lines so the 서식 owns every colour; nodes are untouched. */
function plain(source: string): string {
  return source
    .split('\n')
    .filter((line) => !/^\s*(style|linkStyle|classDef)\s/.test(line))
    .join('\n')
}

/** Render counter, mixed into the id so no two live renders share one. */
let draws = 0

/** Stable enough to name one render. Not the storage key — that is the server's. */
function hash(text: string): number {
  let value = 0
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) | 0
  return value
}

/** The SVG as a 2x PNG `data:` URI, or `null`. The exporters (python-docx, reportlab) place pixels, not SVG. */
export async function rasterise(svg: string, scale = 2): Promise<string | null> {
  try {
    const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    try {
      const image = await new Promise<HTMLImageElement>((resolve, reject) => {
        const element = new Image()
        element.onload = () => resolve(element)
        element.onerror = reject
        element.src = url
      })
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round((image.width || 800) * scale))
      canvas.height = Math.max(1, Math.round((image.height || 400) * scale))
      const context = canvas.getContext('2d')
      if (!context) return null
      // White ground: transparent turns black in a dark-themed .docx viewer.
      context.fillStyle = '#ffffff'
      context.fillRect(0, 0, canvas.width, canvas.height)
      context.drawImage(image, 0, 0, canvas.width, canvas.height)
      return canvas.toDataURL('image/png')
    } finally {
      URL.revokeObjectURL(url)
    }
  } catch {
    return null
  }
}

/**
 * Mermaid theme read off the target element's CSS tokens (`--ink`, `--accent`,
 * `--muted`, `--paper`, `--font-body`), so one function serves the app and a
 * 서식's shadow root. `fontSize` is above reading size because wide diagrams
 * are scaled down to the column.
 */
export function theme(node: HTMLElement) {
  const read = (name: string, fallback: string) =>
    getComputedStyle(node).getPropertyValue(name).trim() || fallback

  const ink = read('--ink', '#1a1a1a')
  const accent = read('--accent', '#5b5bd6')
  const muted = read('--muted', '#666666')
  const paper = read('--paper', '#ffffff')
  // A stack, not `inherit`: an off-screen easel outside the document's styles would fall back to serif.
  const face = read('--font-body', "'Pretendard', 'Malgun Gothic', 'Apple SD Gothic Neo', Arial, sans-serif")

  return {
    theme: 'base' as const,
    fontFamily: face,
    // `<text>`, not `<foreignObject>`: foreign content taints the canvas and
    // `toDataURL` throws in `rasterise`. Top-level; `flowchart.htmlLabels` is deprecated.
    htmlLabels: false,
    themeVariables: {
      // Flat, in the document's ink.
      background: paper,
      primaryColor: paper,
      primaryTextColor: ink,
      primaryBorderColor: accent,
      secondaryColor: paper,
      secondaryTextColor: ink,
      secondaryBorderColor: muted,
      tertiaryColor: paper,
      tertiaryTextColor: ink,
      tertiaryBorderColor: muted,
      lineColor: muted,
      textColor: ink,
      mainBkg: paper,
      nodeBorder: accent,
      clusterBkg: paper,
      clusterBorder: muted,
      edgeLabelBackground: paper,
      fontSize: '18px',
      // Pie slices are read off numbered variables, the xy plot off a comma-joined list.
      ...Object.fromEntries(
        shades(accent, paper, 6).map((colour, i) => [`pie${i + 1}`, colour]),
      ),
      pieOuterStrokeWidth: '1px',
      pieOuterStrokeColor: muted,
      pieSectionTextColor: ink,
      pieTitleTextSize: '17px',
      pieSectionTextSize: '15px',
      pieLegendTextSize: '14px',
      xyChart: {
        backgroundColor: paper,
        titleColor: ink,
        xAxisLabelColor: ink,
        xAxisTitleColor: muted,
        xAxisTickColor: muted,
        xAxisLineColor: muted,
        yAxisLabelColor: ink,
        yAxisTitleColor: muted,
        yAxisTickColor: muted,
        yAxisLineColor: muted,
        plotColorPalette: shades(accent, paper, 4).join(','),
      },
    },
    flowchart: {
      // Fitted to the text column.
      useMaxWidth: true,
      curve: 'linear' as const,
      padding: 12,
      // A subgraph's title sits above its box instead of on the border line.
      subGraphTitleMargin: { top: 10, bottom: 6 },
      // Tight ranks, wide siblings: pushes the graph outward rather than down the page.
      rankSpacing: 30,
      nodeSpacing: 45,
      // A ten-character Korean label stays on one line; a wrapped label makes the whole rank taller.
      wrappingWidth: 320,
    },
    pie: { useMaxWidth: true, textPosition: 0.6 },
    xyChart: {
      useMaxWidth: true,
      width: 760,
      height: 380,
      xAxis: { labelFontSize: 13, titleFontSize: 14 },
      yAxis: { labelFontSize: 13, titleFontSize: 14 },
      chartOrientation: 'vertical' as const,
      plotReservedSpacePercent: 55,
    },
  }
}

/** Chart palette mixed from the one accent toward the paper: slices differ by weight, the document keeps one hue. */
function shades(accent: string, paper: string, count: number): string[] {
  const rgb = (hex: string): [number, number, number] => {
    const clean = hex.replace('#', '').trim()
    const full =
      clean.length === 3
        ? clean
            .split('')
            .map((c) => c + c)
            .join('')
        : clean
    const value = parseInt(full.slice(0, 6), 16)
    return Number.isNaN(value)
      ? [91, 91, 214]
      : [(value >> 16) & 255, (value >> 8) & 255, value & 255]
  }
  const [ar, ag, ab] = rgb(accent)
  const [pr, pg, pb] = rgb(paper)
  const hex = (n: number) => Math.round(Math.min(255, Math.max(0, n))).toString(16).padStart(2, '0')
  return Array.from({ length: count }, (_, i) => {
    // Never all the way to the paper: the last slice must stay visible against it.
    const mix = count === 1 ? 0 : (i / (count - 1)) * 0.7
    return `#${hex(ar + (pr - ar) * mix)}${hex(ag + (pg - ag) * mix)}${hex(ab + (pb - ab) * mix)}`
  })
}

/**
 * Figure theme for a paper, after PaperBanana's NeurIPS 2025 guide: light
 * desaturated pastel zones, rounded nodes, thin uniform strokes, one highlight
 * colour. `hot` is the one class the prompt may write (`node:::hot`).
 */
export function paperTheme(node: HTMLElement) {
  const base = theme(node)
  const read = (name: string, fallback: string) =>
    getComputedStyle(node).getPropertyValue(name).trim() || fallback
  const accent = read('--accent', '#5b5bd6')
  const ink = read('--ink', '#1a1a1a')
  return {
    ...base,
    fontFamily: "'Pretendard', 'Inter', 'Helvetica Neue', Arial, sans-serif",
    themeVariables: {
      ...base.themeVariables,
      // Zones in light ice, nodes in light grey-blue with mid-saturation borders.
      clusterBkg: '#eef4fb',
      clusterBorder: '#b7c7de',
      mainBkg: '#f7f9fc',
      primaryColor: '#f7f9fc',
      primaryBorderColor: '#7f96b8',
      nodeBorder: '#7f96b8',
      secondaryColor: '#fff8ec',
      tertiaryColor: '#eef7f2',
      lineColor: '#5b6b82',
      textColor: ink,
      primaryTextColor: ink,
      fontSize: '17px',
    },
    flowchart: {
      ...base.flowchart,
      // Drawn at its own size: with `useMaxWidth` the SVG says `width="100%"`
      // and the rasteriser has no size to draw at.
      useMaxWidth: false,
      curve: 'basis' as const,
      padding: 16,
      rankSpacing: 44,
      nodeSpacing: 40,
      subGraphTitleMargin: { top: 10, bottom: 6 },
    },
    // Read by `paperStyles` below, not by mermaid.
    hot: accent,
  }
}

/**
 * The frames a document's own figures are drawn into. Width is fixed (the minimum a figure
 * gets), the shape may vary between `minAspect` and `maxAspect`: a flat drawing is padded up to
 * the flattest shape, a tall one is scaled down to fit the tallest. A slide's frame is the body
 * box under its title; a page's is between 4:3 and 16:9.
 */
export interface Frame {
  width: number
  minAspect: number
  maxAspect: number
}
export const FRAMES: Record<'slide' | 'page', Frame> = {
  slide: { width: 1920, minAspect: 2.2, maxAspect: 2.8 },
  page: { width: 1400, minAspect: 4 / 3, maxAspect: 16 / 9 },
}

/** A drawing is not enlarged beyond this to fill its frame: a three-node figure stays a figure. */
const MAX_UPSCALE = 1.6

/** Beyond this misfit the drawing is tried the other way round. */
const TURN_AT = 1.5

/** Natural size of a drawn SVG, from its viewBox (mermaid always writes one). */
export function measure(svg: string): { width: number; height: number } | null {
  const tag = /<svg\b[^>]*>/.exec(svg)?.[0] ?? ''
  const box = /viewBox="[\d.-]+[ ,][\d.-]+[ ,]([\d.]+)[ ,]([\d.]+)"/.exec(tag)
  if (box) return { width: Number(box[1]), height: Number(box[2]) }
  const w = /\swidth="([\d.]+)(?:px)?"/.exec(tag)
  const h = /\sheight="([\d.]+)(?:px)?"/.exec(tag)
  return w && h ? { width: Number(w[1]), height: Number(h[1]) } : null
}

/** How far a picture's shape is outside the frame's range, as a factor (1 = inside). */
function misfit(size: { width: number; height: number }, frame: Frame): number {
  const ratio = size.width / Math.max(1, size.height)
  if (ratio > frame.maxAspect) return ratio / frame.maxAspect
  if (ratio < frame.minAspect) return frame.minAspect / ratio
  return 1
}

/** The source with its top-level direction turned (LR↔TB), or null when it has none to turn
 *  or sets directions inside subgraphs (turning those would undo the writer's layout). */
export function flipped(source: string): string | null {
  if (/^\s*direction\s/m.test(source)) return null
  const head = /^(\s*(?:flowchart|graph)\s+)(LR|RL|TB|TD|BT)\b/m.exec(source)
  if (!head) return null
  const turned = head[2] === 'LR' || head[2] === 'RL' ? 'TB' : 'LR'
  return source.replace(head[0], `${head[1]}${turned}`)
}

/**
 * `draw`, keeping the shape inside the frame's range: a picture far outside it is drawn again
 * with its direction turned, and the closer of the two is kept.
 */
export async function drawFitting(source: string, look: object, frame: Frame): Promise<string | null> {
  const first = await draw(source, look)
  if (!first) return null
  const size = measure(first)
  if (!size || misfit(size, frame) < TURN_AT) return first
  const other = flipped(source)
  if (!other) return first
  const second = await draw(other, look)
  const again = second ? measure(second) : null
  return second && again && misfit(again, frame) < misfit(size, frame) ? second : first
}

/** Where a drawing of `size` sits inside its frame: full width when it can be, centred, scaled
 *  up at most `MAX_UPSCALE`; the frame's height follows the drawing within the aspect range. */
function placement(size: { width: number; height: number }, frame: Frame) {
  const W = frame.width
  const pad = Math.round(W * 0.03)
  let scale = Math.min((W - 2 * pad) / size.width, MAX_UPSCALE)
  const H = Math.round(
    Math.min(Math.max(size.height * scale + 2 * pad, W / frame.maxAspect), W / frame.minAspect),
  )
  scale = Math.min(scale, (H - 2 * pad) / size.height)
  const dw = size.width * scale
  const dh = size.height * scale
  return { W, H, dw, dh, x: (W - dw) / 2, y: (H - dh) / 2 }
}

/**
 * The picture inside a white frame, as one SVG string for the rasteriser. The drawing keeps its
 * own root (mermaid's styles select by its id) and becomes a nested `<svg>` placed by `placement`.
 */
export function framed(svg: string, frame: Frame): string {
  const open = /<svg\b[^>]*>/.exec(svg)
  const size = measure(svg)
  if (!open || !size) return svg
  const at = placement(size, frame)
  const attrs = open[0]
    .slice(4, -1)
    .replace(/\s(?:width|height|style|x|y)="[^"]*"/g, '')
  const inner =
    `<svg${attrs} x="${at.x.toFixed(1)}" y="${at.y.toFixed(1)}" width="${at.dw.toFixed(1)}" ` +
    `height="${at.dh.toFixed(1)}" preserveAspectRatio="xMidYMid meet">` +
    svg.slice(open.index + open[0].length)
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${at.W}" height="${at.H}" viewBox="0 0 ${at.W} ${at.H}">` +
    `<rect width="${at.W}" height="${at.H}" fill="#ffffff"/>${inner}</svg>`
  )
}

/** `framed`, on a drawn element: the frame is built with DOM calls and the drawing moves inside it. */
export function frameElement(drawn: SVGSVGElement, frame: Frame): SVGSVGElement {
  const NS = 'http://www.w3.org/2000/svg'
  const box = drawn.viewBox?.baseVal
  const size = {
    width: box?.width || Number(drawn.getAttribute('width')) || 800,
    height: box?.height || Number(drawn.getAttribute('height')) || 400,
  }
  const at = placement(size, frame)
  const outer = document.createElementNS(NS, 'svg')
  outer.setAttribute('viewBox', `0 0 ${at.W} ${at.H}`)
  outer.setAttribute('width', String(at.W))
  outer.setAttribute('height', String(at.H))
  const ground = document.createElementNS(NS, 'rect')
  ground.setAttribute('width', String(at.W))
  ground.setAttribute('height', String(at.H))
  ground.setAttribute('fill', '#ffffff')
  outer.append(ground)
  drawn.removeAttribute('style')
  drawn.setAttribute('x', at.x.toFixed(1))
  drawn.setAttribute('y', at.y.toFixed(1))
  drawn.setAttribute('width', at.dw.toFixed(1))
  drawn.setAttribute('height', at.dh.toFixed(1))
  drawn.setAttribute('preserveAspectRatio', 'xMidYMid meet')
  outer.append(drawn)
  return outer
}

/**
 * `drawInto`, keeping the shape inside the frame's range like `drawFitting`: both candidates are drawn
 * off-screen and the closer one moves into `node`.
 */
export async function drawIntoFitting(
  node: HTMLElement,
  source: string,
  look: object,
  frame: Frame,
): Promise<SVGSVGElement | null> {
  const scratch = document.createElement('div')
  scratch.style.cssText = 'position:absolute;left:-99999px;top:0;width:1200px'
  document.body.appendChild(scratch)
  try {
    const first = await drawInto(scratch, source, look)
    if (!first) return null
    const shape = (el: SVGSVGElement) => {
      const b = el.viewBox?.baseVal
      return { width: b?.width || 800, height: b?.height || 400 }
    }
    let chosen = first
    const other = flipped(source)
    if (misfit(shape(first), frame) >= TURN_AT && other) {
      const second = document.createElement('div')
      second.style.cssText = scratch.style.cssText
      document.body.appendChild(second)
      try {
        const turned = await drawInto(second, other, look)
        if (turned && misfit(shape(turned), frame) < misfit(shape(first), frame)) chosen = turned
      } finally {
        if (chosen !== first) first.remove()
        second.remove()
      }
    }
    node.replaceChildren(chosen)
    return chosen
  } finally {
    scratch.remove()
  }
}

/**
 * The paper look with the deck's own colours, for a figure drawn beside a slide's words.
 * Larger type than the page: the picture is scaled to a box a third of the slide wide.
 */
export function slideTheme(colours: { accent: string; ink: string; muted: string; font?: string }) {
  return {
    theme: 'base' as const,
    // `<text>`, not `<foreignObject>`: foreign content taints the canvas `rasterise` draws to.
    htmlLabels: false,
    fontFamily: colours.font || "'Pretendard', 'Inter', 'Helvetica Neue', Arial, sans-serif",
    themeVariables: {
      background: '#ffffff',
      clusterBkg: '#eef4fb',
      clusterBorder: '#b7c7de',
      mainBkg: '#f7f9fc',
      primaryColor: '#f7f9fc',
      primaryBorderColor: '#7f96b8',
      primaryTextColor: colours.ink,
      nodeBorder: '#7f96b8',
      secondaryColor: '#fff8ec',
      tertiaryColor: '#eef7f2',
      lineColor: '#5b6b82',
      textColor: colours.ink,
      edgeLabelBackground: '#ffffff',
      fontSize: '26px',
    },
    flowchart: {
      // Drawn at its own size so the rasteriser has a size to draw at; the slide scales it.
      useMaxWidth: false,
      htmlLabels: false,
      curve: 'basis' as const,
      padding: 14,
      rankSpacing: 40,
      nodeSpacing: 34,
      subGraphTitleMargin: { top: 12, bottom: 8 },
    },
    hot: colours.accent,
  }
}

/** Highlight and stroke rules injected into the SVG after drawing; `:::hot` needs a class rule. */
export function paperStyles(hot: string): string {
  // `!important`: mermaid's own rules use `#<svg id>` selectors, which outrank any class.
  return [
    `.node.hot rect, .node.hot polygon, .node.hot path, .node.hot circle { fill: ${hot}22 !important; stroke: ${hot} !important; stroke-width: 2px !important; }`,
    `.node rect, .node polygon, .node path, .node circle { stroke-width: 1.4px; }`,
    `.cluster rect { rx: 10px; ry: 10px; stroke-dasharray: 4 3; }`,
    `.edgePath path { stroke-width: 1.5px; }`,
    `.edgeLabel { font-size: 14px; }`,
  ].join('\n')
}
