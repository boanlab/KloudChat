import { Check, Copy } from 'lucide-react'
import { useMemo, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkCjkFriendly from 'remark-cjk-friendly'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import 'katex/dist/katex.min.css'
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

export function MarkdownBody({ children, className }: { children: string; className?: string }) {
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
          td: ({ children }) => (
            <td className="border-b border-line px-3 py-2 align-top last:border-0">{children}</td>
          ),
          code: ({ className, children, ...props }) => {
            const isBlock = /language-/.test(className ?? '')
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
