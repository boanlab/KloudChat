import { Check, Copy, X } from 'lucide-react'
import { detachTables } from '@/lib/markdownTables'
import { escapeCurrency } from '@/lib/mathDollars'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import 'katex/dist/katex.min.css'
import { Diagram } from '@/components/report/Diagram'
import { CardGrid, Callout } from '@/components/report/CardGrid'
import { ChartBlock } from '@/components/report/ChartBlock'
import { KpiStrip } from '@/components/report/KpiStrip'
import { StepList } from '@/components/report/StepList'
import { diagramKey } from '@/lib/diagramKey'
import { cn } from '@/lib/utils'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'
import { rehypePlaceholders } from '@/lib/placeholders'

// Embedded raster pictures only; the same rule as `services/pictures.py`.
const EMBEDDED_PICTURE =
  /^data:image\/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$/i

// `![alt](data:…)`, closed or cut off at the line end.
const WRITTEN_PICTURE = /!\[([^\]\n]*)\]\((data:[^)\n]*)\)?/g

/** A written-out `data:` picture that is not an embedded raster becomes its alt in italics, never base64 text. */
function withoutBrokenPictures(text: string): string {
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(WRITTEN_PICTURE, (whole, alt: string, url: string) =>
            whole.endsWith(')') && EMBEDDED_PICTURE.test(url) ? whole : alt.trim() ? `*${alt.trim()}*` : '',
          ),
    )
    .join('')
}

/** An inline picture that opens full-size on click; Escape or a click outside closes it. */
function ZoomableImage({ src, alt }: { src: string | undefined; alt: string }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [broken, setBroken] = useState(false)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])
  // undecodable picture: its alt only
  if (broken || !src) return alt ? <em className="text-muted">{alt}</em> : null
  return (
    <span className="my-3 block">
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label={t('그림 크게 보기')}
        className="block max-w-full cursor-zoom-in rounded-card border border-line"
      >
        <img src={src} alt={alt} onError={() => setBroken(true)} className="block h-auto max-w-full rounded-card" />
      </button>
      {alt ? <span className="mt-1.5 block text-center text-base text-muted">{alt}</span> : null}
      {open && (
        <span
          role="dialog"
          aria-modal="true"
          aria-label={alt || t('그림')}
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-50 flex cursor-zoom-out items-center justify-center bg-black/80 p-4"
        >
          <img src={src} alt={alt} className="max-h-full max-w-full rounded-card object-contain" />
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label={t('닫기')}
            className="absolute top-3 right-3 grid size-9 place-items-center rounded-full bg-black/60 text-white hover:bg-black/80"
          >
            <X size={18} />
          </button>
        </span>
      )}
    </span>
  )
}

type Highlighter = typeof import('highlight.js/lib/core').default
let highlighterPromise: Promise<Highlighter> | null = null

/** highlight.js core plus the languages a chat answer is likely to carry, loaded on first use. */
function loadHighlighter(): Promise<Highlighter> {
  highlighterPromise ??= Promise.all([
    import('highlight.js/lib/core'),
    import('highlight.js/lib/languages/python'),
    import('highlight.js/lib/languages/javascript'),
    import('highlight.js/lib/languages/typescript'),
    import('highlight.js/lib/languages/bash'),
    import('highlight.js/lib/languages/json'),
    import('highlight.js/lib/languages/yaml'),
    import('highlight.js/lib/languages/sql'),
    import('highlight.js/lib/languages/xml'),
    import('highlight.js/lib/languages/css'),
    import('highlight.js/lib/languages/java'),
    import('highlight.js/lib/languages/c'),
    import('highlight.js/lib/languages/cpp'),
    import('highlight.js/lib/languages/csharp'),
    import('highlight.js/lib/languages/go'),
    import('highlight.js/lib/languages/rust'),
    import('highlight.js/lib/languages/kotlin'),
    import('highlight.js/lib/languages/swift'),
    import('highlight.js/lib/languages/php'),
    import('highlight.js/lib/languages/ruby'),
    import('highlight.js/lib/languages/r'),
    import('highlight.js/lib/languages/diff'),
    import('highlight.js/lib/languages/dockerfile'),
    import('highlight.js/lib/languages/markdown'),
  ])
    .then(([core, ...languages]) => {
    const hljs = core.default
    const names = [
      'python', 'javascript', 'typescript', 'bash', 'json', 'yaml', 'sql', 'xml', 'css',
      'java', 'c', 'cpp', 'csharp', 'go', 'rust', 'kotlin', 'swift', 'php', 'ruby', 'r',
      'diff', 'dockerfile', 'markdown',
    ]
    names.forEach((name, i) => hljs.registerLanguage(name, languages[i].default))
    hljs.registerAliases(['js', 'jsx', 'mjs'], { languageName: 'javascript' })
    hljs.registerAliases(['ts', 'tsx'], { languageName: 'typescript' })
    hljs.registerAliases(['sh', 'shell', 'zsh', 'console'], { languageName: 'bash' })
    hljs.registerAliases(['html', 'svg'], { languageName: 'xml' })
    hljs.registerAliases(['yml'], { languageName: 'yaml' })
    hljs.registerAliases(['py'], { languageName: 'python' })
    hljs.registerAliases(['rs'], { languageName: 'rust' })
    hljs.registerAliases(['cs'], { languageName: 'csharp' })
    hljs.registerAliases(['md'], { languageName: 'markdown' })
    hljs.registerAliases(['docker'], { languageName: 'dockerfile' })
    return hljs
    })
    .catch((error: unknown) => {
      // A chunk that failed to load (a deploy mid-session) is tried again next time.
      highlighterPromise = null
      throw error
    })
  return highlighterPromise
}

