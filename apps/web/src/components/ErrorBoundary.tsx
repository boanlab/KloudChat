import { RefreshCw, TriangleAlert } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@/components/ui'
import { currentLang, translate } from '@/lib/i18n'

// Class components cannot use hooks, so the language is read directly.
const tr = (text: string) => translate(currentLang(), text)

/** How each browser words a lazy chunk that could not be fetched. */
const STALE_CHUNK = /dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i

interface State {
  error: Error | null
}

/** Catches uncaught render errors and offers retry or reload. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[KloudChat] render failed', error, info.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    // A screen's code that no longer exists on the server (a new version was deployed).
    const stale = STALE_CHUNK.test(error.message)

    return (
      <div className="grid h-full place-items-center bg-bg p-6 text-fg">
        <div className="w-full min-w-0 max-w-md text-center">
          <div className="mx-auto mb-4 grid size-12 place-items-center rounded-panel bg-danger/10 text-danger">
            <TriangleAlert size={22} />
          </div>
          <h1 className="text-lg font-semibold tracking-tight">
            {stale ? tr('새 버전으로 다시 불러와야 합니다') : tr('화면을 표시하지 못했습니다')}
          </h1>
          <p className="mt-2 text-base leading-relaxed text-muted">
            {stale
              ? tr('그사이 KloudChat이 새로 배포되었습니다. 새로고침하면 이어서 쓸 수 있고, 대화 내용은 서버에 그대로 있습니다.')
              : tr('대화 내용은 서버에 그대로 저장되어 있습니다. 아래에서 다시 시도하거나 다른 화면으로 이동하세요.')}
          </p>
          {!stale && (
            // For a report to the administrator; folded so it is not the first thing read.
            <details className="mt-4 text-left">
              <summary className="cursor-pointer text-sm text-faint">{tr('기술 정보')}</summary>
              <pre className="mt-2 max-h-40 overflow-auto rounded-control border border-line bg-elevated px-3 py-2 font-mono text-xs break-all whitespace-pre-wrap text-muted">
                {error.message}
              </pre>
            </details>
          )}
          <div className="mt-5 flex justify-center gap-2">
            {stale ? (
              <Button variant="primary" onClick={() => window.location.reload()}>
                <RefreshCw size={15} />
                {tr('새로고침')}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => this.setState({ error: null })}>
                <RefreshCw size={15} />
                {tr('다시 시도')}
              </Button>
            )}
            <Button
              onClick={() => {
                // Full reload: the state that threw is still in memory.
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
