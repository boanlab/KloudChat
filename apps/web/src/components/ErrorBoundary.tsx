import { RefreshCw, TriangleAlert } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@/components/ui'
import { currentLang, translate } from '@/lib/i18n'

/** A class component cannot use hooks, so the current language is read
 *  directly. */
const tr = (text: string) => translate(currentLang(), text)

/**
 * The last line of defence against a white screen: an uncaught render exception
 * unmounts the whole tree. This catches it, records it, and offers a way out.
 */
interface State {
  error: Error | null
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Also to the console: the on-screen copy is short, and the component
    // stack is what locates the fault.
    console.error('[KloudChat] render failed', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children

    return (
      <div className="grid h-full place-items-center bg-bg p-6 text-fg">
        <div className="w-full max-w-md text-center">
          <div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-danger/10 text-danger">
            <TriangleAlert size={22} />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">{tr('화면을 표시하지 못했습니다')}</h1>
          <p className="mt-2 text-[13px] leading-relaxed text-muted">
            {tr('대화 내용은 서버에 그대로 저장되어 있습니다. 아래에서 다시 시도하거나 다른 화면으로 이동하세요.')}
          </p>
          <pre className="mt-4 max-h-40 overflow-auto rounded-lg border border-line bg-elevated px-3 py-2 text-left font-mono text-[11px] break-words whitespace-pre-wrap text-muted">
            {error.message}
          </pre>
          <div className="mt-5 flex justify-center gap-2">
            <Button variant="primary" onClick={() => this.setState({ error: null })}>
              <RefreshCw size={15} />
              {tr('다시 시도')}
            </Button>
            <Button
              onClick={() => {
                // A full load, not a route change: the state that threw is
                // still in memory.
                window.location.href = '/'
              }}
            >
              {tr('홈으로')}
            </Button>
          </div>
        </div>
      </div>
    )
  }
}