// Highlight once the text stops changing; re-tokenising a streaming block every frame is waste.
const SETTLE_MS = 300

// Above this, highlighting is skipped: a pasted log is not worth the parse.
const HIGHLIGHT_LIMIT = 20_000

/** Highlighted HTML for `text`, or null until the highlighter is in and the language known. */
function useHighlighted(text: string, lang: string | undefined): string | null {
  // Keyed by source: changed text shows plain until its own highlight arrives.
  const key = `${lang ?? ''}\u0000${text}`
  const [done, setDone] = useState<{ key: string; html: string } | null>(null)
  useEffect(() => {
    let current = true
    if (!lang || lang === 'text' || lang === 'plaintext' || text.length > HIGHLIGHT_LIMIT) return
    const timer = window.setTimeout(() => {
      void loadHighlighter()
        .then((hljs) => {
          if (!current || !hljs.getLanguage(lang)) return
          setDone({ key, html: hljs.highlight(text, { language: lang, ignoreIllegals: true }).value })
        })
        .catch(() => {})
    }, SETTLE_MS)
    return () => {
      current = false
      window.clearTimeout(timer)
    }
  }, [key, text, lang])
  return done?.key === key ? done.html : null
}

function CodeBlock({ children, className }: { children: ReactNode; className?: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const lang = /language-(\w+)/.exec(className ?? '')?.[1]
  const text = String(children).replace(/\n$/, '')
  const highlighted = useHighlighted(text, lang)

  return (
    <div className="group relative my-3 overflow-hidden rounded-card border border-line bg-elevated">
      <div className="flex items-center justify-between border-b border-line px-3 py-1.5">
        <span className="font-mono text-xs text-faint">{lang ?? 'text'}</span>
        <button
          onClick={async () => {
            if (!(await copyText(text))) return
            setCopied(true)
            setTimeout(() => setCopied(false), 1400)
          }}
          className="flex items-center gap-1 rounded-control px-1.5 py-0.5 text-xs text-muted transition-colors hover:bg-line hover:text-fg"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? t('복사됨') : t('복사')}
        </button>
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 text-base leading-relaxed">
        {highlighted ? (
          // highlight.js escapes the source; only its own span markup is added.
          <code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: highlighted }} />
        ) : (
          <code className="font-mono">{text}</code>
        )}
      </pre>
    </div>
  )
}

/** Rewrites `\\[…\\]` / `\\(…\\)` to `$$…$$` / `$…$` for remark-math, a price's `$` escaped; code spans are left alone. */
function normaliseMath(text: string): string {
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : escapeCurrency(part)
            .replace(/\\\[([\s\S]*?)\\\]/g, (_, body) => `\n$$${body}$$\n`)
            .replace(/\\\(([\s\S]*?)\\\)/g, (_, body) => `$${body}$`),
    )
    .join('')
}

/** Document a diagram belongs to, so its rendered picture can be stored; absent in chat and while streaming. */
export interface DiagramOwner {
  artifactId: string
  sectionId: string
  /** Pictures already on this section, by diagram key. */
  stored?: Record<string, string>
}

/** Computes the storage key, then hands the diagram over. */
function DiagramBlock({ source, owner }: { source: string; owner?: DiagramOwner }) {
  const [key, setKey] = useState<string | undefined>()
  useEffect(() => {
    let live = true
    void diagramKey(source).then((k) => live && setKey(k))
    return () => {
      live = false
    }
  }, [source])
  return (
    <Diagram
      source={source}
      artifactId={owner?.artifactId}
      sectionId={owner?.sectionId}
      diagramKey={key}
      stored={owner && key ? owner.stored?.[key] : undefined}
    />
  )
}

