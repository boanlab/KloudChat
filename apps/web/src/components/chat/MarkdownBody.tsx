import { Check, Copy } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
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

// Embedded raster pictures only; the same rule as `services/pictures.py`.
const EMBEDDED_PICTURE =
  /^data:image\/(?:png|jpeg|jpg|gif|webp);base64,[A-Za-z0-9+/=\s]+$/i

function CodeBlock({ children, className }: { children: ReactNode; className?: string }) {
  const t = useT()
  const [copied, setCopied] = useState(false)
  const lang = /language-(\w+)/.exec(className ?? '')?.[1]
  const text = String(children).replace(/\n$/, '')

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
        <code className="font-mono">{text}</code>
      </pre>
    </div>
  )
}

/** Rewrites `\\[…\\]` / `\\(…\\)` to `$$…$$` / `$…$` for remark-math; code spans are left alone. */
function normaliseMath(text: string): string {
  return text
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
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
  const source = useMemo(() => normaliseMath(children), [children])
  return (
    <div className={cn('text-md leading-[1.7] break-words', className)}>
      <ReactMarkdown
        // remark-cjk-friendly: CommonMark will not close `**` before a Korean particle.
        remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        // react-markdown blanks `data:` URLs by default; embedded rasters pass.
        urlTransform={(url, key, node) =>
          key === 'src' && node.tagName === 'img' && EMBEDDED_PICTURE.test(url)
            ? url
            : defaultUrlTransform(url)
        }
        components={{
          p: ({ children }) => <p className="my-2.5 first:mt-0 last:mb-0">{children}</p>,
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
          li: ({ children }) => <li className="pl-0.5">{children}</li>,
          strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noreferrer"
              className="text-accent underline underline-offset-2"
            >
              {children}
            </a>
          ),
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
            const isBlock = /language-/.test(className ?? '')
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
            <span className="my-3 block">
              <img
                src={typeof src === 'string' ? src : undefined}
                alt={alt ?? ''}
                className="block h-auto max-w-full rounded-card border border-line"
              />
              {alt ? <span className="mt-1.5 block text-base text-muted">{alt}</span> : null}
            </span>
          ),
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
