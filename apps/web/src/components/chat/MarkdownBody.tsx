import { Check, Copy } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import 'katex/dist/katex.min.css'
import { Diagram } from '@/components/report/Diagram'
import { ChartBlock } from '@/components/report/ChartBlock'
import { KpiStrip } from '@/components/report/KpiStrip'
import { StepList } from '@/components/report/StepList'
import { diagramKey } from '@/lib/diagramKey'
import { cn } from '@/lib/utils'
import { copyText } from '@/lib/clipboard'
import { useT } from '@/lib/useT'

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

/**
 * `\\[…\\]` and `\\(…\\)` → `$$…$$` and `$…$`.
 *
 * `remark-math` understands only the dollar notation, and models frequently
 * write the LaTeX one. Which delimiter the model happened to choose decides
 * whether the maths renders or shows up as backslashes.
 *
 * Code fences are left alone: a block showing LaTeX source is exactly what
 * this rewrite would ruin.
 */
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

/**
 * Which document a diagram belongs to, so the picture it draws can be kept.
 *
 * Absent in the chat transcript and while a document is still streaming: there
 * the diagram is drawn and not stored, because there is nothing to store it
 * onto yet.
 */
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
  // The regex pass runs once per text, not once per render of a memoised row.
  const source = useMemo(() => normaliseMath(children), [children])
  return (
    <div className={cn('text-md leading-[1.7] break-words', className)}>
      <ReactMarkdown
      // CommonMark will not close `**` when the closing marker sits between a
      // bracket and a Korean particle — "**지식의 전이(Knowledge Transfer)**이다"
      // renders with the asterisks showing. That is the shape every Korean
      // answer takes, so the relaxed CJK flanking rules are not optional here.
      remarkPlugins={[remarkGfm, remarkCjkFriendly, remarkMath]}
        rehypePlugins={[rehypeKatex]}
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
          // `start` is forwarded: CommonMark takes the first item's number as
          // the list start, and the exporters read the same Markdown. Dropping
          // it would put the preview out of step with the file.
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
          table: ({ children }) => (
            <div className="my-3 overflow-x-auto rounded-card border border-line">
              <table className="w-full border-collapse text-base">{children}</table>
            </div>
          ),
          thead: ({ children }) => <thead className="bg-elevated">{children}</thead>,
          th: ({ children }) => (
            <th className="border-b border-line px-3 py-2 text-left font-semibold">{children}</th>
          ),
          // `last:border-0` was meant to drop the rule under the *last row*,
          // and `:last-child` on a `<td>` is the last **column** — so every
          // cell down the right-hand side lost its bottom border and the table
          // read as two ruled columns beside one unruled one. Scoped to the
          // row it was always about: the wrapper draws the outer border, so a
          // rule under the final row would sit on top of it.
          td: ({ children }) => (
            <td className="border-b border-line px-3 py-2 align-top [tr:last-child>&]:border-0">
              {children}
            </td>
          ),
          code: ({ className, children, ...props }) => {
            const isBlock = /language-/.test(className ?? '')
            // A mermaid fence is a diagram, not source. Drawn rather than
            // printed — and, where the caller says which document it belongs
            // to, kept as a picture so the exported file has it too.
            // A row of figures, set as a strip. Text rather than a picture,
            // so the exporters draw it as a real table.
            // Real numbers, drawn. The exporters read the same fence and
            // build a chart Word can edit, so this is the same chart rather
            // than a picture of one.
            if (/language-chart/.test(className ?? '')) {
              return <ChartBlock source={String(children).trimEnd()} owner={owner} />
            }
            // A procedure, numbered. Text rather than a picture, for the
            // same reason the strip is.
            if (/language-steps/.test(className ?? '')) {
              return <StepList source={String(children).trimEnd()} />
            }
            if (/language-kpi/.test(className ?? '')) {
              return <KpiStrip source={String(children).trimEnd()} />
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
          pre: ({ children }) => <>{children}</>,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