export function MarkdownBody({
  children,
  className,
  owner,
}: {
  children: string
  className?: string
  owner?: DiagramOwner
}) {
  const source = useMemo(() => detachTables(normaliseMath(withoutBrokenPictures(children))), [children])
  return (
    <div
      className={cn(
        'text-md leading-[1.7] break-words phone:text-[1rem] phone:leading-[1.7]',
        className,
      )}
    >
      <ReactMarkdown
        // remark-cjk-friendly: CommonMark won't close `**` before a Korean particle. A single tilde is a range (「100~500 Hz」); only `~~text~~` strikes.
        remarkPlugins={[[remarkGfm, { singleTilde: false }], remarkCjkFriendly, remarkMath]}
        // A bracket placeholder (「[담당자]」) is marked as a gap to fill.
        rehypePlugins={[rehypeKatex, rehypePlaceholders]}
        // react-markdown blanks `data:` URLs by default; embedded rasters pass.
        urlTransform={(url, key, node) =>
          key === 'src' && node.tagName === 'img' && EMBEDDED_PICTURE.test(url)
            ? url
            : defaultUrlTransform(url)
        }
        components={{
          // A ※ note and a 붙임 line are marked for the 개조식 rules (`GAEJO_CSS`); unstyled elsewhere.
          p: ({ children }) => {
            const lead = Array.isArray(children) ? children[0] : children
            const text = typeof lead === 'string' ? lead : ''
            return (
              <p
                className="my-2.5 first:mt-0 last:mb-0"
                data-note={text.startsWith('※') ? '' : undefined}
                data-attach={text.startsWith('붙임') ? '' : undefined}
              >
                {children}
              </p>
            )
          },
          h1: ({ children }) => <h1 className="mt-5 mb-2 text-xl font-semibold">{children}</h1>,
          h2: ({ children }) => <h2 className="mt-5 mb-2 text-lg font-semibold">{children}</h2>,
          h3: ({ children }) => (
            <h3 className="mt-4 mb-1.5 text-md font-semibold">{children}</h3>
          ),
          ul: ({ children }) => (
            <ul className="my-2.5 list-disc space-y-1 pl-5 marker:text-faint">{children}</ul>
          ),
          // `start` is forwarded to match the exporters.
          ol: ({ children, start }) => (
            <ol start={start} className="my-2.5 list-decimal space-y-1 pl-5 marker:text-faint">
              {children}
            </ol>
          ),
          // A ※ item is a note at its depth (`GAEJO_CSS`); unstyled elsewhere.
          li: ({ children }) => {
            const lead = Array.isArray(children) ? children[0] : children
            const text = typeof lead === 'string' ? lead : ''
            return <li className="pl-0.5" data-note={text.startsWith('※') ? '' : undefined}>{children}</li>
          },
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          a: ({ children, href }) => {
            // A source citation the server linked: `[[3]](url)` renders as a small badge.
            const citation =
              Array.isArray(children) && children.length === 1
                ? String(children[0])
                : typeof children === 'string'
                  ? children
                  : ''
            if (/^\[\d{1,2}\]$/.test(citation)) {
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  title={href}
                  className="mx-0.5 inline-block rounded border border-line-strong px-1 align-baseline text-[0.72em] leading-snug text-accent no-underline hover:bg-elevated"
                >
                  {citation.slice(1, -1)}
                </a>
              )
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="text-accent underline underline-offset-2"
              >
                {children}
              </a>
            )
          },
          blockquote: ({ children }) => (
            <blockquote className="my-3 border-l-2 border-line-strong pl-3 text-muted">
              {children}
            </blockquote>
          ),
          hr: () => <hr className="my-4 border-line" />,
          // `min-w` makes the wrapper scroll instead of squeezing columns to one glyph per line.
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-card border border-line">
              <table className="w-full min-w-[30rem] border-collapse text-base">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-elevated">{children}</thead>,
          th: ({ children }) => (
            <th className="border-b border-line px-3 py-2 text-left font-semibold break-keep">{children}</th>
          ),
          // The wrapper draws the outer border, so the last row has none.
          td: ({ children }) => (
            <td className="border-b border-line px-3 py-2 align-top break-keep [tr:last-child>&]:border-0">
              {children}
            </td>
          ),
          code: ({ className, children, ...props }) => {
            // A fenced block's text ends in a newline even without a language; inline code never does.
            const isBlock = /language-/.test(className ?? '') || String(children).endsWith('\n')
            // Block fences the exporters also read: chart, steps, kpi, cards, callout, mermaid.
            if (/language-chart/.test(className ?? '')) {
              return <ChartBlock source={String(children).trimEnd()} owner={owner} />
            }
            if (/language-steps/.test(className ?? '')) {
              return <StepList source={String(children).trimEnd()} />
            }
            if (/language-kpi/.test(className ?? '')) {
              return <KpiStrip source={String(children).trimEnd()} />
            }
            if (/language-cards/.test(className ?? '')) {
              return <CardGrid source={String(children).trimEnd()} />
            }
            if (/language-callout/.test(className ?? '')) {
              return <Callout source={String(children).trimEnd()} />
            }
            if (/language-mermaid/.test(className ?? '')) {
              return <DiagramBlock source={String(children).trimEnd()} owner={owner} />
            }
            if (isBlock) return <CodeBlock className={className}>{children}</CodeBlock>
            return (
              <code
                className="rounded-control border border-line bg-elevated px-1 py-0.5 font-mono text-[0.86em]"
                {...props}
              >
                {children}
              </code>
            )
          },
          // Alt text doubles as the caption. Spans, not <figure>: a <figure> inside a <p> is closed early.
          img: ({ src, alt }) => (
            <ZoomableImage src={typeof src === 'string' ? src : undefined} alt={alt ?? ''} />
          ),
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
