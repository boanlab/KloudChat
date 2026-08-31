/**
 * Drawing a mermaid diagram, and keeping what was drawn.
 *
 * Its own module because two screens need it and they need it for opposite
 * reasons. The web view draws a diagram *to show it*, and stores the picture
 * on the way past. The page view cannot show an SVG at all — its document is a
 * Tiptap document, and a subtree ProseMirror believes it owns is not a place
 * to render into — so it draws off-screen purely to *get* the picture, and
 * shows that instead.
 *
 * One implementation, or the two surfaces disagree about what a diagram looks
 * like and the digest a picture is stored under stops matching the digest it
 * is looked up by.
 */

/**
 * The diagram as an SVG string, or `null` if mermaid would not draw it.
 *
 * `look` comes from the caller because the two surfaces have different ones:
 * in the page view the tokens are the 서식's, read off an element inside its
 * shadow root; in the web view they are the app's.
 */
export async function draw(source: string, look: object): Promise<string | null> {
  try {
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({
      startOnLoad: false,
      // `securityLevel: strict` is the default and is what keeps a diagram
      // from being a script — the source comes from a model and passes through
      // no sanitiser of ours.
      securityLevel: 'strict',
      ...look,
    })
    const { svg } = await mermaid.render(`d${Math.abs(hash(source))}`, plain(source))
    return svg
  } catch {
    return null
  }
}

/**
 * The diagram without its own colours.
 *
 * `style`, `classDef` and `linkStyle` are the three ways a mermaid source can
 * set its own fills, and a model reaches for them constantly — which is where
 * the pink boxes and the yellow database come from. They win over the theme,
 * so one figure in a report comes out in a palette nothing else on the page
 * uses, and it reads as pasted in from another program.
 *
 * Stripped here rather than forbidden in the prompt alone. The prompt already
 * says not to, and a rule the writer breaks silently is a rule the reader pays
 * for. Same principle as the HTML sanitiser dropping `style=`: the 서식 owns
 * every colour in the document, and the only way to mean that is to enforce it.
 *
 * The declarations are removed, not the nodes — a `style A fill:#f9f` line is
 * dropped and the node `A` stays exactly where it was.
 */
function plain(source: string): string {
  return source
    .split('\n')
    .filter((line) => !/^\s*(style|linkStyle|classDef)\s/.test(line))
    .join('\n')
}

/** Stable enough to name one render. Not the storage key — that is the server's. */
function hash(text: string): number {
  let value = 0
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) | 0
  return value
}

/**
 * The SVG as a PNG `data:` URI, or `null`.
 *
 * A raster rather than the SVG itself because that is what the three exporters
 * can place: `python-docx` and reportlab both want pixels, and neither reads
 * SVG. Drawn at twice the size so the figure is not soft on paper.
 */
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
      // White, not transparent: a diagram on a transparent ground turns into a
      // black rectangle in a `.docx` viewer with a dark theme.
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
 * The diagram in the document's own palette and face.
 *
 * Mermaid's default theme is a set of pastels with a hand-drawn feel, sized to
 * whatever the renderer picks. Dropped into a report it reads as something
 * pasted in from another program — which is exactly what a reader concludes.
 * A figure in a submitted document has to look like it belongs to the document
 * around it.
 *
 * The values are read off the element it will be drawn into rather than
 * configured, so one function serves both surfaces: in the page view that
 * element is inside the template's shadow root and the tokens are the 서식's;
 * in the web view they are the app's. Neither has to know about the other.
 *
 * `fontSize` is raised above what looks right on screen on purpose. Mermaid
 * fits its SVG to the column, so a diagram wider than the text column is
 * scaled down — and everything set at reading size lands below it.
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
    // `<text>`, not `<foreignObject>` — the single most load-bearing line here.
    //
    // With HTML labels mermaid puts each node's words in a `foreignObject`,
    // and a canvas that has been handed an SVG containing foreign content is
    // *tainted*: the drawing succeeds and `toDataURL` then throws. So
    // `rasterise` returned null, no picture was ever stored, and the failure
    // was invisible from both ends — the web view had drawn the diagram and
    // showed it, while the page view and the three exporters, which have only
    // the stored picture to go on, showed a placeholder promising it would
    // appear as soon as somebody opened the web view. Somebody had.
    //
    // It survived the tests because the one that covered this path drew a
    // `pie`, and pie labels are plain `<text>`. Only flowcharts were lost.
    //
    // Set here at the top level rather than under `flowchart`, which is where
    // it reads naturally: mermaid resolves `htmlLabels ?? flowchart.htmlLabels`
    // and warns that the second is deprecated.
    htmlLabels: false,
    themeVariables: {
      // Flat, and the document's ink. No fills that compete with the prose.
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
      // Charts. Mermaid reads pie slices off numbered variables and the xy
      // plot off a comma-joined list, so both are written from one palette.
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
      // Fitted to the text column. A figure that needs a horizontal scrollbar
      // is a figure nobody sees the right-hand half of, and one that runs past
      // the margin is one the printer cuts.
      useMaxWidth: true,
      curve: 'linear' as const,
      padding: 12,
      // Flat rather than tall. `useMaxWidth` scales a diagram to the column,
      // so a portrait one is scaled *down* to fit its width and then runs half
      // a page down — the shape a reader gets is set by the drawing, not by
      // the fitting. Tightening the gap between ranks and widening the gap
      // between siblings pushes the same graph outward instead of downward.
      rankSpacing: 30,
      nodeSpacing: 45,
      // Wide enough that a label the prompt allows — ten Korean characters —
      // stays on one line. Wrapping was tried at 140 on the reasoning that
      // narrower nodes let siblings sit side by side, and it does the
      // opposite: a label broken over two lines makes its node *taller*, every
      // node on that rank matches the tallest, and the diagram grows down the
      // page. A broken word in a box also just reads as a mistake.
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

/**
 * The palette a chart is drawn in, from the one colour a 서식 declares.
 *
 * A flow chart needs no fills — its nodes are outlined and its text is ink.
 * A pie chart is nothing but fills, and drawn in the palette above every slice
 * came out `--paper` on `--paper`: a white circle. So charts need a series of
 * colours, and a report is allowed exactly one accent.
 *
 * They are mixed from that accent toward the paper, so a document keeps a
 * single hue and the slices stay told apart by weight. Twelve steps would be
 * indistinguishable; six is already past what a reader can hold, which is why
 * the prompt caps a pie at six slices.
 */
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
    // Never all the way to the paper: the last slice has to stay visible
    // against it, and a 70% mix still reads as a colour.
    const mix = count === 1 ? 0 : (i / (count - 1)) * 0.7
    return `#${hex(ar + (pr - ar) * mix)}${hex(ag + (pg - ag) * mix)}${hex(ab + (pb - ab) * mix)}`
  })
}
