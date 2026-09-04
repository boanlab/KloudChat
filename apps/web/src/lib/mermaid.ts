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
export async function rasterise(svg: string): Promise<string | null> {
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
      const scale = 2
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
  const face = read('--font-body', 'inherit')

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
    },
    // Read by `paperStyles` below, not by mermaid.
    hot: accent,
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
